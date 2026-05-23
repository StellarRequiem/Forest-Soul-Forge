"""Tests for ADR-0089 Phase B — assessment_score.v1 builtin tool."""
from __future__ import annotations

import asyncio

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.assessment_score import (
    AssessmentScoreTool,
)


def _ctx():
    return ToolContext(
        instance_id="assessor_test",
        agent_dna="a" * 12,
        role="assessor",
        genre="guardian",
        session_id=None,
    )


def _run(args):
    return asyncio.run(AssessmentScoreTool().execute(args, _ctx()))


class TestValidation:
    def test_item_id_required(self):
        with pytest.raises(ToolValidationError, match="item_id"):
            AssessmentScoreTool().validate({"topic_slug": "x", "response": "y"})

    def test_topic_slug_required(self):
        with pytest.raises(ToolValidationError, match="topic_slug"):
            AssessmentScoreTool().validate({"item_id": "i", "response": "y"})

    def test_response_must_be_string(self):
        with pytest.raises(ToolValidationError, match="response"):
            AssessmentScoreTool().validate(
                {"item_id": "i", "topic_slug": "x", "response": 42}
            )

    def test_ground_truth_must_be_list(self):
        with pytest.raises(ToolValidationError, match="ground_truth"):
            AssessmentScoreTool().validate(
                {
                    "item_id": "i", "topic_slug": "x", "response": "y",
                    "ground_truth_answers": "not a list",
                }
            )

    def test_threshold_out_of_range(self):
        with pytest.raises(ToolValidationError, match="threshold"):
            AssessmentScoreTool().validate(
                {
                    "item_id": "i", "topic_slug": "x", "response": "y",
                    "full_credit_threshold": 1.5,
                }
            )


class TestScoring:
    def test_strict_match_is_correct(self):
        result = _run(
            {
                "item_id": "i", "topic_slug": "x",
                "response": "The forward process adds noise",
                "ground_truth_answers": ["The forward process adds noise"],
            }
        )
        assert result.output["verdict"] == "correct"
        assert result.output["score"] == 1.0
        assert result.output["breakdown"]["strict_match"] is True

    def test_strict_match_case_insensitive(self):
        result = _run(
            {
                "item_id": "i", "topic_slug": "x",
                "response": "THE FORWARD PROCESS ADDS NOISE",
                "ground_truth_answers": ["the forward process adds noise"],
            }
        )
        assert result.output["verdict"] == "correct"
        assert result.output["score"] == 1.0

    def test_no_overlap_incorrect(self):
        result = _run(
            {
                "item_id": "i", "topic_slug": "x",
                "response": "completely unrelated answer",
                "ground_truth_answers": ["the forward process adds noise"],
            }
        )
        assert result.output["verdict"] == "incorrect"
        assert result.output["score"] < 0.4

    def test_partial_overlap(self):
        # ~40% overlap should land in partial range
        result = _run(
            {
                "item_id": "i", "topic_slug": "x",
                "response": "forward process noise",
                "ground_truth_answers": ["the forward process adds noise gradually"],
                "partial_credit_threshold": 0.30,
                "full_credit_threshold": 0.80,
            }
        )
        assert result.output["verdict"] in ("partial", "correct")
        assert result.output["needs_rubric"] in (True, False)

    def test_deferred_when_no_ground_truth(self):
        result = _run(
            {
                "item_id": "i", "topic_slug": "x",
                "response": "anything",
            }
        )
        assert result.output["verdict"] == "deferred"
        assert result.output["needs_rubric"] is True
        assert result.output["score"] == 0.0

    def test_lexical_overlap_recorded(self):
        result = _run(
            {
                "item_id": "i", "topic_slug": "x",
                "response": "noise gradually",
                "ground_truth_answers": ["noise gradually added"],
            }
        )
        assert 0.0 < result.output["breakdown"]["lexical_overlap"] <= 1.0

    def test_best_match_index_recorded(self):
        result = _run(
            {
                "item_id": "i", "topic_slug": "x",
                "response": "forward process noise",
                "ground_truth_answers": [
                    "absolutely irrelevant string",
                    "forward process noise added",
                ],
            }
        )
        assert result.output["breakdown"]["best_match_index"] == 1

    def test_response_tokens_counted(self):
        result = _run(
            {
                "item_id": "i", "topic_slug": "x",
                "response": "one two three four",
                "ground_truth_answers": ["one two three four"],
            }
        )
        assert result.output["breakdown"]["response_tokens"] == 4


class TestMetadata:
    def test_metadata_includes_verdict_and_score(self):
        result = _run(
            {
                "item_id": "abc", "topic_slug": "x",
                "response": "y",
                "ground_truth_answers": ["y"],
            }
        )
        assert result.metadata["item_id"] == "abc"
        assert result.metadata["verdict"] == "correct"
        assert result.metadata["score"] == 1.0

    def test_side_effect_summary_mentions_verdict(self):
        result = _run(
            {
                "item_id": "abc", "topic_slug": "x",
                "response": "y", "ground_truth_answers": ["y"],
            }
        )
        assert "correct" in result.side_effect_summary

    def test_rationale_human_readable(self):
        result = _run(
            {
                "item_id": "abc", "topic_slug": "x",
                "response": "y", "ground_truth_answers": ["y"],
            }
        )
        assert "match" in result.output["rationale"].lower()

    def test_thresholds_applied(self):
        # Same data, different thresholds → different verdict
        common = {
            "item_id": "i", "topic_slug": "x",
            "response": "alpha beta",
            "ground_truth_answers": ["alpha beta gamma delta"],
        }
        strict = _run({**common, "full_credit_threshold": 0.99,
                       "partial_credit_threshold": 0.10})
        loose = _run({**common, "full_credit_threshold": 0.20,
                      "partial_credit_threshold": 0.10})
        assert strict.output["verdict"] in ("partial", "incorrect")
        assert loose.output["verdict"] == "correct"
