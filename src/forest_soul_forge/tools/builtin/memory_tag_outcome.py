"""``memory_tag_outcome.v1`` — operator-driven shortcut reinforcement.

ADR-0054 T5 (Burst 182). The reinforcement loop for the
procedural-shortcut substrate. Where T1 stored shortcuts, T2-T4
made the dispatcher use them, T5 lets an operator say "that
shortcut hit was good" / "that shortcut hit was bad" so the
counters evolve and bad patterns soft-delete themselves out of
the search path.

side_effects: read_only
    Mutates only the agent's own per-instance procedural-shortcuts
    table — a counter integer per row. Not a network or filesystem
    side effect from the agent's perspective; the row is the
    agent's OWN state per ADR-0001 D2 (identity invariance: this
    table is per-instance state, not identity). Choosing read_only
    rather than filesystem keeps it accessible from posture-yellow
    agents (the same posture that allows tagging the previous turn
    to record_match) without requiring approval — operator
    feedback is the load-bearing signal we explicitly want
    flowing.

required_initiative_level: L2
    Operator-initiated by design. The chat-tab thumbs surface
    (T5b) drives this dispatch on operator click; the agent never
    autonomously tags its own outcomes. L2 floor matches
    "operator-supervised reactive" — the agent CAN issue this
    dispatch when an operator routes a tagging gesture through it,
    but cannot self-reinforce without operator involvement.

Args:
  shortcut_id (str, required): the row to tag. Must belong to the
    calling agent (instance_id match) — refusing cross-agent tags
    is a structural fence against an L3+ agent reaching across
    instance boundaries. The tool refuses with KeyError-style
    detail rather than silently no-op'ing so a misconfigured chat
    UI surfaces the bug instead of swallowing the gesture.
  outcome (str, required): one of {good, bad, neutral}.
    - "good"    -> strengthen(by=1), success_count += 1
    - "bad"     -> weaken(by=1), failure_count += 1
    - "neutral" -> no counter change, BUT the call is still
                   recorded in the audit chain so an operator
                   reviewing the conversation can see they DID
                   look at this hit and chose neither direction.
  note (str, optional): free-text context (≤300 chars). Lands on
    the audit metadata, NOT the row — same pattern as
    memory_flag_contradiction. Useful when the chat-tab thumbs
    surface offers a free-text "why?" follow-up.

Output:
  {
    "shortcut_id":            str,
    "outcome":                str,    # "good" | "bad" | "neutral"
    "new_success_count":      int,
    "new_failure_count":      int,
    "new_reinforcement_score": int,    # success - failure
    "soft_deleted":           bool,    # True iff reinforcement<0
                                       # — the row stays in the
                                       # table but search_by_cosine
                                       # will skip it
  }

Per ADR-0054 D2 the search path filters by
``(success_count - failure_count) >= reinforcement_floor`` (default
2), so a row with net-negative score is effectively soft-deleted
without an explicit DELETE. The ``soft_deleted`` field on the
output makes that state observable to the chat-tab UI ("this
pattern won't fire again — was that what you wanted?").
"""
from __future__ import annotations

from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolError,
    ToolResult,
    ToolValidationError,
)


VALID_OUTCOMES = ("good", "bad", "neutral")
MAX_NOTE_LEN = 300


class MemoryTagOutcomeError(ToolError):
    """Tool-level error — distinct from validation failures."""


class MemoryTagOutcomeTool:
    """Operator-tag a procedural shortcut as good/bad/neutral.

    ADR-0054 T5 reinforcement surface. The chat-tab thumbs widget
    is the primary caller in v0.1; future versions may add a
    /memory page for batch-reviewing recent shortcut hits.
    """

    name = "memory_tag_outcome"
    version = "1"
    side_effects = "read_only"
    # ADR-0021-amendment §5 — operator-initiated reinforcement.
    # L2 floor; reactive Companion (L1) cannot reach this on its
    # own initiative — only when operator-routed via the chat UI.
    required_initiative_level = "L2"

    def validate(self, args: dict[str, Any]) -> None:
        shortcut_id = args.get("shortcut_id")
        if not isinstance(shortcut_id, str) or not shortcut_id.strip():
            raise ToolValidationError(
                "shortcut_id is required and must be a non-empty string"
            )
        outcome = args.get("outcome")
        if outcome not in VALID_OUTCOMES:
            raise ToolValidationError(
                f"outcome must be one of {VALID_OUTCOMES}; got {outcome!r}"
            )
        note = args.get("note")
        if note is not None:
            if not isinstance(note, str):
                raise ToolValidationError(
                    "note must be a string when provided"
                )
            if len(note) > MAX_NOTE_LEN:
                raise ToolValidationError(
                    f"note too long ({len(note)} chars); "
                    f"max {MAX_NOTE_LEN}"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        shortcut_id: str = args["shortcut_id"]
        outcome: str = args["outcome"]
        note: str | None = args.get("note")

        table = ctx.procedural_shortcuts
        if table is None:
            raise MemoryTagOutcomeError(
                "procedural shortcut substrate is not wired into "
                "this dispatcher — operator must enable the "
                "substrate (T6) before tagging outcomes"
            )

        # Verify the row exists + belongs to this agent. Cross-
        # agent tagging would be a privilege escalation — refuse
        # structurally rather than relying on caller discipline.
        try:
            row = table.get(shortcut_id)
        except KeyError as e:
            raise MemoryTagOutcomeError(
                f"shortcut_id={shortcut_id!r} not found"
            ) from e
        if getattr(row, "instance_id", None) != ctx.instance_id:
            raise MemoryTagOutcomeError(
                f"shortcut_id={shortcut_id!r} belongs to a different "
                f"agent; cross-agent tagging is refused"
            )

        # Apply the reinforcement. "neutral" is a no-op on counters
        # but still produces an audit-visible event because the
        # operator's deliberate non-tag is itself signal ("I saw
        # this and chose neither direction").
        if outcome == "good":
            table.strengthen(shortcut_id, by=1)
        elif outcome == "bad":
            table.weaken(shortcut_id, by=1)
        # outcome == "neutral" → no counter change.

        # Re-read so the returned dataclass reflects the post-
        # update state. Defensive against future trigger logic
        # that might compute derived columns.
        updated = table.get(shortcut_id)
        new_success = int(updated.success_count)
        new_failure = int(updated.failure_count)
        new_score = new_success - new_failure
        soft_deleted = new_score < 0

        return ToolResult(
            output={
                "shortcut_id":             shortcut_id,
                "outcome":                 outcome,
                "new_success_count":       new_success,
                "new_failure_count":       new_failure,
                "new_reinforcement_score": new_score,
                "soft_deleted":            soft_deleted,
            },
            metadata={
                "shortcut_id":   shortcut_id,
                "outcome":       outcome,
                "note_present":  note is not None,
                "note":          note,
                # Audit chain emits a regular tool_call_succeeded
                # for this dispatch — the shortcut metadata above
                # is enough to grep for "what tags landed on
                # which shortcut" without needing a dedicated
                # event type. Reinforcement tags are routine
                # operator gestures, unlike the substitution
                # itself (which warranted tool_call_shortcut at
                # T4 because operator absence is the norm).
            },
            tokens_used=None,
            cost_usd=None,
            side_effect_summary=(
                f"tag_outcome: {shortcut_id} -> {outcome} "
                f"(success={new_success} failure={new_failure} "
                f"score={new_score}"
                + (" soft_deleted)" if soft_deleted else ")")
            ),
        )


__all__ = [
    "MemoryTagOutcomeTool",
    "MemoryTagOutcomeError",
    "VALID_OUTCOMES",
    "MAX_NOTE_LEN",
]
