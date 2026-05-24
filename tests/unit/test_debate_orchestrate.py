"""Tests for ADR-0090 Phase C — debate_orchestrate.v1 builtin tool."""
from __future__ import annotations

import asyncio

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.debate_orchestrate import (
    DebateOrchestrateTool,
)


def _ctx():
    return ToolContext(
        instance_id="debate_mod_test",
        agent_dna="a" * 12,
        role="debate_moderator",
        genre="researcher",
        session_id=None,
    )


def _run(args):
    return asyncio.run(DebateOrchestrateTool().execute(args, _ctx()))


class TestValidation:
    def test_question_required(self):
        with pytest.raises(ToolValidationError, match="question"):
            DebateOrchestrateTool().validate(
                {"roles": ["analyst", "critic"]}
            )

    def test_roles_required(self):
        with pytest.raises(ToolValidationError, match="roles"):
            DebateOrchestrateTool().validate({"question": "Q?"})

    def test_roles_must_be_nonempty(self):
        with pytest.raises(ToolValidationError, match="roles"):
            DebateOrchestrateTool().validate(
                {"question": "Q?", "roles": []}
            )

    def test_strategy_must_be_valid(self):
        with pytest.raises(ToolValidationError, match="strategy"):
            DebateOrchestrateTool().validate(
                {"question": "Q?", "roles": ["a"], "strategy": "bogus"}
            )

    def test_transcript_must_be_list(self):
        with pytest.raises(ToolValidationError, match="transcript"):
            DebateOrchestrateTool().validate(
                {"question": "Q?", "roles": ["a"], "transcript": "bad"}
            )

    def test_transcript_speaker_required(self):
        with pytest.raises(ToolValidationError, match="speaker"):
            DebateOrchestrateTool().validate({
                "question": "Q?", "roles": ["a"],
                "transcript": [{"turn_kind": "open"}],
            })

    def test_max_turns_must_be_positive(self):
        with pytest.raises(ToolValidationError, match="max_turns"):
            DebateOrchestrateTool().validate(
                {"question": "Q?", "roles": ["a"], "max_turns": 0}
            )

    def test_operator_signaled_close_must_be_bool(self):
        with pytest.raises(ToolValidationError, match="operator_signaled_close"):
            DebateOrchestrateTool().validate({
                "question": "Q?", "roles": ["a"],
                "operator_signaled_close": "yes",
            })


class TestOrchestration:
    def test_first_turn_is_open_and_first_role(self):
        r = _run({
            "question": "Is X true?",
            "roles":    ["analyst", "critic", "lab_synthesizer"],
            "transcript": [],
        })
        assert r.output["next_speaker"] == "analyst"
        assert r.output["next_turn_kind"] == "open"
        assert r.output["turn_index"] == 0
        assert r.output["terminate"] is False

    def test_round_robin_cycles_through_roles(self):
        r = _run({
            "question": "Q?",
            "roles":    ["a", "b", "c"],
            "transcript": [
                {"speaker": "a", "turn_kind": "open"},
                {"speaker": "b", "turn_kind": "counter"},
            ],
            "strategy": "round_robin",
        })
        assert r.output["next_speaker"] == "c"
        assert r.output["turn_index"] == 2

    def test_round_robin_wraps(self):
        r = _run({
            "question": "Q?",
            "roles":    ["a", "b"],
            "transcript": [
                {"speaker": "a"},
                {"speaker": "b"},
            ],
            "strategy": "round_robin",
        })
        assert r.output["next_speaker"] == "a"

    def test_max_turns_terminates(self):
        r = _run({
            "question": "Q?",
            "roles":    ["a", "b"],
            "transcript": [{"speaker": "a"}, {"speaker": "b"}],
            "max_turns": 2,
        })
        assert r.output["terminate"] is True
        assert r.output["terminate_reason"] == "max_turns"
        assert r.output["next_turn_kind"] == "close"

    def test_operator_close_terminates(self):
        r = _run({
            "question": "Q?",
            "roles":    ["a", "b"],
            "transcript": [{"speaker": "a"}],
            "operator_signaled_close": True,
        })
        assert r.output["terminate"] is True
        assert r.output["terminate_reason"] == "operator_signal"

    def test_synthesizer_role_closes_when_terminating(self):
        r = _run({
            "question": "Q?",
            "roles":    ["analyst", "critic", "lab_synthesizer"],
            "transcript": [
                {"speaker": "analyst"},
                {"speaker": "critic"},
            ],
            "max_turns": 2,
        })
        # When terminating, prefer the synthesizer-ish role to close
        assert r.output["next_speaker"] == "lab_synthesizer"

    def test_critic_speaker_labeled_counter(self):
        r = _run({
            "question": "Q?",
            "roles":    ["analyst", "critic", "lab_synthesizer"],
            "transcript": [{"speaker": "analyst", "turn_kind": "open"}],
        })
        # turn_index=1, round_robin -> critic
        assert r.output["next_speaker"] == "critic"
        assert r.output["next_turn_kind"] == "counter"

    def test_synthesizer_speaker_labeled_synthesize(self):
        r = _run({
            "question": "Q?",
            "roles":    ["analyst", "critic", "lab_synthesizer"],
            "transcript": [
                {"speaker": "analyst", "turn_kind": "open"},
                {"speaker": "critic", "turn_kind": "counter"},
            ],
        })
        # turn_index=2 -> lab_synthesizer
        assert r.output["next_speaker"] == "lab_synthesizer"
        assert r.output["next_turn_kind"] == "synthesize"

    def test_turn_counts_aggregated(self):
        r = _run({
            "question": "Q?",
            "roles":    ["a", "b", "c"],
            "transcript": [
                {"speaker": "a"},
                {"speaker": "a"},
                {"speaker": "b"},
            ],
        })
        assert r.output["turn_counts"] == {"a": 2, "b": 1, "c": 0}

    def test_demand_driven_picks_least_spoken(self):
        r = _run({
            "question": "Q?",
            "roles":    ["a", "b", "c"],
            "transcript": [
                {"speaker": "a"},
                {"speaker": "a"},
                {"speaker": "b"},
            ],
            "strategy": "demand_driven",
        })
        # c has spoken 0; should be picked
        assert r.output["next_speaker"] == "c"

    def test_demand_driven_tiebreak_by_declaration_order(self):
        r = _run({
            "question": "Q?",
            "roles":    ["a", "b", "c"],
            "transcript": [],
            "strategy": "demand_driven",
        })
        # All tied at 0; "a" wins by order
        assert r.output["next_speaker"] == "a"

    def test_deterministic(self):
        args = {
            "question": "Q?",
            "roles":    ["a", "b", "c"],
            "transcript": [{"speaker": "a"}],
            "strategy": "round_robin",
        }
        r1 = _run(args)
        r2 = _run(args)
        assert r1.output["next_speaker"] == r2.output["next_speaker"]
        assert r1.output["next_turn_kind"] == r2.output["next_turn_kind"]

    def test_rebut_after_counter(self):
        r = _run({
            "question": "Q?",
            "roles":    ["analyst", "scribe"],  # no critic/synth in role names
            "transcript": [
                {"speaker": "analyst", "turn_kind": "open"},
                {"speaker": "scribe", "turn_kind": "counter"},
            ],
        })
        # Next speaker is analyst again (round_robin); previous was counter
        # so the turn kind should be rebut
        assert r.output["next_speaker"] == "analyst"
        assert r.output["next_turn_kind"] == "rebut"

    def test_structured_strategy_cycles(self):
        r = _run({
            "question": "Q?",
            "roles":    ["analyst", "critic", "lab_synthesizer"],
            "transcript": [],
            "strategy": "structured",
        })
        assert r.output["next_speaker"] == "analyst"
        assert r.output["next_turn_kind"] == "open"

    def test_close_speaker_falls_back_to_first_role(self):
        # When no synth/synthesizer role present, last_speaker is
        # used; if transcript empty + termination, fallback to first
        r = _run({
            "question": "Q?",
            "roles":    ["alpha", "beta"],
            "transcript": [],
            "operator_signaled_close": True,
        })
        # No synth-ish role + empty transcript -> fallback to first
        assert r.output["next_speaker"] == "alpha"
        assert r.output["next_turn_kind"] == "close"
