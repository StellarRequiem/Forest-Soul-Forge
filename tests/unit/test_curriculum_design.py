"""Tests for ADR-0089 Phase A — curriculum_design.v1 builtin tool."""
from __future__ import annotations

import asyncio

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.curriculum_design import (
    CurriculumDesignTool,
)


def _ctx():
    return ToolContext(
        instance_id="curriculum_designer_test",
        agent_dna="a" * 12,
        role="curriculum_designer",
        genre="researcher",
        session_id=None,
    )


def _run(args):
    return asyncio.run(CurriculumDesignTool().execute(args, _ctx()))


def _simple_catalog():
    return [
        {"slug": "basics",   "title": "Basics",   "prereq_slugs": []},
        {"slug": "linear",   "title": "Linear algebra",
         "prereq_slugs": ["basics"]},
        {"slug": "calculus", "title": "Calculus",
         "prereq_slugs": ["basics"]},
        {"slug": "probability", "title": "Probability",
         "prereq_slugs": ["calculus"]},
        {"slug": "diffusion", "title": "Diffusion models",
         "prereq_slugs": ["linear", "probability"]},
    ]


class TestValidation:
    def test_goal_topic_required(self):
        with pytest.raises(ToolValidationError, match="goal_topic"):
            CurriculumDesignTool().validate(
                {"catalog": _simple_catalog()}
            )

    def test_goal_topic_must_be_string(self):
        with pytest.raises(ToolValidationError, match="goal_topic"):
            CurriculumDesignTool().validate(
                {"goal_topic": 42, "catalog": _simple_catalog()}
            )

    def test_catalog_required(self):
        with pytest.raises(ToolValidationError, match="catalog"):
            CurriculumDesignTool().validate({"goal_topic": "diffusion"})

    def test_catalog_must_be_list(self):
        with pytest.raises(ToolValidationError, match="catalog"):
            CurriculumDesignTool().validate(
                {"goal_topic": "diffusion", "catalog": "not a list"}
            )

    def test_catalog_must_be_non_empty(self):
        with pytest.raises(ToolValidationError, match="at least one"):
            CurriculumDesignTool().validate(
                {"goal_topic": "diffusion", "catalog": []}
            )

    def test_catalog_capped(self):
        big = [
            {"slug": f"t{i}", "title": f"Topic {i}"}
            for i in range(201)
        ]
        with pytest.raises(ToolValidationError, match="200"):
            CurriculumDesignTool().validate(
                {"goal_topic": "diffusion", "catalog": big}
            )

    def test_entry_must_be_dict(self):
        with pytest.raises(ToolValidationError, match="must be a dict"):
            CurriculumDesignTool().validate(
                {"goal_topic": "diffusion", "catalog": ["not a dict"]}
            )

    def test_slug_required(self):
        with pytest.raises(ToolValidationError, match="slug"):
            CurriculumDesignTool().validate(
                {"goal_topic": "diffusion", "catalog": [{"title": "x"}]}
            )

    def test_slug_must_be_unique(self):
        with pytest.raises(ToolValidationError, match="duplicates"):
            CurriculumDesignTool().validate(
                {
                    "goal_topic": "diffusion",
                    "catalog": [
                        {"slug": "a", "title": "A"},
                        {"slug": "a", "title": "Another A"},
                    ],
                }
            )

    def test_title_required(self):
        with pytest.raises(ToolValidationError, match="title"):
            CurriculumDesignTool().validate(
                {
                    "goal_topic": "diffusion",
                    "catalog": [{"slug": "x"}],
                }
            )

    def test_prereqs_must_be_list(self):
        with pytest.raises(ToolValidationError, match="prereq_slugs"):
            CurriculumDesignTool().validate(
                {
                    "goal_topic": "diffusion",
                    "catalog": [
                        {
                            "slug": "x", "title": "X",
                            "prereq_slugs": "not a list",
                        }
                    ],
                }
            )

    def test_familiarity_out_of_range(self):
        with pytest.raises(ToolValidationError, match="familiarity"):
            CurriculumDesignTool().validate(
                {
                    "goal_topic": "diffusion",
                    "catalog": [
                        {
                            "slug": "x", "title": "X",
                            "current_familiarity": 11,
                        }
                    ],
                }
            )

    def test_expertise_level_must_be_known(self):
        with pytest.raises(ToolValidationError, match="expertise_level"):
            CurriculumDesignTool().validate(
                {
                    "goal_topic": "diffusion",
                    "catalog": _simple_catalog(),
                    "expertise_level": "expert",  # not in enum
                }
            )

    def test_target_weeks_range(self):
        with pytest.raises(ToolValidationError, match="target_weeks"):
            CurriculumDesignTool().validate(
                {
                    "goal_topic": "diffusion",
                    "catalog": _simple_catalog(),
                    "target_weeks": 100,
                }
            )


class TestOrdering:
    def test_basic_topo_order(self):
        result = _run(
            {"goal_topic": "diffusion", "catalog": _simple_catalog()}
        )
        slugs = [step["slug"] for step in result.output["ordered_path"]]
        # basics must come before linear / calculus
        assert slugs.index("basics") < slugs.index("linear")
        assert slugs.index("basics") < slugs.index("calculus")
        # calculus must precede probability
        assert slugs.index("calculus") < slugs.index("probability")
        # linear + probability both precede diffusion
        assert slugs.index("linear") < slugs.index("diffusion")
        assert slugs.index("probability") < slugs.index("diffusion")

    def test_deterministic_two_calls_match(self):
        cat = _simple_catalog()
        r1 = _run({"goal_topic": "diffusion", "catalog": cat})
        r2 = _run({"goal_topic": "diffusion", "catalog": cat})
        assert (
            [s["slug"] for s in r1.output["ordered_path"]]
            == [s["slug"] for s in r2.output["ordered_path"]]
        )

    def test_already_known_excluded(self):
        cat = _simple_catalog()
        cat[0]["current_familiarity"] = 9  # basics already known
        result = _run(
            {"goal_topic": "diffusion", "catalog": cat}
        )
        slugs = [step["slug"] for step in result.output["ordered_path"]]
        assert "basics" not in slugs
        assert "basics" in result.output["already_known"]

    def test_rank_field_is_1_based(self):
        result = _run(
            {"goal_topic": "diffusion", "catalog": _simple_catalog()}
        )
        ranks = [s["rank"] for s in result.output["ordered_path"]]
        assert ranks == list(range(1, len(ranks) + 1))


class TestDAG:
    def test_nodes_include_every_catalog_entry(self):
        result = _run(
            {"goal_topic": "diffusion", "catalog": _simple_catalog()}
        )
        assert set(result.output["dag"]["nodes"]) == {
            "basics", "linear", "calculus",
            "probability", "diffusion",
        }

    def test_edges_only_for_defined_prereqs(self):
        cat = _simple_catalog()
        cat.append({
            "slug": "advanced", "title": "Advanced",
            "prereq_slugs": ["diffusion", "missing-topic"],
        })
        result = _run({"goal_topic": "advanced", "catalog": cat})
        edges = result.output["dag"]["edges"]
        # diffusion → advanced is in
        assert ["diffusion", "advanced"] in edges
        # missing-topic → advanced is NOT — it's orphan
        for src, _ in edges:
            assert src != "missing-topic"
        assert "missing-topic" in result.output["orphan_prereqs"]


class TestCycles:
    def test_simple_cycle_detected(self):
        cat = [
            {"slug": "a", "title": "A", "prereq_slugs": ["b"]},
            {"slug": "b", "title": "B", "prereq_slugs": ["a"]},
        ]
        result = _run({"goal_topic": "a", "catalog": cat})
        assert result.output["has_cycles"] is True
        assert set(result.output["cycle_members"]) == {"a", "b"}

    def test_self_loop_detected(self):
        cat = [
            {"slug": "a", "title": "A", "prereq_slugs": ["a"]},
        ]
        result = _run({"goal_topic": "a", "catalog": cat})
        assert result.output["has_cycles"] is True
        assert "a" in result.output["cycle_members"]

    def test_no_cycles_in_clean_catalog(self):
        result = _run(
            {"goal_topic": "diffusion", "catalog": _simple_catalog()}
        )
        assert result.output["has_cycles"] is False
        assert result.output["cycle_members"] == []


class TestExecuteSummary:
    def test_summary_counts(self):
        result = _run(
            {"goal_topic": "diffusion", "catalog": _simple_catalog()}
        )
        summary = result.output["summary"]
        assert summary["catalog_size"] == 5
        assert summary["path_size"] == 5
        assert summary["max_depth"] >= 1

    def test_expertise_and_weeks_pass_through(self):
        result = _run(
            {
                "goal_topic": "diffusion",
                "catalog": _simple_catalog(),
                "expertise_level": "intermediate",
                "target_weeks": 8,
            }
        )
        assert result.output["expertise_level"] == "intermediate"
        assert result.output["target_weeks"] == 8

    def test_metadata_includes_top_level_fields(self):
        result = _run(
            {"goal_topic": "diffusion", "catalog": _simple_catalog()}
        )
        assert result.metadata["catalog_size"] == 5
        assert result.metadata["has_cycles"] is False
        assert result.metadata["goal_topic"] == "diffusion"

    def test_side_effect_summary_mentions_path_size(self):
        result = _run(
            {"goal_topic": "diffusion", "catalog": _simple_catalog()}
        )
        assert "curriculum" in result.side_effect_summary
        assert "5" in result.side_effect_summary
