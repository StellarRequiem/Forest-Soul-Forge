"""``memory_disclose.v1`` — cross-agent memory disclosure.

ADR-0022 v0.2 + ADR-0027 §4 — the data-minimization rule:

* The full content of the source entry stays on the originator's
  store. The recipient gets only a **summary string** (operator/agent-
  supplied at disclosure time), the entry id (for back-reference),
  and the scope under which it was disclosed (``consented``).

* Disclosure requires a pre-existing consent grant for the
  ``(source_entry, recipient_instance)`` pair. Granting consent is a
  separate operation (Memory.grant_consent / future ``memory_consent``
  endpoints in T16). This tool refuses if the grant is missing or
  revoked — agents cannot self-grant.

* Only ``scope='consented'`` source entries are disclosable. Private
  and lineage entries are refused: the original scope choice
  expressed an intent that disclose would silently override. If the
  operator wants to disclose a private/lineage entry, they re-scope
  the original first.

The runtime emits ``memory_disclosed`` on the audit chain after a
successful execution. This tool's metadata supplies recipient,
source_entry_id, and summary digest for the chain entry.

Side-effects classification: ``read_only`` for the same reason
``memory_write.v1`` is — the disclosed copy lands in the local
registry, not on the network or the host filesystem outside the
registry. The ADR-0033 swarm chain (low → mid → high) routes through
this tool when a finding crosses tier boundaries.
"""
from __future__ import annotations

from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


# Same constraints as memory_write.v1 — the summary is the disclosed
# *content* on the recipient side, so the same 8 KB ceiling on the
# disclosed-copy row applies. In practice summaries should be much
# shorter (a sentence or two) — ADR-0027 §4's minimum-disclosure rule
# argues for terse — but the hard cap matches memory_write so an
# operator-supplied summary doesn't hit a different limit unexpectedly.
_MAX_SUMMARY = 8 * 1024


class MemoryDiscloseTool:
    """Disclose one of the calling agent's ``scope='consented'``
    memory entries to another agent's store.

    Args:
      source_entry_id (str, required): the entry on the caller's
        store to disclose. Must be owned by the caller, not deleted,
        and at ``scope='consented'``.
      recipient_instance (str, required): the agent that receives
        the disclosed copy. Must already have an active consent grant
        on ``source_entry_id`` (created via Memory.grant_consent or
        the upcoming consent endpoints in T16).
      summary (str, required): the minimum-disclosure text the
        recipient sees. The full source content does NOT cross — only
        this summary does. Operator-supplied; per ADR-0027 §4 should
        be terse and reference, not reproduce, the original.

    Output:
      { disclosed_entry_id, recipient_instance, source_entry_id,
        disclosed_at, summary_digest }

    Refusals (raise ToolValidationError):
      * source not found / owned by caller / deleted
      * source scope is not 'consented'
      * recipient_instance does not exist in the registry
      * no active consent grant for (source, recipient)
    """

    name = "memory_disclose"
    version = "1"
    side_effects = "read_only"  # local-only registry write; see module docstring
    # ADR-0021-amendment §5 — cross-agent memory disclosure is a
    # load-bearing decision (ADR-0027 §4 minimum-disclosure). Requires
    # L3+: reactive Companion (L1) and suggestion-class Communicator
    # (L2) cannot autonomously disclose. The agent's own memory_ceiling
    # (per ADR-0027 §5 genre privacy floor) gates which scopes can be
    # disclosed at all; this gate adds the orthogonal initiative axis.
    required_initiative_level = "L3"

    def validate(self, args: dict[str, Any]) -> None:
        for field in ("source_entry_id", "recipient_instance", "summary"):
            v = args.get(field)
            if not isinstance(v, str) or not v.strip():
                raise ToolValidationError(
                    f"{field} must be a non-empty string"
                )
        summary = args["summary"]
        if len(summary) > _MAX_SUMMARY:
            raise ToolValidationError(
                f"summary exceeds max {_MAX_SUMMARY} bytes; got {len(summary)}"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        memory = _resolve_memory(ctx)
        source_id = args["source_entry_id"]
        recipient = args["recipient_instance"]
        summary = args["summary"]

        # 1. Source entry must exist, belong to caller, not be deleted,
        #    and be at scope='consented'.
        source = memory.get(source_id)
        if source is None:
            raise ToolValidationError(
                f"source entry {source_id!r} not found"
            )
        if source.instance_id != ctx.instance_id:
            # Ownership check is the bedrock of this tool. An agent
            # cannot disclose entries it doesn't own — the originating
            # agent has to be the one consenting to share.
            raise ToolValidationError(
                f"source entry {source_id!r} is not owned by the calling "
                f"agent (owned by {source.instance_id!r})"
            )
        if source.is_deleted:
            raise ToolValidationError(
                f"source entry {source_id!r} is deleted; cannot disclose"
            )
        if source.scope != "consented":
            raise ToolValidationError(
                f"source entry {source_id!r} is at scope {source.scope!r}; "
                "only 'consented'-scoped entries are disclosable. "
                "Re-scope the original or write a new consented entry."
            )

        # 2. Recipient must exist as an agent in the registry. We
        #    reach into the same connection the Memory class is bound
        #    to — both go through the registry's single-writer
        #    discipline so there's no second connection to coordinate.
        recipient_row = memory.conn.execute(
            "SELECT instance_id FROM agents WHERE instance_id = ? LIMIT 1;",
            (recipient,),
        ).fetchone()
        if recipient_row is None:
            raise ToolValidationError(
                f"recipient instance {recipient!r} not found in registry"
            )
        if recipient == ctx.instance_id:
            # Self-disclosure is a no-op and almost certainly a
            # caller bug — refusing makes the bug visible.
            raise ToolValidationError(
                "recipient_instance must differ from the calling agent; "
                "self-disclosure is meaningless"
            )

        # 3. Active consent grant must exist for (source, recipient).
        #    Memory.is_consented honors the revoked_at column.
        if not memory.is_consented(
            entry_id=source_id, recipient_instance=recipient,
        ):
            raise ToolValidationError(
                f"no active consent grant for entry {source_id!r} → "
                f"recipient {recipient!r}. Grant consent first "
                "(Memory.grant_consent or the consent endpoints)."
            )

        # 4. Materialize the disclosed copy on the recipient's side.
        #    Goes through Memory.append for the originating-side
        #    structure, then patches in the disclosed_* columns. We
        #    write the SUMMARY as the disclosed copy's content so
        #    every read path sees the minimum disclosure rather than
        #    the original. Per ADR-0027 §4 the original content stays
        #    where it was.
        from forest_soul_forge.core.memory import _now_iso, _sha256
        disclosed_at = _now_iso()
        # We don't use Memory.append here because append is shaped for
        # originating-side writes (no disclosed_* columns). A direct
        # INSERT is the right primitive — the runtime carries the
        # audit-chain emission, this tool just stages the row.
        import uuid
        new_entry_id = str(uuid.uuid4())
        memory.conn.execute(
            """
            INSERT INTO memory_entries (
                entry_id, instance_id, agent_dna, layer, scope,
                content, content_digest, tags_json, consented_to_json,
                created_at, disclosed_from_entry, disclosed_summary,
                disclosed_at
            ) VALUES (?, ?, ?, ?, 'consented', ?, ?, '[]', '[]', ?, ?, ?, ?);
            """,
            (
                new_entry_id, recipient,
                # Recipient's agent_dna is fetched from the recipient row.
                # If we wanted to avoid the extra query we could carry it
                # via the agents row above — but that row was returned by
                # SELECT instance_id only. Fetch the dna alongside.
                _fetch_agent_dna(memory.conn, recipient),
                source.layer,
                summary,        # content = the minimum summary
                _sha256(summary),
                disclosed_at,
                source_id,      # disclosed_from_entry
                summary,        # disclosed_summary (same as content for v0.2)
                disclosed_at,
            ),
        )
        summary_digest = _sha256(summary)

        return ToolResult(
            output={
                "disclosed_entry_id":  new_entry_id,
                "recipient_instance":  recipient,
                "source_entry_id":     source_id,
                "disclosed_at":        disclosed_at,
                "summary_digest":      summary_digest,
            },
            metadata={
                # The runtime hashes metadata into the audit chain so
                # the chain entry contains everything an inspector
                # would need to reconstruct the disclosure without
                # leaking the original content. Summary digest
                # appears here AND in output — different consumers.
                "source_entry_id":     source_id,
                "recipient_instance":  recipient,
                "summary_digest":      summary_digest,
                "summary_length":      len(summary),
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"disclosed source entry to {recipient}: "
                f"{summary[:60]}{'…' if len(summary) > 60 else ''}"
            ),
        )


def _resolve_memory(ctx: ToolContext):
    """Mirror of memory_write._resolve_memory — same wiring contract."""
    mem = getattr(ctx, "memory", None)
    if mem is not None:
        return mem
    mem = (ctx.constraints or {}).get("memory")
    if mem is not None:
        return mem
    raise ToolValidationError(
        "memory_disclose.v1: no Memory bound to ctx (daemon wiring missing)."
    )


def _fetch_agent_dna(conn, instance_id: str) -> str:
    """One-shot lookup of an agent's DNA for the recipient row's
    ``agent_dna`` column. We carry the dna on the disclosed copy so
    the recipient's character sheet + memory queries can join on it
    the same way they do for originating rows."""
    row = conn.execute(
        "SELECT dna FROM agents WHERE instance_id = ? LIMIT 1;",
        (instance_id,),
    ).fetchone()
    if row is None:
        # Should be unreachable — we validated existence in execute()
        # before calling this. Defensive raise so a future refactor
        # that drops the validation surfaces here instead of writing
        # a row with empty dna.
        raise ToolValidationError(
            f"recipient instance {instance_id!r} disappeared between "
            "validation and disclosure write — registry inconsistency"
        )
    return row[0] if not hasattr(row, "keys") else row["dna"]
