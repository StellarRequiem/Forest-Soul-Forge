"""Tests for ADR-0089 Phase D — spaced_repetition_schedule.v1 builtin tool."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.spaced_repetition_schedule import (
    SpacedRepetitionScheduleTool,
)


def _ctx():
    return ToolContext(
        instance_id="spaced_repetition_pilot_test",
        agent_dna="a" * 12,
        role="spaced_repetition_pilot",
        genre="actuator",
        session_id=None,
    )


def _run(args):
    return asyncio.run(SpacedRepetitionScheduleTool().execute(args, _ctx()))


def _good_args(tmp_path: Path, **overrides):
    base = {
        "topic_slug": "diffusion-forward",
        "quality": 4,
        "reviewed_at": "2026-05-23T10:00:00Z",
        "queue_path": str(tmp_path / "review_queue.jsonl"),
    }
    base.update(overrides)
    return base


class TestValidation:
    def test_topic_slug_required(self):
        with pytest.raises(ToolValidationError, match="topic_slug"):
            SpacedRepetitionScheduleTool().validate({"quality": 4})

    def test_quality_required(self):
        with pytest.raises(ToolValidationError, match="quality"):
            SpacedRepetitionScheduleTool().validate({"topic_slug": "x"})

    def test_quality_out_of_range_high(self):
        with pytest.raises(ToolValidationError, match="quality"):
            SpacedRepetitionScheduleTool().validate(
                {"topic_slug": "x", "quality": 6}
            )

    def test_quality_out_of_range_negative(self):
        with pytest.raises(ToolValidationError, match="quality"):
            SpacedRepetitionScheduleTool().validate(
                {"topic_slug": "x", "quality": -1}
            )

    def test_quality_must_be_int(self):
        with pytest.raises(ToolValidationError, match="quality"):
            SpacedRepetitionScheduleTool().validate(
                {"topic_slug": "x", "quality": 4.5}
            )

    def test_prior_repetition_must_be_nonneg(self):
        with pytest.raises(ToolValidationError, match="prior_repetition"):
            SpacedRepetitionScheduleTool().validate(
                {"topic_slug": "x", "quality": 4, "prior_repetition": -2}
            )

    def test_prior_easiness_below_min(self):
        with pytest.raises(ToolValidationError, match="prior_easiness"):
            SpacedRepetitionScheduleTool().validate(
                {"topic_slug": "x", "quality": 4, "prior_easiness": 1.0}
            )

    def test_prior_interval_must_be_nonneg(self):
        with pytest.raises(ToolValidationError, match="prior_interval"):
            SpacedRepetitionScheduleTool().validate(
                {"topic_slug": "x", "quality": 4, "prior_interval_days": -1}
            )

    def test_reviewed_at_unparseable(self):
        with pytest.raises(ToolValidationError, match="reviewed_at"):
            SpacedRepetitionScheduleTool().validate(
                {"topic_slug": "x", "quality": 4, "reviewed_at": "not-iso"}
            )


class TestSM2:
    def test_first_review_q5_interval_1(self, tmp_path: Path):
        result = _run(_good_args(tmp_path, quality=5))
        # n=0 prior → n=1 → I=1
        assert result.output["next_repetition"] == 1
        assert result.output["next_interval_days"] == 1

    def test_second_review_q5_interval_6(self, tmp_path: Path):
        result = _run(_good_args(
            tmp_path, quality=5,
            prior_repetition=1, prior_interval_days=1,
        ))
        # n=1 → n=2 → I=6
        assert result.output["next_repetition"] == 2
        assert result.output["next_interval_days"] == 6

    def test_third_review_uses_ef(self, tmp_path: Path):
        result = _run(_good_args(
            tmp_path, quality=5,
            prior_repetition=2,
            prior_interval_days=6,
            prior_easiness=2.5,
        ))
        # n=2 → n=3 → I = round(6 * EF') where EF' ~= 2.6 → I = 16
        assert result.output["next_repetition"] == 3
        assert result.output["next_interval_days"] >= 15

    def test_failed_review_resets_repetition(self, tmp_path: Path):
        result = _run(_good_args(
            tmp_path, quality=2,
            prior_repetition=5, prior_interval_days=30,
        ))
        # q < 3 → reset to n=0, I=1
        assert result.output["next_repetition"] == 0
        assert result.output["next_interval_days"] == 1

    def test_easiness_increases_on_perfect_recall(self, tmp_path: Path):
        result = _run(_good_args(
            tmp_path, quality=5,
            prior_easiness=2.5,
        ))
        # EF' = 2.5 + 0.1 - 0 * (...) = 2.6
        assert result.output["next_easiness"] > 2.5

    def test_easiness_decreases_on_low_grade(self, tmp_path: Path):
        result = _run(_good_args(
            tmp_path, quality=3,
            prior_easiness=2.5,
        ))
        # EF' = 2.5 + 0.1 - 2 * (0.08 + 2 * 0.02) = 2.5 - 0.14
        assert result.output["next_easiness"] < 2.5

    def test_easiness_clamped_at_min(self, tmp_path: Path):
        # Start low; very-low grade; clamp should hold
        result = _run(_good_args(
            tmp_path, quality=0,
            prior_easiness=1.3,
        ))
        assert result.output["next_easiness"] >= 1.3


class TestQueueWrite:
    def test_writes_jsonl_record(self, tmp_path: Path):
        result = _run(_good_args(tmp_path))
        queue = Path(result.output["queue_path"])
        assert queue.exists()
        rec = json.loads(queue.read_text())
        assert rec["topic_slug"] == "diffusion-forward"
        assert rec["quality"] == 4
        assert rec["schedule_id"].startswith("sr_")

    def test_appends_rather_than_overwrites(self, tmp_path: Path):
        _run(_good_args(tmp_path))
        _run(_good_args(tmp_path, quality=3))
        queue_text = Path(_good_args(tmp_path)["queue_path"]).read_text()
        assert len(queue_text.splitlines()) == 2

    def test_attestor_from_ctx(self, tmp_path: Path):
        result = _run(_good_args(tmp_path))
        rec = json.loads(Path(result.output["queue_path"]).read_text())
        assert rec["attestor"] == "spaced_repetition_pilot_test"

    def test_source_score_id_recorded(self, tmp_path: Path):
        args = _good_args(tmp_path)
        args["source_score_id"] = "score_xyz"
        result = _run(args)
        rec = json.loads(Path(result.output["queue_path"]).read_text())
        assert rec["source_score_id"] == "score_xyz"

    def test_fire_at_offset_from_reviewed_at(self, tmp_path: Path):
        result = _run(_good_args(tmp_path, quality=5))
        # Should fire 1 day after reviewed_at
        from datetime import datetime
        reviewed = datetime.fromisoformat(
            result.output["reviewed_at"].replace("Z", "+00:00")
        )
        fire = datetime.fromisoformat(
            result.output["fire_at"]
        )
        # Both will have tz; first review interval = 1 day
        delta_days = (fire - reviewed).days
        assert delta_days == 1


class TestMetadata:
    def test_metadata_carries_id_and_interval(self, tmp_path: Path):
        result = _run(_good_args(tmp_path))
        assert result.metadata["schedule_id"].startswith("sr_")
        assert result.metadata["topic_slug"] == "diffusion-forward"
        assert result.metadata["next_interval_days"] >= 1

    def test_side_effect_summary_mentions_slug(self, tmp_path: Path):
        result = _run(_good_args(tmp_path, quality=5))
        assert "diffusion-forward" in result.side_effect_summary
        assert "1d" in result.side_effect_summary


class TestSideEffects:
    def test_side_effects_filesystem(self):
        assert SpacedRepetitionScheduleTool.side_effects == "filesystem"

    def test_version_is_bare_string(self):
        assert SpacedRepetitionScheduleTool.version == "1"
