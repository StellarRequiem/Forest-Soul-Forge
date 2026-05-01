"""``memory_recall.v1`` — read memory the calling agent can see.

ADR-0022 v0.1 + v0.2, ADR-0027 §1, ADR-0033.

Three modes:

* ``mode='private'`` (default; v0.1 backstop) — owner-only,
  scope='private'. Same-agent self-reads are NOT audited as
  ``memory_read``; the tool dispatcher's normal
  ``tool_call_dispatched`` / ``_succeeded`` events already record
  that this agent read its own memory through this tool.

* ``mode='lineage'`` (v0.2) — own private+lineage rows + lineage
  chain peers' lineage rows. Lineage chain is computed
  automatically from the registry's ``agent_ancestry`` closure
  table (ancestors-of-self ∪ descendants-of-self). Operators can
  override by passing an explicit ``lineage_chain`` arg if they
  want to scope the read tighter than the full chain.

* ``mode='consented'`` (v0.2) — lineage's set + scope='consented'
  rows the reader has an active grant for in ``memory_consents``.

Cross-agent visibility (modes other than 'private') represents a
read across an information-flow boundary; the runtime emits a
``memory_read`` audit event when the tool returns rows owned by an
instance other than the caller.

The tool wires ``ctx.memory`` (a Memory instance) — the daemon
populates it via deps; tests inject directly.
"""
from __future__ import annotations

from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_VALID_LAYERS = ("episodic", "semantic", "procedural")
_VALID_MODES = ("private", "lineage", "consented")


class MemoryRecallTool:
    """Read entries the calling agent can see, scoped by ``mode``.

    Args:
      query  (str, optional): substring match against content + tags
                               + disclosed_summary.
      layer  (str, optional): episodic | semantic | procedural. None
                               returns all layers.
      limit  (int, optional): max entries returned. Default 20, max 200.
      mode   (str, optional): private (default) | lineage | consented.
                               'realm' is reserved for H3 federation
                               and refused with a clear message.
      lineage_chain (list[str], optional): explicit override for the
                               ancestors+descendants set. Only honored
                               when mode != 'private'. When omitted in
                               lineage/consented mode, the tool reads
                               agent_ancestry to compute the chain.
      surface_contradictions (bool, optional): ADR-0027-amendment §7.3.
                               When True, every returned entry includes
                               an ``unresolved_contradictions`` list of
                               open contradictions referencing it.
                               Default False (back-compat). Independent
                               of mode — surfaces contradictions on any
                               entry the reader sees.
      staleness_threshold_days (int, optional): ADR-0027-amendment §7.4.
                               When set, every returned entry includes
                               an ``is_stale`` boolean computed against
                               the threshold (default per claim_type:
                               90 for preference, 30 for agent_inference,
                               unlimited for observation/external_fact).
                               Caller-supplied threshold overrides the
                               per-claim-type defaults uniformly.
                               Defaults to None (no staleness flagging).

    Output:
      {
        "count":   int,
        "mode":    str,
        "entries": [
          {entry_id, instance_id, layer, scope, content, tags,
           created_at, is_disclosed_copy, disclosed_from_entry,
           disclosed_summary,
           # v11 — ADR-0027-amendment §7
           claim_type, confidence, last_challenged_at,
           # optional (per parameter):
           unresolved_contradictions, is_stale}, ...
        ]
      }
    """

    name = "memory_recall"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        layer = args.get("layer")
        if layer is not None and layer not in _VALID_LAYERS:
            raise ToolValidationError(
                f"layer must be one of {list(_VALID_LAYERS)} or omitted; "
                f"got {layer!r}"
            )
        query = args.get("query")
        if query is not None and not isinstance(query, str):
            raise ToolValidationError(
                f"query must be a string when provided; got {type(query).__name__}"
            )
        limit = args.get("limit")
        if limit is not None:
            if not isinstance(limit, int) or limit < 1 or limit > 200:
                raise ToolValidationError(
                    f"limit must be an int 1..200; got {limit!r}"
                )
        mode = args.get("mode")
        if mode is not None and mode not in _VALID_MODES:
            # Special-case 'realm' so the error message points at the
            # right ADR. Anything else gets a generic message.
            if mode == "realm":
                raise ToolValidationError(
                    "mode='realm' is reserved for Horizon 3 federation "
                    "and unreachable today (ADR-0027 §1). Use 'private', "
                    "'lineage', or 'consented'."
                )
            raise ToolValidationError(
                f"mode must be one of {list(_VALID_MODES)} or omitted; "
                f"got {mode!r}"
            )
        chain = args.get("lineage_chain")
        if chain is not None:
            if not isinstance(chain, list) or not all(isinstance(x, str) for x in chain):
                raise ToolValidationError(
                    "lineage_chain must be a list of strings when provided"
                )
        # ADR-0027-amendment §7 — new optional parameters.
        surface_contradictions = args.get("surface_contradictions")
        if surface_contradictions is not None and not isinstance(
            surface_contradictions, bool
        ):
            raise ToolValidationError(
                "surface_contradictions must be a bool when provided; got "
                f"{type(surface_contradictions).__name__}"
            )
        staleness = args.get("staleness_threshold_days")
        if staleness is not None:
            # Reject bool explicitly (isinstance(True, int) is True).
            if isinstance(staleness, bool) or not isinstance(staleness, int):
                raise ToolValidationError(
                    "staleness_threshold_days must be a positive int when "
                    f"provided; got {staleness!r} ({type(staleness).__name__})"
                )
            if staleness < 1:
                raise ToolValidationError(
                    "staleness_threshold_days must be >= 1 when provided; "
                    f"got {staleness}"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        memory = _resolve_memory(ctx)
        layer = args.get("layer")
        query = args.get("query")
        limit = int(args.get("limit") or 20)
        mode = args.get("mode") or "private"

        # All three modes route through recall_visible_to so the tool
        # semantic is consistent: each mode describes EXACTLY what
        # the reader sees, with progressively wider scopes:
        #   private    — own scope='private' only
        #   lineage    — own private+lineage + chain peers' lineage
        #   consented  — lineage's set + consented grants
        # In v0.1 only scope='private' was reachable, so mode='private'
        # is the backward-compatible default for callers that don't
        # know about modes.
        explicit_chain = args.get("lineage_chain")
        if mode == "private":
            chain: tuple[str, ...] = (ctx.instance_id,)
        elif explicit_chain is not None:
            chain = tuple(explicit_chain)
        else:
            chain = _compute_lineage_chain(memory.conn, ctx.instance_id)
        entries = memory.recall_visible_to(
            reader_instance_id=ctx.instance_id,
            mode=mode,
            lineage_chain=chain,
            layer=layer,
            query=query,
            limit=limit,
        )
        chain_size = 0 if mode == "private" else len(chain)

        # ADR-0027-amendment §7 — new optional behaviors.
        surface_contradictions = bool(args.get("surface_contradictions"))
        staleness_threshold = args.get("staleness_threshold_days")

        out = []
        cross_agent_count = 0
        contradicted_count = 0
        stale_count = 0
        for e in entries:
            if e.instance_id != ctx.instance_id:
                cross_agent_count += 1
            # ADR-0027-amendment §7.6 — K1 fold. A verified entry
            # surfaces as confidence='high' regardless of stored value.
            # Reverts to stored value when verification has been
            # revoked (memory_verifications.revoked_at IS NOT NULL).
            effective_confidence = (
                "high" if memory.is_verified(entry_id=e.entry_id)
                else e.confidence
            )
            entry_dict = {
                "entry_id":             e.entry_id,
                "instance_id":          e.instance_id,
                "layer":                e.layer,
                "scope":                e.scope,
                "content":              e.content,
                "tags":                 list(e.tags),
                "created_at":           e.created_at,
                "is_disclosed_copy":    e.is_disclosed_copy,
                "disclosed_from_entry": e.disclosed_from_entry,
                "disclosed_summary":    e.disclosed_summary,
                # v11 epistemic fields — always surfaced. Defaults are
                # the schema CHECK column DEFAULTs ('observation',
                # 'medium') so v10-shape rows look unchanged to
                # callers that don't read these fields.
                "claim_type":           e.claim_type,
                "confidence":           effective_confidence,
                "last_challenged_at":   e.last_challenged_at,
            }
            # Optional: surface unresolved contradictions per-entry.
            if surface_contradictions:
                conflicts = memory.unresolved_contradictions_for(e.entry_id)
                entry_dict["unresolved_contradictions"] = conflicts
                if conflicts:
                    contradicted_count += 1
            # Optional: per-entry staleness flag.
            if staleness_threshold is not None:
                is_stale = memory.is_entry_stale(
                    e, threshold_days=int(staleness_threshold),
                )
                entry_dict["is_stale"] = is_stale
                if is_stale:
                    stale_count += 1
            out.append(entry_dict)

        metadata: dict[str, Any] = {
            "layer_filter":       layer,
            "query":              query,
            "limit":              limit,
            "mode":               mode,
            "lineage_chain_size": chain_size,
            # The runtime uses cross_agent_count to decide whether
            # to emit memory_read on the audit chain. Same-agent-
            # only reads stay quiet (ADR-0027 §6).
            "cross_agent_count":  cross_agent_count,
        }
        if surface_contradictions:
            metadata["surface_contradictions"] = True
            metadata["contradicted_count"] = contradicted_count
        if staleness_threshold is not None:
            metadata["staleness_threshold_days"] = int(staleness_threshold)
            metadata["stale_count"] = stale_count

        return ToolResult(
            output={
                "count":   len(out),
                "mode":    mode,
                "entries": out,
            },
            metadata=metadata,
            tokens_used=None, cost_usd=None,
            side_effect_summary=None,
        )


def _resolve_memory(ctx: ToolContext):
    """Pull the Memory from ctx. The daemon wires it; tests inject
    via constraints['memory'] for in-memory exercises.

    Modes other than 'private' read across agent boundaries — the
    Memory.recall_visible_to filter still ensures the reader only
    sees rows the scope admits.
    """
    # Preferred: ctx.memory attribute (daemon wiring).
    mem = getattr(ctx, "memory", None)
    if mem is not None:
        return mem
    # Test fallback: stash on constraints dict.
    mem = (ctx.constraints or {}).get("memory")
    if mem is not None:
        return mem
    raise ToolValidationError(
        "memory_recall.v1: no Memory bound to ctx (daemon wiring "
        "missing). The daemon must populate ctx.memory before "
        "dispatching."
    )


def _compute_lineage_chain(conn, instance_id: str) -> tuple[str, ...]:
    """Walk ``agent_ancestry`` to derive the reader's lineage chain.

    Per ADR-0027 §1, ``lineage`` scope means "owner + parent +
    descendants." The closure table answers both directions in one
    query each:

      ancestors-of-self:    ``WHERE instance_id = reader``
                             (depth=0 row gives self; depth>0 gives ancestors)
      descendants-of-self:  ``WHERE ancestor_id = reader``
                             (depth=0 again gives self; depth>0 gives descendants)

    Union of the two = the full lineage chain. The reader's own id
    appears in both (via the depth=0 self-row); the resulting tuple
    is deduplicated.

    On databases that don't have an ``agent_ancestry`` table (some
    in-memory test fixtures), we fall back to the singleton
    ``(instance_id,)`` chain — equivalent to "no peers." The Memory
    class already tolerates that shape.
    """
    try:
        rows_up = conn.execute(
            "SELECT ancestor_id FROM agent_ancestry WHERE instance_id = ?;",
            (instance_id,),
        ).fetchall()
        rows_down = conn.execute(
            "SELECT instance_id FROM agent_ancestry WHERE ancestor_id = ?;",
            (instance_id,),
        ).fetchall()
    except Exception:
        # Defensive — see docstring on the singleton fallback.
        return (instance_id,)
    chain: set[str] = {instance_id}
    for r in rows_up:
        chain.add(r[0] if not hasattr(r, "keys") else r["ancestor_id"])
    for r in rows_down:
        chain.add(r[0] if not hasattr(r, "keys") else r["instance_id"])
    return tuple(sorted(chain))
