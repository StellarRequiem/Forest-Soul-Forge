"""``debate_orchestrate.v1`` — ADR-0090 Phase C debate orchestrator.

Deterministic turn-ordering for a multi-agent debate. Inputs:
question + role-set + prior transcript + ordering strategy.
Outputs: next_speaker, next_turn_kind, and a termination signal
when the debate should close.

Deterministic so the operator can audit + replay the orchestration
— LLM-driven moderation is opaque; this tool emits the structural
turn-ordering, while LLM narrative layers on top via the
debate_moderation.v1 skill.

Read-only. The debate_moderator (D10 Phase C) is the sole
consumer.

## Strategies

  - ``round_robin``: cycle through the role list in declared
    order. Each speaker takes one turn per round.
  - ``structured``: opinion-then-counter-then-synth — the first
    role opens, the second counters, the third synthesizes. If
    there are more than 3 roles, additional roles take counters.
  - ``demand_driven``: whichever role has spoken least so far
    speaks next; ties broken by declaration order.

## Turn kinds

  - ``open``: the first turn of the debate. Speaker frames the
    question.
  - ``counter``: a critic-style turn. The speaker challenges the
    most-recent open / synthesis.
  - ``rebut``: a response to a counter. The speaker defends or
    qualifies a prior position.
  - ``synthesize``: a synthesis-style turn. The speaker aggregates
    across prior turns.
  - ``close``: the final turn. Emitted with ``terminate=True``
    when the debate has hit the turn cap or operator-flagged
    completion.

## Inputs

  question (str, required): the debate question.
  roles (list[str], required): participating role names. Order
    matters for round_robin + structured strategies.
  transcript (list[dict], optional): prior turns, each with
    {speaker (str), turn_kind (str, optional), summary (str,
    optional)}. Default empty (first turn).
  strategy (str, optional): one of round_robin / structured /
    demand_driven. Default ``round_robin``.
  max_turns (int, optional): hard cap on total turns. Default 12.
  operator_signaled_close (bool, optional): when true, the next
    turn is ``close`` regardless of count. Default false.

## Output

  {
    "orchestrated_at":      str (ISO Z),
    "question":             str,
    "strategy":             str,
    "next_speaker":         str,    # role name; "" if terminated
    "next_turn_kind":       str,    # open/counter/rebut/synthesize/close
    "turn_index":           int,    # 0-based; matches transcript length
    "terminate":            bool,
    "terminate_reason":     str,    # "max_turns"/"operator_signal"/""
    "turn_counts":          {role: int, ...},
    "rationale":            str,    # human-readable rationale
  }

side_effects=read_only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_MAX_ROLES = 20
_MAX_TRANSCRIPT = 200
_MAX_QUESTION_LEN = 2000
_MAX_TURNS_CEILING = 200
_VALID_STRATEGIES = {"round_robin", "structured", "demand_driven"}
_VALID_TURN_KINDS = {"open", "counter", "rebut", "synthesize", "close"}


class DebateOrchestrateTool:
    """Deterministic turn-ordering for a multi-agent debate."""

    name = "debate_orchestrate"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        q = args.get("question")
        if not isinstance(q, str) or not q.strip():
            raise ToolValidationError("question is required")
        if len(q) > _MAX_QUESTION_LEN:
            raise ToolValidationError(
                f"question must be <= {_MAX_QUESTION_LEN} chars"
            )

        roles = args.get("roles")
        if not isinstance(roles, list) or not roles:
            raise ToolValidationError(
                "roles must be a non-empty list"
            )
        if len(roles) > _MAX_ROLES:
            raise ToolValidationError(
                f"roles must have <= {_MAX_ROLES} entries"
            )
        for i, r in enumerate(roles):
            if not isinstance(r, str) or not r.strip():
                raise ToolValidationError(
                    f"roles[{i}] must be a non-empty string"
                )

        strategy = args.get("strategy", "round_robin")
        if strategy not in _VALID_STRATEGIES:
            raise ToolValidationError(
                f"strategy must be one of {sorted(_VALID_STRATEGIES)}"
            )

        tx = args.get("transcript", [])
        if not isinstance(tx, list):
            raise ToolValidationError("transcript must be a list")
        if len(tx) > _MAX_TRANSCRIPT:
            raise ToolValidationError(
                f"transcript must have <= {_MAX_TRANSCRIPT} entries"
            )
        for i, t in enumerate(tx):
            if not isinstance(t, dict):
                raise ToolValidationError(
                    f"transcript[{i}] must be an object"
                )
            sp = t.get("speaker")
            if not isinstance(sp, str) or not sp.strip():
                raise ToolValidationError(
                    f"transcript[{i}].speaker is required"
                )
            tk = t.get("turn_kind")
            if tk is not None and tk not in _VALID_TURN_KINDS:
                raise ToolValidationError(
                    f"transcript[{i}].turn_kind must be one of "
                    f"{sorted(_VALID_TURN_KINDS)}"
                )

        mt = args.get("max_turns", 12)
        if not isinstance(mt, int) or mt < 1 or mt > _MAX_TURNS_CEILING:
            raise ToolValidationError(
                f"max_turns must be an integer in [1, {_MAX_TURNS_CEILING}]"
            )

        osc = args.get("operator_signaled_close", False)
        if not isinstance(osc, bool):
            raise ToolValidationError(
                "operator_signaled_close must be a boolean"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        question = args["question"]
        roles: list[str] = list(args["roles"])
        strategy = args.get("strategy", "round_robin")
        transcript: list[dict[str, Any]] = list(args.get("transcript", []))
        max_turns = int(args.get("max_turns", 12))
        operator_close = bool(args.get("operator_signaled_close", False))

        turn_counts = {r: 0 for r in roles}
        for t in transcript:
            sp = t["speaker"]
            if sp in turn_counts:
                turn_counts[sp] += 1

        turn_index = len(transcript)

        # Termination triggers
        terminate = False
        terminate_reason = ""
        if operator_close:
            terminate = True
            terminate_reason = "operator_signal"
        elif turn_index >= max_turns:
            terminate = True
            terminate_reason = "max_turns"

        if terminate:
            # Pick a synthesizing role to close if available;
            # otherwise the last speaker.
            last_speaker = (
                transcript[-1]["speaker"] if transcript else roles[0]
            )
            close_speaker = _pick_close_speaker(roles, last_speaker)
            body = _compose_body(
                question, strategy, close_speaker, "close",
                turn_index, True, terminate_reason, turn_counts,
                rationale=(
                    f"debate terminated ({terminate_reason}); "
                    f"{close_speaker} delivers the closing turn"
                ),
            )
            return ToolResult(
                output=body,
                metadata={
                    "next_speaker":  close_speaker,
                    "terminate":     True,
                    "turn_index":    turn_index,
                },
                tokens_used=None, cost_usd=None,
                side_effect_summary=(
                    f"orchestrated close: {close_speaker} "
                    f"({terminate_reason})"
                ),
            )

        # Strategy selection
        if strategy == "round_robin":
            next_speaker = roles[turn_index % len(roles)]
        elif strategy == "structured":
            next_speaker = _structured_pick(roles, turn_index)
        else:  # demand_driven
            next_speaker = _demand_pick(roles, turn_counts)

        next_turn_kind = _infer_turn_kind(
            transcript, turn_index, next_speaker, roles, strategy,
        )

        body = _compose_body(
            question, strategy, next_speaker, next_turn_kind,
            turn_index, False, "", turn_counts,
            rationale=(
                f"strategy={strategy}; turn_index={turn_index}; "
                f"next={next_speaker} (turn_kind={next_turn_kind})"
            ),
        )
        return ToolResult(
            output=body,
            metadata={
                "next_speaker":  next_speaker,
                "next_turn_kind": next_turn_kind,
                "terminate":     False,
                "turn_index":    turn_index,
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"orchestrated turn {turn_index}: "
                f"{next_speaker} ({next_turn_kind})"
            ),
        )


def _structured_pick(roles: list[str], turn_index: int) -> str:
    # Structured: roles[0] opens, then alternating
    # counter/synthesize. With >=3 roles: roles[0] open, roles[1]
    # counter, roles[2] synthesize, then cycle back.
    return roles[turn_index % len(roles)]


def _demand_pick(roles: list[str], counts: dict[str, int]) -> str:
    # Whichever role has spoken least so far; ties broken by
    # declaration order.
    min_count = min(counts.get(r, 0) for r in roles)
    for r in roles:
        if counts.get(r, 0) == min_count:
            return r
    return roles[0]


def _pick_close_speaker(roles: list[str], last_speaker: str) -> str:
    # Prefer a synthesizer-ish role to close. Otherwise the last
    # speaker.
    for r in roles:
        if "synthesizer" in r.lower() or "synth" in r.lower():
            return r
    if last_speaker in roles:
        return last_speaker
    return roles[0]


def _infer_turn_kind(
    transcript: list[dict[str, Any]],
    turn_index: int,
    speaker: str,
    roles: list[str],
    strategy: str,
) -> str:
    if turn_index == 0:
        return "open"
    # If the speaker is a synthesizer-ish role, label synthesize.
    if "synthesizer" in speaker.lower() or "synth" in speaker.lower():
        return "synthesize"
    # If the speaker is a critic-ish role, label counter.
    if "critic" in speaker.lower():
        return "counter"
    # If the previous turn was a counter, this is a rebut.
    if transcript:
        prev = transcript[-1]
        if prev.get("turn_kind") == "counter":
            return "rebut"
    # Default for non-special roles after open
    return "rebut"


def _compose_body(
    question: str,
    strategy: str,
    next_speaker: str,
    next_turn_kind: str,
    turn_index: int,
    terminate: bool,
    terminate_reason: str,
    turn_counts: dict[str, int],
    rationale: str,
) -> dict[str, Any]:
    return {
        "orchestrated_at":  datetime.now(timezone.utc)
                                     .replace(tzinfo=None)
                                     .isoformat(timespec="seconds")
                                     + "Z",
        "question":         question,
        "strategy":         strategy,
        "next_speaker":     next_speaker,
        "next_turn_kind":   next_turn_kind,
        "turn_index":       turn_index,
        "terminate":        terminate,
        "terminate_reason": terminate_reason,
        "turn_counts":      turn_counts,
        "rationale":        rationale,
    }
