"""Tests for ADR-0088 Phase B — voice_match_check.v1 builtin tool."""
from __future__ import annotations

import asyncio

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.voice_profile_build import (
    VoiceProfileBuildTool,
)
from forest_soul_forge.tools.builtin.voice_match_check import (
    VoiceMatchCheckTool,
)


def _ctx():
    return ToolContext(
        instance_id="style_steward_test",
        agent_dna="a" * 12,
        role="style_steward",
        genre="guardian",
        session_id=None,
    )


def _run_match(args):
    return asyncio.run(VoiceMatchCheckTool().execute(args, _ctx()))


def _build_profile(samples: list[str]):
    return asyncio.run(
        VoiceProfileBuildTool().execute({"samples": samples}, _ctx()),
    ).output


SAMPLE_ONVOICE = (
    "The morning was cold; the lake was still. I walked along the shore, "
    "watching the light shift across the water. Sometimes the right "
    "thing is to wait — to let the moment arrive on its own. "
    "Perhaps that was the lesson. Maybe it always is.\n\n"
    "Later, I wrote a few notes. They were short and direct."
)
SAMPLE_ALT = (
    "We built the thing because it had to exist. The cost was real; "
    "the alternative was worse. I would argue that the right framing "
    "is not whether we should — but how. Possibly that's the whole "
    "discipline.\n\n"
    "Later, the team shipped. We learned what we needed."
)
DRAFT_ONVOICE = (
    "The afternoon came; the light was thin. I sat at the kitchen "
    "table, watching shadows move across the floor. Perhaps the "
    "answer was obvious. Sometimes it always is — even when we "
    "pretend otherwise. The day went on; we did the work.\n\n"
    "Later, I noted what mattered. The list was short."
)
DRAFT_VERY_OFF = (
    "Behold the magnificent vista! Verily, the empyrean was a "
    "tessellation of marvels most extraordinary, manifesting "
    "in coruscating splendor across an azimuthal panorama "
    "circumscribed by chromatic transcendence, wherein the "
    "phenomenological hermeneutics of luminescent atmospheric "
    "stratification engendered a profound semiotic perplexity "
    "that engulfed the contemplative observer in cascading "
    "epistemological permutations of unprecedented magnitude."
)


def _profile():
    return _build_profile([SAMPLE_ONVOICE, SAMPLE_ALT])


class TestValidation:
    def test_draft_required(self):
        with pytest.raises(ToolValidationError, match="draft"):
            VoiceMatchCheckTool().validate({"profile": _profile()})

    def test_draft_must_be_nonempty(self):
        with pytest.raises(ToolValidationError, match="draft"):
            VoiceMatchCheckTool().validate(
                {"draft": "   ", "profile": _profile()},
            )

    def test_draft_too_long(self):
        with pytest.raises(ToolValidationError, match="200000"):
            VoiceMatchCheckTool().validate(
                {"draft": "x" * 200_001, "profile": _profile()},
            )

    def test_profile_required(self):
        with pytest.raises(ToolValidationError, match="profile"):
            VoiceMatchCheckTool().validate({"draft": DRAFT_ONVOICE})

    def test_profile_must_be_dict(self):
        with pytest.raises(ToolValidationError, match="profile"):
            VoiceMatchCheckTool().validate(
                {"draft": DRAFT_ONVOICE, "profile": "no"},
            )

    def test_profile_missing_feature_rejected(self):
        bad = _profile()
        del bad["mean_sentence_length"]
        with pytest.raises(ToolValidationError, match="mean_sentence_length"):
            VoiceMatchCheckTool().validate(
                {"draft": DRAFT_ONVOICE, "profile": bad},
            )

    def test_profile_feature_must_be_number(self):
        bad = _profile()
        bad["mean_sentence_length"] = "not a number"
        with pytest.raises(ToolValidationError, match="number"):
            VoiceMatchCheckTool().validate(
                {"draft": DRAFT_ONVOICE, "profile": bad},
            )

    def test_valid_args_accepted(self):
        VoiceMatchCheckTool().validate(
            {"draft": DRAFT_ONVOICE, "profile": _profile()},
        )


class TestExecute:
    def test_returns_required_keys(self):
        result = _run_match(
            {"draft": DRAFT_ONVOICE, "profile": _profile()},
        )
        for key in [
            "generated_at", "composite_score", "verdict",
            "per_feature", "flagged_features", "spans",
            "draft_word_count",
        ]:
            assert key in result.output, f"missing {key!r}"

    def test_onvoice_draft_lands_match_or_minor(self):
        result = _run_match(
            {"draft": DRAFT_ONVOICE, "profile": _profile()},
        )
        # onvoice draft should NOT be drift_major
        assert result.output["verdict"] in {"match", "drift_minor"}

    def test_offvoice_draft_lands_drift_major(self):
        result = _run_match(
            {"draft": DRAFT_VERY_OFF, "profile": _profile()},
        )
        assert result.output["verdict"] == "drift_major"
        assert len(result.output["flagged_features"]) >= 1

    def test_composite_score_in_zero_to_one(self):
        result = _run_match(
            {"draft": DRAFT_ONVOICE, "profile": _profile()},
        )
        assert 0.0 <= result.output["composite_score"] <= 1.0

    def test_per_feature_has_required_fields(self):
        result = _run_match(
            {"draft": DRAFT_ONVOICE, "profile": _profile()},
        )
        for feature, entry in result.output["per_feature"].items():
            for key in ("draft_value", "profile_value", "delta", "flag", "weight"):
                assert key in entry, f"{feature} missing {key!r}"

    def test_per_feature_flag_values_valid(self):
        result = _run_match(
            {"draft": DRAFT_VERY_OFF, "profile": _profile()},
        )
        for entry in result.output["per_feature"].values():
            assert entry["flag"] in {"match", "drift_minor", "drift_major"}

    def test_spans_capped_at_three(self):
        result = _run_match(
            {"draft": DRAFT_VERY_OFF, "profile": _profile()},
        )
        assert len(result.output["spans"]) <= 3

    def test_spans_include_feature_excerpt_note(self):
        result = _run_match(
            {"draft": DRAFT_VERY_OFF, "profile": _profile()},
        )
        if result.output["spans"]:
            for span in result.output["spans"]:
                assert "feature" in span
                assert "excerpt" in span
                assert "note" in span

    def test_draft_word_count_returned(self):
        result = _run_match(
            {"draft": DRAFT_ONVOICE, "profile": _profile()},
        )
        assert result.output["draft_word_count"] > 0

    def test_short_draft_rejected(self):
        with pytest.raises(ToolValidationError, match="word count"):
            _run_match(
                {"draft": "Only a few words here.", "profile": _profile()},
            )

    def test_self_match_high_score(self):
        # Building a profile from the draft itself should produce a
        # very high match score against that same draft.
        prof = _build_profile([DRAFT_ONVOICE])
        result = _run_match({"draft": DRAFT_ONVOICE, "profile": prof})
        assert result.output["composite_score"] >= 0.9
        assert result.output["verdict"] == "match"

    def test_metadata_includes_verdict_and_score(self):
        result = _run_match(
            {"draft": DRAFT_ONVOICE, "profile": _profile()},
        )
        assert result.metadata["verdict"] == result.output["verdict"]
        assert (
            result.metadata["composite_score"]
            == result.output["composite_score"]
        )

    def test_deterministic_same_input_same_output(self):
        prof = _profile()
        result1 = _run_match({"draft": DRAFT_ONVOICE, "profile": prof})
        result2 = _run_match({"draft": DRAFT_ONVOICE, "profile": prof})
        assert (
            result1.output["composite_score"]
            == result2.output["composite_score"]
        )
        assert (
            result1.output["verdict"] == result2.output["verdict"]
        )
        assert (
            result1.output["flagged_features"]
            == result2.output["flagged_features"]
        )

    def test_side_effect_summary_present(self):
        result = _run_match(
            {"draft": DRAFT_ONVOICE, "profile": _profile()},
        )
        assert "voice match" in result.side_effect_summary
