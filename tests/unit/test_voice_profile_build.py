"""Tests for ADR-0088 Phase B — voice_profile_build.v1 builtin tool."""
from __future__ import annotations

import asyncio

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.voice_profile_build import (
    VoiceProfileBuildTool,
)


def _ctx():
    return ToolContext(
        instance_id="style_steward_test",
        agent_dna="a" * 12,
        role="style_steward",
        genre="guardian",
        session_id=None,
    )


def _run(args):
    return asyncio.run(VoiceProfileBuildTool().execute(args, _ctx()))


SAMPLE_A = (
    "The morning was cold; the lake was still. I walked along the shore, "
    "watching the light shift across the water. Sometimes the right "
    "thing is to wait — to let the moment arrive on its own. "
    "Perhaps that was the lesson. Maybe it always is.\n\n"
    "Later, I wrote a few notes. They were short and direct."
)
SAMPLE_B = (
    "We built the thing because it had to exist. The cost was real; "
    "the alternative was worse. I would argue that the right framing "
    "is not whether we should — but how. Possibly that's the whole "
    "discipline.\n\n"
    "Later, the team shipped. We learned what we needed."
)


class TestValidation:
    def test_samples_required(self):
        with pytest.raises(ToolValidationError, match="samples"):
            VoiceProfileBuildTool().validate({})

    def test_samples_must_be_list(self):
        with pytest.raises(ToolValidationError, match="samples"):
            VoiceProfileBuildTool().validate({"samples": "not a list"})

    def test_samples_cannot_be_empty(self):
        with pytest.raises(ToolValidationError, match="at least one"):
            VoiceProfileBuildTool().validate({"samples": []})

    def test_samples_count_capped(self):
        with pytest.raises(ToolValidationError, match="50"):
            VoiceProfileBuildTool().validate(
                {"samples": [SAMPLE_A] * 51},
            )

    def test_sample_must_be_string(self):
        with pytest.raises(ToolValidationError, match="must be a string"):
            VoiceProfileBuildTool().validate({"samples": [123]})

    def test_sample_too_long(self):
        with pytest.raises(ToolValidationError, match="100000"):
            VoiceProfileBuildTool().validate(
                {"samples": ["x" * 100_001]},
            )

    def test_profile_label_must_be_string(self):
        with pytest.raises(ToolValidationError, match="profile_label"):
            VoiceProfileBuildTool().validate(
                {"samples": [SAMPLE_A], "profile_label": 42},
            )

    def test_valid_args_accepted(self):
        VoiceProfileBuildTool().validate(
            {"samples": [SAMPLE_A, SAMPLE_B], "profile_label": "blog"},
        )


class TestExecute:
    def test_returns_required_features(self):
        result = _run({"samples": [SAMPLE_A, SAMPLE_B]})
        body = result.output
        for key in [
            "mean_sentence_length", "stdev_sentence_length",
            "mean_word_length", "type_token_ratio",
            "avg_paragraph_length", "comma_per_sentence",
            "semicolon_per_1k", "emdash_per_1k",
            "hedging_per_1k", "first_person_per_1k",
            "top_function_words", "sample_count", "total_words",
            "generated_at", "profile_label",
        ]:
            assert key in body, f"missing feature {key!r}"

    def test_sample_count_reflects_input(self):
        result = _run({"samples": [SAMPLE_A, SAMPLE_B]})
        assert result.output["sample_count"] == 2

    def test_total_words_positive(self):
        result = _run({"samples": [SAMPLE_A]})
        assert result.output["total_words"] >= 50

    def test_too_few_words_rejected(self):
        with pytest.raises(ToolValidationError, match="50"):
            _run({"samples": ["hi there friend."]})

    def test_profile_label_round_trips(self):
        result = _run(
            {"samples": [SAMPLE_A, SAMPLE_B], "profile_label": "blog_set"},
        )
        assert result.output["profile_label"] == "blog_set"

    def test_hedging_density_detects_hedging_text(self):
        hedging_heavy = (
            "Perhaps it is true. Maybe the answer matters. Possibly "
            "the right approach is somewhat slower. I might be wrong. "
            "Sometimes the obvious thing is approximately correct. " * 4
        )
        result = _run({"samples": [hedging_heavy]})
        assert result.output["hedging_per_1k"] > 30.0

    def test_first_person_density_detects_first_person(self):
        first_person_heavy = (
            "I went to the store. I bought groceries. My list was short. "
            "We met at home. Our day was quiet. I cooked while we talked. "
            "I think it mattered. " * 4
        )
        result = _run({"samples": [first_person_heavy]})
        assert result.output["first_person_per_1k"] > 80.0

    def test_semicolon_density_captured(self):
        semi_heavy = (
            "First; then second; then third. The cost was real; the "
            "alternative was worse; we shipped anyway. Sometimes the "
            "right framing matters; sometimes it doesn't. " * 4
        )
        result = _run({"samples": [semi_heavy]})
        assert result.output["semicolon_per_1k"] > 30.0

    def test_top_function_words_capped_at_20(self):
        result = _run({"samples": [SAMPLE_A, SAMPLE_B]})
        assert len(result.output["top_function_words"]) <= 20

    def test_deterministic_same_input_same_output(self):
        result1 = _run({"samples": [SAMPLE_A, SAMPLE_B]})
        result2 = _run({"samples": [SAMPLE_A, SAMPLE_B]})
        # all numeric features should match exactly
        for key in [
            "mean_sentence_length", "stdev_sentence_length",
            "mean_word_length", "type_token_ratio",
            "comma_per_sentence", "semicolon_per_1k",
            "emdash_per_1k", "hedging_per_1k",
            "first_person_per_1k", "total_words", "sample_count",
        ]:
            assert result1.output[key] == result2.output[key]

    def test_metadata_includes_sample_count(self):
        result = _run({"samples": [SAMPLE_A, SAMPLE_B]})
        assert result.metadata["sample_count"] == 2
        assert result.metadata["total_words"] == result.output["total_words"]

    def test_side_effect_summary_present(self):
        result = _run({"samples": [SAMPLE_A]})
        assert "voice profile" in result.side_effect_summary

    def test_emdash_detected(self):
        emdash_heavy = (
            "The morning came — and went. The light shifted — slowly. "
            "It mattered — at least to me. " * 8
        )
        result = _run({"samples": [emdash_heavy]})
        assert result.output["emdash_per_1k"] > 30.0
