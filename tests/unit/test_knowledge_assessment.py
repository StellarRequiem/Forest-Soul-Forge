"""Tests for ADR-0089 Phase B — knowledge_assessment.v1 builtin tool."""
from __future__ import annotations

import asyncio

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.knowledge_assessment import (
    KnowledgeAssessmentTool,
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
    return asyncio.run(KnowledgeAssessmentTool().execute(args, _ctx()))


class TestValidation:
    def test_topic_slug_required(self):
        with pytest.raises(ToolValidationError, match="topic_slug"):
            KnowledgeAssessmentTool().validate({})

    def test_topic_slug_must_be_non_empty(self):
        with pytest.raises(ToolValidationError, match="topic_slug"):
            KnowledgeAssessmentTool().validate({"topic_slug": "   "})

    def test_invalid_difficulty(self):
        with pytest.raises(ToolValidationError, match="difficulty"):
            KnowledgeAssessmentTool().validate(
                {"topic_slug": "x", "difficulty": "extreme"}
            )

    def test_invalid_kind(self):
        with pytest.raises(ToolValidationError, match="kind"):
            KnowledgeAssessmentTool().validate(
                {"topic_slug": "x", "kind": "essay"}
            )

    def test_seed_must_be_string(self):
        with pytest.raises(ToolValidationError, match="seed"):
            KnowledgeAssessmentTool().validate(
                {"topic_slug": "x", "seed": 42}
            )

    def test_prompt_template_too_long(self):
        with pytest.raises(ToolValidationError, match="prompt_template"):
            KnowledgeAssessmentTool().validate(
                {"topic_slug": "x", "prompt_template": "y" * 5001}
            )

    def test_defaults_validate(self):
        KnowledgeAssessmentTool().validate({"topic_slug": "diffusion"})


class TestExecute:
    def test_default_short_answer_medium(self):
        result = _run({"topic_slug": "diffusion"})
        assert result.output["difficulty"] == "medium"
        assert result.output["kind"] == "short_answer"
        assert result.output["topic_slug"] == "diffusion"
        assert result.output["item_id"].startswith("item_")

    def test_deterministic_item_id(self):
        r1 = _run({"topic_slug": "x", "difficulty": "hard"})
        r2 = _run({"topic_slug": "x", "difficulty": "hard"})
        assert r1.output["item_id"] == r2.output["item_id"]

    def test_different_seed_different_id(self):
        r1 = _run({"topic_slug": "x", "seed": "a"})
        r2 = _run({"topic_slug": "x", "seed": "b"})
        assert r1.output["item_id"] != r2.output["item_id"]

    def test_multiple_choice_structural(self):
        result = _run({"topic_slug": "x", "kind": "multiple_choice"})
        assert result.output["structural"]["options_required"] is True
        assert result.output["structural"]["free_text_allowed"] is False

    def test_short_answer_structural(self):
        result = _run({"topic_slug": "x", "kind": "short_answer"})
        assert result.output["structural"]["options_required"] is False
        assert result.output["structural"]["free_text_allowed"] is True
        assert result.output["structural"]["max_answer_len"] == 500

    def test_explain_structural(self):
        result = _run({"topic_slug": "x", "kind": "explain"})
        assert result.output["structural"]["max_answer_len"] == 3000

    def test_prompt_template_passthrough(self):
        pt = "Explain the forward diffusion process."
        result = _run({"topic_slug": "x", "prompt_template": pt})
        assert result.output["prompt_template"] == pt

    def test_metadata_includes_item_id(self):
        result = _run({"topic_slug": "x"})
        assert result.metadata["item_id"] == result.output["item_id"]

    def test_side_effect_summary_mentions_slug(self):
        result = _run({"topic_slug": "diffusion-models"})
        assert "diffusion-models" in result.side_effect_summary
        assert "item_" in result.side_effect_summary

    def test_all_difficulties_accepted(self):
        for d in ("easy", "medium", "hard"):
            result = _run({"topic_slug": "x", "difficulty": d})
            assert result.output["difficulty"] == d

    def test_seed_default_empty_string_works(self):
        # No seed → still produces a stable id
        r1 = _run({"topic_slug": "x"})
        r2 = _run({"topic_slug": "x"})
        assert r1.output["item_id"] == r2.output["item_id"]

    def test_generated_at_is_iso(self):
        result = _run({"topic_slug": "x"})
        assert "T" in result.output["generated_at"]
        assert result.output["generated_at"].endswith("Z")
