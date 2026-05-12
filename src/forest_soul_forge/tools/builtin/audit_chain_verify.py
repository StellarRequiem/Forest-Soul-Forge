"""``audit_chain_verify.v1`` — walk the daemon's audit chain and
verify hash links.

ADR-0033 Phase B1. LogLurker's reason to exist: a swarm-low agent
that runs this tool periodically catches a tampered or truncated
audit chain before mid-tier sees a corrupted view.

The tool wraps :meth:`AuditChain.verify` — the canonical chain
verifier — and surfaces:

  * ``ok`` — True iff every entry is hash-linked to its predecessor
              and the genesis is intact
  * ``entries_verified`` — count of entries walked
  * ``broken_at_seq`` — sequence number of the first structural
                         break (None when ok)
  * ``reason`` — one-line description of the break
  * ``unknown_event_types`` — event types in the chain that aren't
                               in KNOWN_EVENT_TYPES (forward-compat
                               warning, not a failure)

side_effects=read_only — the tool reads the JSONL, never writes.
The audit chain itself is the daemon's source of truth so this is
strictly an inspection.

Path resolution: the tool reaches for the chain via the daemon's
:class:`AuditChain` instance on ``ctx.constraints['audit_chain']``
(test fallback) or, in the daemon path, the dispatcher exposes it
through a future ToolContext field. When neither is available the
tool refuses cleanly rather than guessing at a filesystem path.
"""
from __future__ import annotations

from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


class AuditChainVerifyTool:
    """Verify the daemon's audit chain end-to-end.

    Args:
      max_unknown_to_report (int, optional): cap on the number of
        unknown event types returned in the output. Default 20.
        Larger chains with many forward-compat events get truncated
        to keep the response bounded; the full count is still
        reported in metadata.
      strict (bool, optional): ADR-0049 T7 — when true, require
        every agent-emitted entry (agent_dna != None) to carry a
        non-null `signature` field. Refuses the chain on the first
        agent-emitted entry without a signature. Default false
        keeps the ADR-0049 D5 'tolerant' contract — legacy
        pre-ADR-0049 entries pass with hash check only. Strict
        mode is for operators verifying that EVERY agent action
        post-ADR-0049 is digitally signed; useful for compliance
        snapshots + tamper-proof archival.

    Output:
      {
        "ok":               bool,
        "entries_verified": int,
        "broken_at_seq":    int | null,
        "reason":           str  | null,
        "unknown_event_types":      [str, ...],   # capped
        "unknown_event_types_count": int          # uncapped
      }
    """

    name = "audit_chain_verify"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        cap = args.get("max_unknown_to_report")
        if cap is not None:
            if not isinstance(cap, int) or cap < 1 or cap > 1000:
                raise ToolValidationError(
                    f"max_unknown_to_report must be an int 1..1000; got {cap!r}"
                )
        strict = args.get("strict")
        if strict is not None and not isinstance(strict, bool):
            raise ToolValidationError(
                f"strict must be a bool; got {type(strict).__name__}"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        chain = _resolve_chain(ctx)
        strict = bool(args.get("strict", False))
        result = chain.verify(strict=strict)
        cap = int(args.get("max_unknown_to_report") or 20)
        unknown_full = list(result.unknown_event_types)
        unknown_capped = unknown_full[:cap]
        truncated = len(unknown_full) > cap

        return ToolResult(
            output={
                "ok":                         result.ok,
                "entries_verified":           result.entries_verified,
                "broken_at_seq":              result.broken_at_seq,
                "reason":                     result.reason,
                "unknown_event_types":        unknown_capped,
                "unknown_event_types_count":  len(unknown_full),
            },
            metadata={
                "truncated_unknown": truncated,
                # The chain head's hash is the natural fingerprint
                # for "what state did we verify?" An operator who
                # runs this twice and gets the same head + ok=true
                # knows nothing has appended in between.
                "head_seq": chain.head_seq() if hasattr(chain, "head_seq") else None,
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"verified {result.entries_verified} entries; "
                f"{'ok' if result.ok else f'BROKEN at seq={result.broken_at_seq}'}"
            ),
        )


def _resolve_chain(ctx: ToolContext):
    """Pull the AuditChain instance from ctx. Two paths:

    * ``ctx.constraints['audit_chain']`` — test fallback (in-memory
      chain constructed by the test fixture)
    * (future) ``ctx.audit_chain`` — daemon-wired field

    Refuses cleanly when neither is set so the dispatcher returns
    a 4xx rather than crashing on AttributeError.
    """
    chain = (ctx.constraints or {}).get("audit_chain")
    if chain is not None:
        return chain
    chain = getattr(ctx, "audit_chain", None)
    if chain is not None:
        return chain
    raise ToolValidationError(
        "audit_chain_verify.v1: no AuditChain bound to ctx (daemon "
        "wiring missing). The daemon must populate the chain ref "
        "before dispatching."
    )
