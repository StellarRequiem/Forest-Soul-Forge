"""Tests for ADR-0091 Phase B — comfort_recommend.v1 builtin tool."""
from __future__ import annotations

import asyncio

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.comfort_recommend import (
    ComfortRecommendTool,
)


def _ctx():
    return ToolContext(
        instance_id="comfort_optimizer_test",
        agent_dna="a" * 12,
        role="comfort_optimizer",
        genre="researcher",
        session_id=None,
    )


def _run(args):
    return asyncio.run(ComfortRecommendTool().execute(args, _ctx()))


def _area(slug, **kw):
    base = {"area_slug": slug}
    base.update(kw)
    return base


class TestValidation:
    def test_window_slug_required(self):
        with pytest.raises(ToolValidationError, match="window_slug"):
            ComfortRecommendTool().validate({
                "time_of_day": "evening",
                "areas": [_area("kitchen")],
            })

    def test_time_of_day_required(self):
        with pytest.raises(ToolValidationError, match="time_of_day"):
            ComfortRecommendTool().validate({
                "window_slug": "w1",
                "areas": [_area("kitchen")],
            })

    def test_time_of_day_enum(self):
        with pytest.raises(ToolValidationError, match="time_of_day"):
            ComfortRecommendTool().validate({
                "window_slug": "w1",
                "time_of_day": "afternoon",
                "areas": [_area("kitchen")],
            })

    def test_areas_required(self):
        with pytest.raises(ToolValidationError, match="areas"):
            ComfortRecommendTool().validate({
                "window_slug": "w1",
                "time_of_day": "evening",
            })

    def test_areas_must_be_non_empty(self):
        with pytest.raises(ToolValidationError, match="at least one"):
            ComfortRecommendTool().validate({
                "window_slug": "w1",
                "time_of_day": "evening",
                "areas": [],
            })

    def test_areas_capped(self):
        big = [_area(f"a{i}") for i in range(101)]
        with pytest.raises(ToolValidationError, match="100"):
            ComfortRecommendTool().validate({
                "window_slug": "w1",
                "time_of_day": "evening",
                "areas": big,
            })

    def test_area_slug_unique(self):
        with pytest.raises(ToolValidationError, match="duplicates"):
            ComfortRecommendTool().validate({
                "window_slug": "w1",
                "time_of_day": "evening",
                "areas": [_area("kitchen"), _area("kitchen")],
            })

    def test_brightness_range(self):
        with pytest.raises(ToolValidationError, match="brightness"):
            ComfortRecommendTool().validate({
                "window_slug": "w1",
                "time_of_day": "evening",
                "areas": [_area("kitchen", current_brightness_pct=150)],
            })

    def test_temp_must_be_number(self):
        with pytest.raises(ToolValidationError, match="current_temp_f"):
            ComfortRecommendTool().validate({
                "window_slug": "w1",
                "time_of_day": "evening",
                "areas": [_area("kitchen", current_temp_f="warm")],
            })

    def test_preferences_must_be_dict(self):
        with pytest.raises(ToolValidationError, match="preferences"):
            ComfortRecommendTool().validate({
                "window_slug": "w1",
                "time_of_day": "evening",
                "areas": [_area("kitchen")],
                "preferences": "warm",
            })

    def test_preference_min_le_max(self):
        with pytest.raises(ToolValidationError, match="preferred_temp_min_f"):
            ComfortRecommendTool().validate({
                "window_slug": "w1",
                "time_of_day": "evening",
                "areas": [_area("kitchen")],
                "preferences": {
                    "preferred_temp_min_f": 80,
                    "preferred_temp_max_f": 70,
                },
            })

    def test_vacation_mode_must_be_bool(self):
        with pytest.raises(ToolValidationError, match="vacation_mode"):
            ComfortRecommendTool().validate({
                "window_slug": "w1",
                "time_of_day": "evening",
                "areas": [_area("kitchen")],
                "vacation_mode": "yes",
            })


class TestSceneDimension:
    def test_vacation_mode_recommends_away_when_not_set(self):
        result = _run({
            "window_slug": "w1",
            "time_of_day": "evening",
            "vacation_mode": True,
            "areas": [_area("kitchen", current_scene="cooking")],
        })
        scene = next(
            r for r in result.output["recommendations"]
            if r["dimension"] == "scene"
        )
        assert scene["action_kind"] == "set_scene"
        assert scene["target"] == "away"
        assert scene["priority"] == 1

    def test_vacation_mode_no_action_when_already_away(self):
        result = _run({
            "window_slug": "w1",
            "time_of_day": "evening",
            "vacation_mode": True,
            "areas": [_area("kitchen", current_scene="away")],
        })
        scene = next(
            r for r in result.output["recommendations"]
            if r["dimension"] == "scene"
        )
        assert scene["action_kind"] == "no_action"

    def test_default_scene_when_missing(self):
        result = _run({
            "window_slug": "w1",
            "time_of_day": "evening",
            "areas": [_area("kitchen")],
        })
        scene = next(
            r for r in result.output["recommendations"]
            if r["dimension"] == "scene"
        )
        assert scene["action_kind"] == "set_scene"
        assert scene["target"] == "evening"

    def test_no_scene_action_when_scene_set_and_no_vacation(self):
        result = _run({
            "window_slug": "w1",
            "time_of_day": "evening",
            "areas": [_area("kitchen", current_scene="dinner")],
        })
        scene = next(
            r for r in result.output["recommendations"]
            if r["dimension"] == "scene"
        )
        assert scene["action_kind"] == "no_action"


class TestTemperatureDimension:
    def test_recommends_adjust_when_below_window(self):
        result = _run({
            "window_slug": "w1",
            "time_of_day": "evening",
            "areas": [_area("kitchen", current_temp_f=60, current_scene="x")],
        })
        temp = next(
            r for r in result.output["recommendations"]
            if r["dimension"] == "temperature"
        )
        assert temp["action_kind"] == "adjust_temperature"
        assert temp["target"] == 71.0  # midpoint of [68, 74]

    def test_recommends_adjust_when_above_window(self):
        result = _run({
            "window_slug": "w1",
            "time_of_day": "evening",
            "areas": [_area("kitchen", current_temp_f=80, current_scene="x")],
        })
        temp = next(
            r for r in result.output["recommendations"]
            if r["dimension"] == "temperature"
        )
        assert temp["action_kind"] == "adjust_temperature"

    def test_no_action_within_window(self):
        result = _run({
            "window_slug": "w1",
            "time_of_day": "evening",
            "areas": [_area("kitchen", current_temp_f=71, current_scene="x")],
        })
        temp = next(
            r for r in result.output["recommendations"]
            if r["dimension"] == "temperature"
        )
        assert temp["action_kind"] == "no_action"

    def test_within_tolerance_no_action(self):
        # current 75 is outside [68,74] but within delta=2
        result = _run({
            "window_slug": "w1",
            "time_of_day": "evening",
            "areas": [_area("kitchen", current_temp_f=75, current_scene="x")],
        })
        temp = next(
            r for r in result.output["recommendations"]
            if r["dimension"] == "temperature"
        )
        assert temp["action_kind"] == "no_action"
        assert "within tolerance" in temp["rationale"]

    def test_no_action_when_temp_missing(self):
        result = _run({
            "window_slug": "w1",
            "time_of_day": "evening",
            "areas": [_area("kitchen", current_scene="x")],
        })
        temp = next(
            r for r in result.output["recommendations"]
            if r["dimension"] == "temperature"
        )
        assert temp["action_kind"] == "no_action"

    def test_custom_preference_window(self):
        result = _run({
            "window_slug": "w1",
            "time_of_day": "evening",
            "areas": [_area("kitchen", current_temp_f=80, current_scene="x")],
            "preferences": {
                "preferred_temp_min_f": 78,
                "preferred_temp_max_f": 82,
            },
        })
        temp = next(
            r for r in result.output["recommendations"]
            if r["dimension"] == "temperature"
        )
        assert temp["action_kind"] == "no_action"


class TestLightingDimension:
    def test_dim_lights_when_evening_and_bright(self):
        result = _run({
            "window_slug": "w1",
            "time_of_day": "evening",
            "areas": [_area(
                "living", current_brightness_pct=80, current_scene="x"
            )],
        })
        lighting = next(
            r for r in result.output["recommendations"]
            if r["dimension"] == "lighting"
        )
        assert lighting["action_kind"] == "dim_lights"
        assert lighting["target"] == 30.0

    def test_no_dim_when_evening_and_already_dim(self):
        result = _run({
            "window_slug": "w1",
            "time_of_day": "evening",
            "areas": [_area(
                "living", current_brightness_pct=20, current_scene="x"
            )],
        })
        lighting = next(
            r for r in result.output["recommendations"]
            if r["dimension"] == "lighting"
        )
        assert lighting["action_kind"] == "no_action"

    def test_brighten_when_morning_and_dim(self):
        result = _run({
            "window_slug": "w1",
            "time_of_day": "morning",
            "areas": [_area(
                "bedroom", current_brightness_pct=20, current_scene="x"
            )],
        })
        lighting = next(
            r for r in result.output["recommendations"]
            if r["dimension"] == "lighting"
        )
        assert lighting["action_kind"] == "brighten_lights"
        assert lighting["target"] == 70.0

    def test_no_brighten_when_morning_and_already_bright(self):
        result = _run({
            "window_slug": "w1",
            "time_of_day": "morning",
            "areas": [_area(
                "bedroom", current_brightness_pct=80, current_scene="x"
            )],
        })
        lighting = next(
            r for r in result.output["recommendations"]
            if r["dimension"] == "lighting"
        )
        assert lighting["action_kind"] == "no_action"

    def test_midday_lighting_default_noop(self):
        result = _run({
            "window_slug": "w1",
            "time_of_day": "midday",
            "areas": [_area(
                "kitchen", current_brightness_pct=80, current_scene="x"
            )],
        })
        lighting = next(
            r for r in result.output["recommendations"]
            if r["dimension"] == "lighting"
        )
        assert lighting["action_kind"] == "no_action"

    def test_night_dims_like_evening(self):
        result = _run({
            "window_slug": "w1",
            "time_of_day": "night",
            "areas": [_area(
                "living", current_brightness_pct=80, current_scene="x"
            )],
        })
        lighting = next(
            r for r in result.output["recommendations"]
            if r["dimension"] == "lighting"
        )
        assert lighting["action_kind"] == "dim_lights"

    def test_no_brightness_no_action(self):
        result = _run({
            "window_slug": "w1",
            "time_of_day": "evening",
            "areas": [_area("kitchen", current_scene="x")],
        })
        lighting = next(
            r for r in result.output["recommendations"]
            if r["dimension"] == "lighting"
        )
        assert lighting["action_kind"] == "no_action"


class TestSummaryAndShape:
    def test_summary_counts_match_recommendations(self):
        result = _run({
            "window_slug": "w1",
            "time_of_day": "evening",
            "vacation_mode": True,
            "areas": [
                _area("kitchen", current_brightness_pct=80,
                      current_temp_f=80, current_scene="cooking"),
            ],
        })
        s = result.output["summary"]
        # scene set_scene + temp adjust + light dim => 3 actions, 0 noop
        assert s["area_count"] == 1
        assert s["action_count"] + s["no_action_count"] == 3

    def test_highest_priority_reflects_scene(self):
        result = _run({
            "window_slug": "w1",
            "time_of_day": "evening",
            "vacation_mode": True,
            "areas": [_area("k", current_scene="cooking")],
        })
        assert result.output["summary"]["highest_priority"] == 1

    def test_highest_priority_none_if_no_actions(self):
        result = _run({
            "window_slug": "w1",
            "time_of_day": "midday",
            "areas": [_area(
                "kitchen", current_temp_f=71, current_scene="default",
                current_brightness_pct=80,
            )],
        })
        assert result.output["summary"]["highest_priority"] is None

    def test_preferences_resolved_with_defaults(self):
        result = _run({
            "window_slug": "w1",
            "time_of_day": "evening",
            "areas": [_area("kitchen", current_scene="x")],
        })
        p = result.output["preferences"]
        assert p["preferred_temp_min_f"] == 68.0
        assert p["preferred_temp_max_f"] == 74.0
        assert p["temp_action_delta_f"] == 2.0

    def test_window_slug_echoed(self):
        result = _run({
            "window_slug": "evening-2026-05-24",
            "time_of_day": "evening",
            "areas": [_area("kitchen", current_scene="x")],
        })
        assert result.output["window_slug"] == "evening-2026-05-24"

    def test_vacation_mode_echoed(self):
        result = _run({
            "window_slug": "w1",
            "time_of_day": "evening",
            "vacation_mode": True,
            "areas": [_area("kitchen", current_scene="away")],
        })
        assert result.output["vacation_mode"] is True


class TestDeterminism:
    def test_same_inputs_produce_same_output(self):
        args = {
            "window_slug": "w1",
            "time_of_day": "evening",
            "vacation_mode": False,
            "areas": [
                _area("kitchen", current_temp_f=80,
                      current_brightness_pct=80, current_scene="x"),
                _area("bedroom", current_temp_f=60,
                      current_brightness_pct=10, current_scene="y"),
            ],
        }
        r1 = _run(args)
        r2 = _run(args)
        for r in (r1.output, r2.output):
            r.pop("generated_at")
        assert r1.output == r2.output

    def test_areas_sorted_for_stable_ordering(self):
        args = {
            "window_slug": "w1",
            "time_of_day": "evening",
            "areas": [
                _area("zzz", current_scene="x"),
                _area("aaa", current_scene="x"),
                _area("mmm", current_scene="x"),
            ],
        }
        result = _run(args)
        # Scene comes first; within scene, areas should appear in
        # area_slug order.
        scene_recs = [
            r for r in result.output["recommendations"]
            if r["dimension"] == "scene"
        ]
        assert [r["area_slug"] for r in scene_recs] == ["aaa", "mmm", "zzz"]


class TestMetadata:
    def test_side_effect_summary_includes_counts(self):
        result = _run({
            "window_slug": "w1",
            "time_of_day": "evening",
            "vacation_mode": True,
            "areas": [_area("kitchen", current_scene="cooking")],
        })
        assert "action" in result.side_effect_summary
        assert "vacation=on" in result.side_effect_summary

    def test_metadata_carries_window_and_counts(self):
        result = _run({
            "window_slug": "w1",
            "time_of_day": "evening",
            "areas": [_area("kitchen", current_scene="x")],
        })
        assert result.metadata["window_slug"] == "w1"
        assert result.metadata["area_count"] == 1
