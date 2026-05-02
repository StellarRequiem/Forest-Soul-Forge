"""``memory_flag_contradiction.v1`` — stamp a row in memory_contradictions.

ADR-0036 T2 — the action surface for the Verifier Loop. Where
memory_challenge.v1 stamps an entry as in-question (§7.4 staleness
signal), this tool stamps a *contradiction* row that names BOTH
sides — the entry that came earlier and the entry that disagrees
with it later.

The tool is operator-only at v0.3 by convention (constitutional kit
gating). Verifier-genre agents born with the tool reach it
autonomously; companion / actuator agents do not.

side_effects: filesystem
    Inserts into memory_contradictions table. Treated as filesystem
    rather than read_only because it mutates persistent state. The
    audit chain records the flag through the metadata.audit_event_type
    pattern (mirrors memory_challenge.v1).

required_initiative_level: L3
    Same posture as memory_challenge.v1. Reactive Companion (L1)
    cannot autonomously flag contradictions. Verifier (Guardian L3)
    reaches autonomously.

Args:
  earlier_entry_id (str, required): the older memory entry — the
    "former claim" side of the contradiction. Both entries must
    exist in memory_entries; the tool validates upfront so the FK
    error doesn't surface as a sqlite3.IntegrityError.
  later_entry_id (str, required): the newer memory entry — the
    "competing claim" side.
  contradiction_kind (str, required): one of {direct, updated,
    qualified, retracted} per ADR-0027-am §7.3 CHECK constraint.
    Verifier classification picks the kind via llm_think; manual
    operator flags pick directly.
  confidence (str, required): {low, medium, high}. ADR-0036 §4.1
    requires Verifier flags to land at high confidence; the tool
    accepts any value but the constitutional kit gating refuses
    flag_below_confidence_floor for verifier_loop role.
  note (str, optional): free-text rationale. Lands in the audit-
    event payload, NOT on the contradiction row. Max 500 chars.

Output:
  {
    "contradiction_id":   str,    # the new row's PK
    "earlier_entry_id":   str,
    "later_entry_id":     str,
    "contradiction_kind": str,
    "detected_at":        str,    # ISO timestamp
    "detected_by":        str,    # ctx.instance_id
  }

Visibility gate (mirrors memory_verify.v1 / memory_challenge.v1):
each entry must be reachable by the calling agent — a private
entry owned by another agent is unreachable, and the tool refuses
rather than allowing a leaky permission grant.
"""
from __future__ import annotations

from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


VALID_KINDS = ("direct", "updated", "qualified", "retracted")
VALID_CONFIDENCES = ("low", "medium", "high")
MAX_NOTE_LEN = 500


class MemoryFlagContradictionError(Exception):
    """Tool-level error — distinct from validation failures."""


class MemoryFlagContradictionTool:
    """Stamp a contradiction row. ADR-0036 T2."""

    name = "memory_flag_contradiction"
    version = "1"
    side_effects = "filesystem"
    # ADR-0021-amendment §5 — flagging a contradiction is a meta-
    # scrutiny stamp; same posture as memory_challenge / memory_verify.
    # L3 floor — reactive Companion (L1) cannot autonomously flag.
    required_initiative_level = "L3"

    def validate(self, args: dict[str, Any]) -> None:
        for key in ("earlier_entry_id", "later_entry_id"):
            val = args.get(key)
            if not isinstance(val, str) or not val.strip():
                raise ToolValidationError(
                    f"{key} is required and must be a non-empty string"
                )

        if args["earlier_entry_id"] == args["later_entry_id"]:
            raise ToolValidationError(
                "earlier_entry_id and later_entry_id must be distinct — "
                "an entry cannot contradict itself"
            )

        kind = args.get("contradiction_kind")
        if kind not in VALID_KINDS:
            raise ToolValidationError(
                f"contradiction_kind must be one of {VALID_KINDS}; got {kind!r}"
            )

        confidence = args.get("confidence")
        if confidence not in VALID_CONFIDENCES:
            raise ToolValidationError(
                f"confidence must be one of {VALID_CONFIDENCES}; got {confidence!r}"
            )

        note = args.get("note")
        if note is not None:
            if not isinstance(note, str):
                raise ToolValidationError(
                    "note must be a string when provided"
                )
            if len(note) > MAX_NOTE_LEN:
                raise ToolValidationError(
                    f"note too long ({len(note)} chars); max {MAX_NOTE_LEN}"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        earlier_id = args["earlier_entry_id"]
        later_id = args["later_entry_id"]
        kind = args["contradiction_kind"]
        confidence = args["confidence"]
        note = args.get("note")

        memory = ctx.memory
        if memory is None:
            raise MemoryFlagContradictionError(
                "memory subsystem not wired into this tool call — the "
                "daemon should populate ctx.memory; check lifespan"
            )

        # Verify both entries exist + are reachable. A flag against a
        # non-existent entry would fail at the FK constraint with a
        # less helpful error; pre-validate for a clean refusal.
        for label, entry_id in (
            ("earlier", earlier_id), ("later", later_id),
        ):
            entry = memory.get(entry_id)
            if entry is None:
                raise MemoryFlagContradictionError(
                    f"{label} entry {entry_id!r} not found in memory"
                )
            if entry.scope == "private" and entry.instance_id != ctx.instance_id:
                raise MemoryFlagContradictionError(
                    f"{label} entry {entry_id!r} is private to a "
                    f"different agent; cannot flag"
                )

        contradiction_id, detected_at = memory.flag_contradiction(
            earlier_entry_id=earlier_id,
            later_entry_id=later_id,
            contradiction_kind=kind,
            detected_by=ctx.instance_id,
        )

        return ToolResult(
            output={
                "contradiction_id":   contradiction_id,
                "earlier_entry_id":   earlier_id,
                "later_entry_id":     later_id,
                "contradiction_kind": kind,
                "detected_at":        detected_at,
                "detected_by":        ctx.instance_id,
            },
            metadata={
                "contradiction_id":  contradiction_id,
                "earlier_entry_id":  earlier_id,
                "later_entry_id":    later_id,
                "contradiction_kind": kind,
                "confidence":        confidence,
                "detected_by":       ctx.instance_id,
                "note_present":      note is not None,
                # The runtime emits memory_contradiction_flagged on the
                # audit chain when it sees this event_type marker.
                # Mirrors memory_challenge.v1's pattern — the note (if
                # any) lands in the audit payload, not on the row.
                "audit_event_type":  "memory_contradiction_flagged",
                "flag_note":         note,
            },
            side_effect_summary=(
                f"flagged contradiction {contradiction_id}: "
                f"{earlier_id} <-> {later_id} ({kind}, conf={confidence})"
            ),
            tokens_used=None, cost_usd=None,
        )


__all__ = [
    "MemoryFlagContradictionTool",
    "MemoryFlagContradictionError",
    "VALID_KINDS",
    "VALID_CONFIDENCES",
    "MAX_NOTE_LEN",
]
