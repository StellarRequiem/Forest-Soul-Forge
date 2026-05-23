"""Tests for ADR-0087 Phase C — task_rank.v1 builtin tool."""
from __future__ import annotations

import asyncio

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.task_rank import TaskRankTool


def _ctx():
    return ToolContext(
        instance_id="task_prioritizer_test",
        agent_dna="a" * 12,
        role="task_prioritizer",
        genre="researcher",
        session_id=None,
    )


def _run(args):
    return asyncio.run(TaskRankTool().execute(args, _ctx()))


class TestValidation:
    def test_tasks_required(self):
        with pytest.raises(ToolValidationError, match="tasks"):
            TaskRankTool().validate({})

    def test_tasks_must_be_list(self):
        with pytest.raises(ToolValidationError, match="tasks"):
            TaskRankTool().validate({"tasks": "not a list"})

    def test_tasks_cannot_be_empty(self):
        with pytest.raises(ToolValidationError, match="at least one"):
            TaskRankTool().validate({"tasks": []})

    def test_tasks_count_capped(self):
        with pytest.raises(ToolValidationError, match="200"):
            TaskRankTool().validate(
                {"tasks": [{"title": f"t{i}"} for i in range(201)]}
            )

    def test_task_must_be_dict(self):
        with pytest.raises(ToolValidationError, match="must be a dict"):
            TaskRankTool().validate({"tasks": ["not a dict"]})

    def test_task_title_required(self):
        with pytest.raises(ToolValidationError, match="title"):
            TaskRankTool().validate({"tasks": [{"urgency": 5}]})

    def test_task_title_must_be_string(self):
        with pytest.raises(ToolValidationError, match="title"):
            TaskRankTool().validate({"tasks": [{"title": 42}]})

    def test_task_title_too_long(self):
        with pytest.raises(ToolValidationError, match="500"):
            TaskRankTool().validate(
                {"tasks": [{"title": "x" * 501}]}
            )

    def test_urgency_out_of_range(self):
        with pytest.raises(ToolValidationError, match="urgency"):
            TaskRankTool().validate(
                {"tasks": [{"title": "t", "urgency": 11}]}
            )

    def test_impact_out_of_range(self):
        with pytest.raises(ToolValidationError, match="impact"):
            TaskRankTool().validate(
                {"tasks": [{"title": "t", "impact": -1}]}
            )

    def test_effort_must_be_number(self):
        with pytest.raises(ToolValidationError, match="effort"):
            TaskRankTool().validate(
                {"tasks": [{"title": "t", "effort": "high"}]}
            )

    def test_due_in_hours_negative_rejected(self):
        with pytest.raises(ToolValidationError, match="due_in_hours"):
            TaskRankTool().validate(
                {"tasks": [{"title": "t", "due_in_hours": -3}]}
            )

    def test_tags_must_be_list(self):
        with pytest.raises(ToolValidationError, match="tags"):
            TaskRankTool().validate(
                {"tasks": [{"title": "t", "tags": "tag"}]}
            )

    def test_weights_must_be_dict(self):
        with pytest.raises(ToolValidationError, match="weights"):
            TaskRankTool().validate(
                {"tasks": [{"title": "t"}], "weights": [1, 2, 3]}
            )

    def test_weight_must_be_nonnegative(self):
        with pytest.raises(ToolValidationError, match="urgency"):
            TaskRankTool().validate(
                {"tasks": [{"title": "t"}], "weights": {"urgency": -0.5}}
            )

    def test_focus_bonus_must_be_nonnegative(self):
        with pytest.raises(ToolValidationError, match="focus_bonus"):
            TaskRankTool().validate(
                {"tasks": [{"title": "t"}], "focus_bonus": -1.0}
            )

    def test_areas_of_focus_must_be_list(self):
        with pytest.raises(ToolValidationError, match="areas_of_focus"):
            TaskRankTool().validate(
                {"tasks": [{"title": "t"}], "areas_of_focus": "all"}
            )

    def test_valid_minimal_args_ok(self):
        TaskRankTool().validate({"tasks": [{"title": "t"}]})

    def test_valid_full_args_ok(self):
        TaskRankTool().validate(
            {
                "tasks": [
                    {
                        "title": "t",
                        "urgency": 8,
                        "impact": 7,
                        "effort": 3,
                        "tags": ["work"],
                        "due_in_hours": 4,
                    },
                ],
                "weights": {"urgency": 1.5, "impact": 2.0, "effort": 0.3},
                "areas_of_focus": ["work"],
                "focus_bonus": 2.0,
            }
        )


class TestExecute:
    def test_ranks_higher_impact_first(self):
        r = _run(
            {
                "tasks": [
                    {"title": "low", "urgency": 3, "impact": 3, "effort": 5},
                    {"title": "high", "urgency": 3, "impact": 9, "effort": 5},
                ]
            }
        )
        ranked = r.output["ranked"]
        assert ranked[0]["title"] == "high"
        assert ranked[0]["rank"] == 1
        assert ranked[1]["title"] == "low"

    def test_lower_effort_wins_when_tied(self):
        r = _run(
            {
                "tasks": [
                    {"title": "cheap", "urgency": 5, "impact": 5, "effort": 1},
                    {"title": "expensive", "urgency": 5, "impact": 5, "effort": 9},
                ]
            }
        )
        assert r.output["ranked"][0]["title"] == "cheap"

    def test_urgency_drives_rank(self):
        r = _run(
            {
                "tasks": [
                    {"title": "urgent", "urgency": 9, "impact": 5, "effort": 5},
                    {"title": "calm", "urgency": 1, "impact": 5, "effort": 5},
                ]
            }
        )
        assert r.output["ranked"][0]["title"] == "urgent"

    def test_due_in_hours_overrides_low_urgency(self):
        r = _run(
            {
                "tasks": [
                    {
                        "title": "deadline",
                        "urgency": 2,
                        "impact": 5,
                        "effort": 5,
                        "due_in_hours": 1,
                    },
                    {
                        "title": "no-deadline",
                        "urgency": 5,
                        "impact": 5,
                        "effort": 5,
                    },
                ]
            }
        )
        # due_in_hours=1 -> deadline urgency ~= 10 - 1/6 ~= 9.83 -> dominates
        assert r.output["ranked"][0]["title"] == "deadline"

    def test_focus_bonus_applied(self):
        r = _run(
            {
                "tasks": [
                    {"title": "off-focus", "urgency": 5, "impact": 5, "effort": 5},
                    {
                        "title": "on-focus",
                        "urgency": 5,
                        "impact": 5,
                        "effort": 5,
                        "tags": ["forest"],
                    },
                ],
                "areas_of_focus": ["forest"],
                "focus_bonus": 3.0,
            }
        )
        assert r.output["ranked"][0]["title"] == "on-focus"
        assert r.output["ranked"][0]["breakdown"]["focus_bonus"] == 3.0

    def test_focus_bonus_case_insensitive(self):
        r = _run(
            {
                "tasks": [
                    {
                        "title": "match-case",
                        "tags": ["FOREST"],
                    }
                ],
                "areas_of_focus": ["forest"],
                "focus_bonus": 2.0,
            }
        )
        assert r.output["ranked"][0]["breakdown"]["focus_bonus"] == 2.0

    def test_defaults_when_dimensions_omitted(self):
        # Title-only task should get default 5/5/5 scoring
        r = _run({"tasks": [{"title": "default"}]})
        breakdown = r.output["ranked"][0]["breakdown"]
        # Defaults: 1.2*5 + 1.5*5 - 0.5*5 = 6 + 7.5 - 2.5 = 11.0
        assert r.output["ranked"][0]["score"] == 11.0
        assert breakdown["urgency"] == 6.0
        assert breakdown["impact"] == 7.5
        assert breakdown["effort"] == 2.5

    def test_weight_override_changes_ranking(self):
        r = _run(
            {
                "tasks": [
                    {"title": "urgent", "urgency": 9, "impact": 3, "effort": 3},
                    {"title": "impactful", "urgency": 3, "impact": 9, "effort": 3},
                ],
                "weights": {"urgency": 5.0, "impact": 0.1, "effort": 0.0},
            }
        )
        # Heavy urgency-weighted; urgent should win
        assert r.output["ranked"][0]["title"] == "urgent"

    def test_zero_weights_ok(self):
        # All weights zero -> all scores collapse to focus_bonus
        r = _run(
            {
                "tasks": [
                    {"title": "x"},
                    {"title": "y"},
                ],
                "weights": {"urgency": 0, "impact": 0, "effort": 0},
            }
        )
        # Tie -> stable order by original index
        assert r.output["ranked"][0]["title"] == "x"
        assert r.output["ranked"][1]["title"] == "y"

    def test_tied_scores_stable_order(self):
        r = _run(
            {
                "tasks": [
                    {"title": "a", "urgency": 5, "impact": 5, "effort": 5},
                    {"title": "b", "urgency": 5, "impact": 5, "effort": 5},
                    {"title": "c", "urgency": 5, "impact": 5, "effort": 5},
                ]
            }
        )
        titles = [t["title"] for t in r.output["ranked"]]
        assert titles == ["a", "b", "c"]

    def test_ranks_are_one_indexed(self):
        r = _run({"tasks": [{"title": "x"}, {"title": "y"}]})
        ranks = [t["rank"] for t in r.output["ranked"]]
        assert ranks == [1, 2]

    def test_output_carries_metadata(self):
        r = _run(
            {
                "tasks": [
                    {"title": "winner", "urgency": 10, "impact": 10, "effort": 1},
                    {"title": "loser", "urgency": 1, "impact": 1, "effort": 9},
                ]
            }
        )
        assert r.metadata["task_count"] == 2
        assert r.metadata["top_title"] == "winner"

    def test_side_effect_summary(self):
        r = _run({"tasks": [{"title": "x"}]})
        assert "ranked 1 tasks" in r.side_effect_summary

    def test_breakdown_present_per_task(self):
        r = _run({"tasks": [{"title": "x"}]})
        bd = r.output["ranked"][0]["breakdown"]
        assert set(bd.keys()) == {
            "urgency", "impact", "effort", "focus_bonus",
        }

    def test_generated_at_iso(self):
        r = _run({"tasks": [{"title": "x"}]})
        assert r.output["generated_at"].endswith("Z")

    def test_weights_echoed_in_output(self):
        r = _run({"tasks": [{"title": "x"}]})
        w = r.output["weights"]
        assert w["urgency"] == 1.2
        assert w["impact"] == 1.5
        assert w["effort"] == 0.5

    def test_areas_of_focus_echoed_in_output(self):
        r = _run(
            {
                "tasks": [{"title": "x"}],
                "areas_of_focus": ["work", "health"],
            }
        )
        assert r.output["areas_of_focus"] == ["work", "health"]


class TestSchema:
    def test_name_version(self):
        t = TaskRankTool()
        assert t.name == "task_rank"
        assert t.version == "1"

    def test_side_effects_read_only(self):
        assert TaskRankTool().side_effects == "read_only"
