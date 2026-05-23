"""Tests for ADR-0088 Phase C — format_adapt.v1 builtin tool."""
from __future__ import annotations

import asyncio

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.format_adapt import FormatAdaptTool


def _ctx():
    return ToolContext(
        instance_id="editor_test",
        agent_dna="a" * 12,
        role="editor",
        genre="guardian",
        session_id=None,
    )


def _run(args):
    return asyncio.run(FormatAdaptTool().execute(args, _ctx()))


DRAFT_SHORT = (
    "The morning was cold; the lake was still. I walked along the shore, "
    "watching the light shift across the water. Sometimes the right "
    "thing is to wait — to let the moment arrive on its own.\n\n"
    "Later, I wrote a few notes. They were short and direct."
)
DRAFT_LONG_BLOG = (
    "# Multi-agent governance\n\n"
    "We need durable discipline. The posture model gives us that. "
    "Every agent has a stance; every stance has a ceiling.\n\n"
    "## Why posture matters\n\n"
    "Posture sets the default trust level. YELLOW means the operator "
    "approves every external action; GREEN lets the agent act inside "
    "its kit's ceiling without per-call approval.\n\n"
    "## The audit chain\n\n"
    "The chain is the spine. Every decision lands as a hash-linked "
    "entry. Tampering is detectable.\n\n"
    "## What we built\n\n"
    "We shipped the writer, the researcher, and the steward. The "
    "editor composes the gates. The pilot queues the publish."
) * 3
DRAFT_HUGE = (
    "The story begins with morning light. It bends across the water "
    "in a way I can never quite describe but always remember. "
    "Sometimes the best writing is the writing you do when nothing "
    "is at stake. " * 80
)


class TestValidation:
    def test_draft_required(self):
        with pytest.raises(ToolValidationError, match="draft"):
            FormatAdaptTool().validate(
                {"target_format": "twitter_thread"},
            )

    def test_draft_must_be_nonempty(self):
        with pytest.raises(ToolValidationError, match="draft"):
            FormatAdaptTool().validate(
                {"draft": "   ", "target_format": "blog"},
            )

    def test_draft_too_short(self):
        with pytest.raises(ToolValidationError, match=">= 50"):
            FormatAdaptTool().validate(
                {"draft": "short.", "target_format": "blog"},
            )

    def test_draft_too_long(self):
        with pytest.raises(ToolValidationError, match="200000"):
            FormatAdaptTool().validate(
                {"draft": "x" * 200_001, "target_format": "blog"},
            )

    def test_target_format_required(self):
        with pytest.raises(ToolValidationError, match="target_format"):
            FormatAdaptTool().validate({"draft": DRAFT_SHORT})

    def test_target_format_must_be_valid(self):
        with pytest.raises(ToolValidationError, match="target_format"):
            FormatAdaptTool().validate(
                {"draft": DRAFT_SHORT, "target_format": "podcast"},
            )

    def test_max_tweets_must_be_positive(self):
        with pytest.raises(ToolValidationError, match="max_tweets"):
            FormatAdaptTool().validate(
                {
                    "draft": DRAFT_SHORT,
                    "target_format": "twitter_thread",
                    "max_tweets": 0,
                },
            )

    def test_max_tweets_capped(self):
        with pytest.raises(ToolValidationError, match="50"):
            FormatAdaptTool().validate(
                {
                    "draft": DRAFT_SHORT,
                    "target_format": "twitter_thread",
                    "max_tweets": 51,
                },
            )

    def test_valid_args_accepted(self):
        FormatAdaptTool().validate(
            {"draft": DRAFT_SHORT, "target_format": "twitter_thread"},
        )


class TestTwitterThread:
    def test_short_draft_to_thread(self):
        result = _run(
            {"draft": DRAFT_SHORT, "target_format": "twitter_thread"},
        )
        assert result.output["target_format"] == "twitter_thread"
        assert len(result.output["segments"]) >= 1

    def test_each_segment_under_280(self):
        result = _run(
            {"draft": DRAFT_LONG_BLOG, "target_format": "twitter_thread"},
        )
        for seg in result.output["segments"]:
            assert len(seg) <= 280, f"segment too long: {seg!r}"

    def test_multi_tweet_numbered(self):
        result = _run(
            {"draft": DRAFT_LONG_BLOG, "target_format": "twitter_thread"},
        )
        segs = result.output["segments"]
        if len(segs) > 1:
            assert segs[0].endswith("1/" + str(len(segs)))
            assert segs[-1].endswith(f"{len(segs)}/{len(segs)}")

    def test_huge_draft_triggers_overflow(self):
        result = _run(
            {
                "draft": DRAFT_HUGE,
                "target_format": "twitter_thread",
                "max_tweets": 5,
            },
        )
        assert result.output["metrics"]["overflow"] is True
        assert len(result.output["segments"]) == 5


class TestLinkedInPost:
    def test_short_draft_fits(self):
        result = _run(
            {"draft": DRAFT_SHORT, "target_format": "linkedin_post"},
        )
        assert result.output["metrics"]["overflow"] is False
        assert len(result.output["adapted_text"]) <= 2_500

    def test_huge_draft_truncates(self):
        result = _run(
            {"draft": DRAFT_HUGE, "target_format": "linkedin_post"},
        )
        assert result.output["metrics"]["overflow"] is True
        assert len(result.output["adapted_text"]) <= 2_500

    def test_headers_stripped(self):
        result = _run(
            {"draft": DRAFT_LONG_BLOG, "target_format": "linkedin_post"},
        )
        # leading "# " ATX header should not appear at start
        assert not result.output["adapted_text"].startswith("#")


class TestNewsletter:
    def test_produces_four_segments(self):
        result = _run(
            {"draft": DRAFT_LONG_BLOG, "target_format": "newsletter"},
        )
        assert len(result.output["segments"]) == 4

    def test_segments_labeled(self):
        result = _run(
            {"draft": DRAFT_LONG_BLOG, "target_format": "newsletter"},
        )
        labels = [seg.split("\n", 1)[0] for seg in result.output["segments"]]
        assert labels[0].startswith("Subject:")
        assert labels[1] == "TL;DR"
        assert labels[2] == "Body"
        assert labels[3] == "Asks"

    def test_short_draft_handles_gracefully(self):
        result = _run(
            {"draft": DRAFT_SHORT, "target_format": "newsletter"},
        )
        assert len(result.output["segments"]) == 4


class TestBlog:
    def test_preserves_structure(self):
        result = _run(
            {"draft": DRAFT_LONG_BLOG, "target_format": "blog"},
        )
        # blog adaptation should retain header markers
        assert "#" in result.output["adapted_text"]

    def test_no_overflow_on_blog(self):
        result = _run(
            {"draft": DRAFT_LONG_BLOG, "target_format": "blog"},
        )
        assert result.output["metrics"]["overflow"] is False

    def test_segments_split_at_headers(self):
        result = _run(
            {"draft": DRAFT_LONG_BLOG, "target_format": "blog"},
        )
        assert len(result.output["segments"]) >= 2


class TestCommon:
    def test_deterministic_same_input(self):
        result1 = _run(
            {"draft": DRAFT_LONG_BLOG, "target_format": "twitter_thread"},
        )
        result2 = _run(
            {"draft": DRAFT_LONG_BLOG, "target_format": "twitter_thread"},
        )
        assert result1.output["segments"] == result2.output["segments"]
        assert (
            result1.output["metrics"]["segment_count"]
            == result2.output["metrics"]["segment_count"]
        )

    def test_metadata_includes_target_format(self):
        result = _run(
            {"draft": DRAFT_SHORT, "target_format": "newsletter"},
        )
        assert result.metadata["target_format"] == "newsletter"

    def test_side_effect_summary_present(self):
        result = _run(
            {"draft": DRAFT_SHORT, "target_format": "blog"},
        )
        assert "adapted" in result.side_effect_summary

    def test_total_chars_and_words_reflect_output(self):
        result = _run(
            {"draft": DRAFT_LONG_BLOG, "target_format": "newsletter"},
        )
        assert result.output["metrics"]["total_chars"] > 0
        assert result.output["metrics"]["total_words"] > 0
