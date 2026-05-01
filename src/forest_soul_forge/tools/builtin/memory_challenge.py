"""``memory_challenge.v1`` — explicit signal that an entry is in question.

ADR-0027-amendment §7.4 + open question 4.

A challenge is **distinct from a contradiction**: a contradiction has
a competing later entry that disagrees with the earlier one, and lands
in the ``memory_contradictions`` table. A challenge is the operator
saying "this entry is being scrutinized" *without* yet writing a
competing entry. The ``last_challenged_at`` column on memory_entries
captures the staleness pressure; ``memory_recall.v1`` surfaces it
through the staleness check (Tranche 3b, commit 24ec62b).

Why operator-only at v0.2 (per ADR-0027-am open question 4):
    Agent-self-challenge produces ambiguity. Did the agent challenge
    its own entry because it's uncertain (legitimate) or because it
    wants to manipulate the operator's trust signal (failure mode)?
    Agents can already record uncertainty at write time via
    ``confidence: low``. Explicit challenge stays operator-driven.

Operator-only enforcement matches the ``memory_verify.v1`` pattern:
the tool accepts an explicit ``challenger_id`` arg (the operator's
handle / public key fingerprint / signing identity). The constitutional
kit gating decides whether an agent has access to this tool — operators
deliberately add it via ``tools_add`` at birth time only for agents
that should be able to surface this signal on the operator's behalf.
The runtime's identity layer is ADR-0025 territory; this tool stays
narrow.

side_effects: filesystem
    Writes to memory_entries.last_challenged_at; treated as filesystem
    rather than read_only because it mutates a memory row. The audit
    chain records the challenge through the runtime's standard
    ``memory_challenged`` event emission.

Wires through ctx.memory like the other memory tools.
"""
from __future__ import annotations

from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


class MemoryChallengeError(Exception):
    """Tool-level error — distinct from validation failures."""


class MemoryChallengeTool:
    """Stamp an entry as challenged. Operator-driven (gated via kit).

    Args:
      entry_id (str): the memory entry being challenged. Required;
        non-empty string.
      challenger_id (str): the human challenger's identifier (operator
        handle, public key fingerprint, signing handle). Required;
        non-empty string. Same shape as ``memory_verify.v1``'s
        ``verifier_id``. Lands in the audit-event payload so an
        inspector can answer "who challenged this?"
      note (str, optional): free text explaining the challenge (e.g.
        "user disputed this in latest session", "third-party source
        contradicts"). Lands in the audit-event payload; max 500
        chars. The note is NOT stored on the memory row — only the
        timestamp (``last_challenged_at``) is. Per ADR-0027-am §7.4
        the row's content + claim_type stay unchanged; the challenge
        is a distinct signal.

    Output:
      {
        "entry_id":            str,
        "challenged":          bool,
        "challenger_id":       str,
        "last_challenged_at":  str,    # ISO timestamp written
      }

    Visibility gate (mirrors ``memory_verify.v1``): the calling agent
    must be able to see the entry. A private entry owned by another
    agent is unreachable; the tool refuses rather than allowing a
    leaky permission grant.
    """

    name = "memory_challenge"
    version = "1"
    side_effects = "filesystem"

    def validate(self, args: dict[str, Any]) -> None:
        entry_id = args.get("entry_id")
        if not isinstance(entry_id, str) or not entry_id.strip():
            raise ToolValidationError(
                "entry_id is required and must be a non-empty string"
            )

        challenger_id = args.get("challenger_id")
        if not isinstance(challenger_id, str) or not challenger_id.strip():
            raise ToolValidationError(
                "challenger_id is required and must be a non-empty string"
            )

        note = args.get("note")
        if note is not None:
            if not isinstance(note, str):
                raise ToolValidationError(
                    "note must be a string when provided"
                )
            if len(note) > 500:
                raise ToolValidationError(
                    f"note too long ({len(note)} chars); max 500"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        entry_id = args["entry_id"]
        challenger_id = args["challenger_id"]
        note = args.get("note")

        memory = ctx.memory
        if memory is None:
            raise MemoryChallengeError(
                "memory subsystem not wired into this tool call — the "
                "daemon should populate ctx.memory; check lifespan"
            )

        # Confirm the entry exists and the calling agent can see it.
        # Same gate as memory_verify.v1 — challenging an entry the
        # agent can't reach would be a leaky permission grant.
        entry = memory.get(entry_id)
        if entry is None:
            raise MemoryChallengeError(
                f"memory entry {entry_id!r} not found"
            )
        if entry.scope == "private" and entry.instance_id != ctx.instance_id:
            raise MemoryChallengeError(
                f"entry {entry_id!r} is private to a different agent; "
                f"cannot challenge"
            )

        ts = memory.mark_challenged(entry_id=entry_id)

        return ToolResult(
            output={
                "entry_id":           entry_id,
                "challenged":         True,
                "challenger_id":      challenger_id,
                "last_challenged_at": ts,
            },
            metadata={
                "entry_id":          entry_id,
                "challenger_id":     challenger_id,
                "note_present":      note is not None,
                # The runtime emits ``memory_challenged`` on the audit
                # chain when it sees this event_type marker. The note
                # (if any) lands in the audit payload, not on the row.
                "audit_event_type":  "memory_challenged",
                "challenge_note":    note,
            },
            side_effect_summary=(
                f"challenged entry {entry_id} by {challenger_id}"
                + (f" ({note[:60]}...)" if note and len(note) > 60
                   else f" ({note})" if note else "")
            ),
            tokens_used=None, cost_usd=None,
        )


__all__ = ["MemoryChallengeError", "MemoryChallengeTool"]
