"""Unit tests for ADR-0033 Phase B2 — pure-Python mid-tier tools.

Covers:
- behavioral_baseline.v1
- anomaly_score.v1 (paired with the baseline emitter)
- log_correlate.v1
- lateral_movement_detect.v1

All four are deterministic over caller-supplied data so tests
exercise real paths (no subprocess mocking needed).
"""
from __future__ import annotations

import asyncio

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin import (
    AnomalyScoreTool,
    BehavioralBaselineTool,
    LateralMovementDetectTool,
    LogCorrelateTool,
)


def _run(coro):
    return asyncio.run(coro)


def _ctx():
    return ToolContext(
        instance_id="x", agent_dna="x" * 12,
        role="observer", genre="security_mid",
        session_id="s",
    )


# ===========================================================================
# behavioral_baseline.v1
# ===========================================================================
class TestBehavioralBaseline:
    def test_validation_refusals(self):
        for bad, hint in [
            ({}, "events"),
            ({"events": "not a list"}, "list"),
            ({"events": [123]}, "dict"),
            ({"events": [], "fields": {}}, "non-empty"),
            ({"events": [{}], "fields": {"a": {"type": "bananas"}}}, "type"),
            ({"events": [{}], "fields": {"a": {"type": "categorical", "top_n": 999}}}, "top_n"),
            ({"events": [{}], "fields": {"a": {"type": "timestamp", "buckets": 1}}}, "buckets"),
        ]:
            with pytest.raises(ToolValidationError, match=hint):
                BehavioralBaselineTool().validate(bad)

    def test_categorical_frequency_table(self):
        events = [
            {"user": "alice"}, {"user": "alice"}, {"user": "bob"},
        ]
        result = _run(BehavioralBaselineTool().execute({
            "events": events,
            "fields": {"user": {"type": "categorical"}},
        }, _ctx()))
        u = result.output["fields"]["user"]
        assert u["unique_count"] == 2
        assert u["frequency"] == {"alice": 2, "bob": 1}
        assert u["top"][0]["value"] == "alice"

    def test_numeric_stats(self):
        events = [{"x": v} for v in [10, 20, 30]]
        result = _run(BehavioralBaselineTool().execute({
            "events": events,
            "fields": {"x": {"type": "numeric"}},
        }, _ctx()))
        x = result.output["fields"]["x"]
        assert x["count"] == 3
        assert x["mean"] == 20.0
        assert x["min"] == 10
        assert x["max"] == 30

    def test_numeric_handles_non_numeric(self):
        events = [{"x": "abc"}, {"x": 5}, {"x": None}]
        result = _run(BehavioralBaselineTool().execute({
            "events": events,
            "fields": {"x": {"type": "numeric"}},
        }, _ctx()))
        x = result.output["fields"]["x"]
        assert x["count"] == 1
        assert x["non_numeric_count"] == 1
        assert x["missing_count"] == 1

    def test_timestamp_buckets_parse_iso(self):
        events = [
            {"t": "2026-04-27T10:00:00Z"},
            {"t": "2026-04-27T11:00:00Z"},
            {"t": "2026-04-27T12:00:00Z"},
            {"t": "2026-04-27T13:00:00Z"},
        ]
        result = _run(BehavioralBaselineTool().execute({
            "events": events,
            "fields": {"t": {"type": "timestamp", "buckets": 4}},
        }, _ctx()))
        t = result.output["fields"]["t"]
        assert t["count"] == 4
        assert sum(t["buckets"]) == 4

    def test_missing_field_counted(self):
        events = [{"x": 1}, {}, {"x": 2}]
        result = _run(BehavioralBaselineTool().execute({
            "events": events,
            "fields": {"x": {"type": "numeric"}},
        }, _ctx()))
        x = result.output["fields"]["x"]
        assert x["missing_count"] == 1
        assert x["count"] == 2


# ===========================================================================
# anomaly_score.v1
# ===========================================================================
class TestAnomalyScore:
    @pytest.fixture
    def baseline(self):
        events = [
            {"user": "alice", "bytes": 100},
            {"user": "alice", "bytes": 110},
            {"user": "alice", "bytes": 105},
            {"user": "bob", "bytes": 200},
        ]
        return _run(BehavioralBaselineTool().execute({
            "events": events,
            "fields": {
                "user": {"type": "categorical"},
                "bytes": {"type": "numeric"},
            },
        }, _ctx())).output

    def test_normal_window_low_score(self, baseline):
        normal = [{"user": "alice", "bytes": 108}]
        result = _run(AnomalyScoreTool().execute({
            "events": normal, "baseline": baseline,
        }, _ctx()))
        # alice is the most common; small z-score
        assert result.output["score"] < 1.0

    def test_novel_categorical_scores_one(self, baseline):
        novel = [{"user": "hacker", "bytes": 105}]
        result = _run(AnomalyScoreTool().execute({
            "events": novel, "baseline": baseline,
        }, _ctx()))
        u = result.output["fields"]["user"]
        assert u["score"] == 1.0
        assert "hacker" in u["novel"]

    def test_numeric_outlier_high_z(self, baseline):
        outlier = [{"user": "alice", "bytes": 99999}]
        result = _run(AnomalyScoreTool().execute({
            "events": outlier, "baseline": baseline,
        }, _ctx()))
        b = result.output["fields"]["bytes"]
        assert b["score"] > 10.0  # very anomalous

    def test_overall_score_is_max_of_fields(self, baseline):
        # novel categorical (score 1.0) + tame numeric → overall = 1.0
        events = [{"user": "ghost", "bytes": 105}]
        result = _run(AnomalyScoreTool().execute({
            "events": events, "baseline": baseline,
        }, _ctx()))
        # max should pick the higher of user.score (1.0) vs bytes.score
        assert result.output["score"] >= 1.0

    def test_field_subset(self, baseline):
        # only score 'user', skip 'bytes'
        result = _run(AnomalyScoreTool().execute({
            "events": [{"user": "alice", "bytes": 99999}],
            "baseline": baseline,
            "fields": ["user"],
        }, _ctx()))
        assert "user" in result.output["fields"]
        assert "bytes" not in result.output["fields"]

    def test_field_missing_from_baseline(self, baseline):
        result = _run(AnomalyScoreTool().execute({
            "events": [{"user": "alice"}],
            "baseline": baseline,
            "fields": ["nonexistent"],
        }, _ctx()))
        assert result.output["fields"]["nonexistent"]["type"] == "missing_from_baseline"


# ===========================================================================
# log_correlate.v1
# ===========================================================================
class TestLogCorrelate:
    def test_validation_refusals(self):
        for bad, hint in [
            ({}, "events"),
            ({"events": [{}]}, "key"),
            ({"events": [{}], "key": {}}, "field|regex"),
            ({"events": [{}], "key": {"field": ""}}, "non-empty"),
            ({"events": [{}], "key": {"regex": "[bad"}}, "compile"),
            ({"events": [{}], "key": {"regex": "no group", "field": "x"}}, "capturing"),
            ({"events": [{}], "key": {"regex": "(.*)", "field": ""}}, "field"),
        ]:
            with pytest.raises(ToolValidationError, match=hint):
                LogCorrelateTool().validate(bad)

    def test_field_extraction(self):
        events = [
            {"user": "alice", "ts": "2026-04-27T10:00:00Z", "path": "/a.log"},
            {"user": "bob", "ts": "2026-04-27T10:01:00Z", "path": "/b.log"},
            {"user": "alice", "ts": "2026-04-27T10:02:00Z", "path": "/a.log"},
        ]
        result = _run(LogCorrelateTool().execute({
            "events": events, "key": {"field": "user"},
        }, _ctx()))
        assert result.output["group_count"] == 2
        keys = {g["key"] for g in result.output["groups"]}
        assert keys == {"alice", "bob"}

    def test_regex_extraction(self):
        events = [
            {"text": "from 10.0.0.1"},
            {"text": "from 10.0.0.2"},
            {"text": "no match here"},
        ]
        result = _run(LogCorrelateTool().execute({
            "events": events,
            "key": {"regex": r"(\d+\.\d+\.\d+\.\d+)", "field": "text"},
        }, _ctx()))
        assert result.output["unmatched_count"] == 1
        assert result.output["group_count"] == 2

    def test_groups_sorted_by_count_desc(self):
        events = (
            [{"u": "alice"} for _ in range(5)] +
            [{"u": "bob"} for _ in range(3)] +
            [{"u": "carol"} for _ in range(7)]
        )
        result = _run(LogCorrelateTool().execute({
            "events": events, "key": {"field": "u"},
        }, _ctx()))
        counts = [g["count"] for g in result.output["groups"]]
        assert counts == sorted(counts, reverse=True)
        assert result.output["groups"][0]["key"] == "carol"

    def test_max_groups_truncates(self):
        events = [{"u": f"user{i}"} for i in range(20)]
        result = _run(LogCorrelateTool().execute({
            "events": events, "key": {"field": "u"}, "max_groups": 5,
        }, _ctx()))
        assert len(result.output["groups"]) == 5
        assert result.output["truncated"] is True
        assert result.output["group_count"] == 20  # full count preserved

    def test_per_group_event_cap(self):
        events = [{"u": "alice", "i": i} for i in range(100)]
        result = _run(LogCorrelateTool().execute({
            "events": events, "key": {"field": "u"}, "max_per_group": 10,
        }, _ctx()))
        assert len(result.output["groups"][0]["events"]) == 10
        assert result.output["groups"][0]["count"] == 100

    def test_distinct_sources_listed(self):
        events = [
            {"u": "alice", "path": "/a.log"},
            {"u": "alice", "path": "/b.log"},
            {"u": "alice", "path": "/a.log"},
        ]
        result = _run(LogCorrelateTool().execute({
            "events": events, "key": {"field": "u"},
        }, _ctx()))
        assert set(result.output["groups"][0]["distinct_sources"]) == {"/a.log", "/b.log"}


# ===========================================================================
# lateral_movement_detect.v1
# ===========================================================================
class TestLateralMovementDetect:
    def test_validation_refusals(self):
        for bad, hint in [
            ({}, "edges"),
            ({"edges": [{"src": "a"}]}, "src.*dst"),
            ({"edges": [], "thresholds": {"fan_out_min": -1}}, "positive"),
            ({"edges": [], "max_examples": 0}, "max_examples"),
        ]:
            with pytest.raises(ToolValidationError, match=hint):
                LateralMovementDetectTool().validate(bad)

    def test_fan_out_triggers(self):
        edges = [{"src": "alice", "dst": f"dst{i}"} for i in range(15)]
        result = _run(LateralMovementDetectTool().execute(
            {"edges": edges, "thresholds": {"fan_out_min": 10}}, _ctx(),
        ))
        assert any(f["src"] == "alice" for f in result.output["fan_out"])
        alice_fan = next(f for f in result.output["fan_out"] if f["src"] == "alice")
        assert alice_fan["distinct_dsts"] == 15

    def test_fan_in_triggers(self):
        edges = [{"src": f"src{i}", "dst": "victim"} for i in range(15)]
        result = _run(LateralMovementDetectTool().execute(
            {"edges": edges, "thresholds": {"fan_in_min": 10}}, _ctx(),
        ))
        assert any(f["dst"] == "victim" for f in result.output["fan_in"])

    def test_below_threshold_does_not_trigger(self):
        edges = [{"src": "alice", "dst": f"d{i}"} for i in range(5)]
        result = _run(LateralMovementDetectTool().execute(
            {"edges": edges, "thresholds": {"fan_out_min": 10}}, _ctx(),
        ))
        assert result.output["fan_out"] == []

    def test_new_edges_against_baseline(self):
        edges = [
            {"src": "alice", "dst": "db1"},
            {"src": "alice", "dst": "db2"},
            {"src": "bob", "dst": "web"},
        ]
        baseline = [
            {"src": "alice", "dst": "db1"},
            {"src": "bob", "dst": "web"},
        ]
        result = _run(LateralMovementDetectTool().execute(
            {"edges": edges, "baseline_edges": baseline}, _ctx(),
        ))
        assert len(result.output["new_edges"]) == 1
        assert result.output["new_edges"][0] == {"src": "alice", "dst": "db2"}

    def test_no_baseline_means_no_new_edges_path(self):
        edges = [{"src": "a", "dst": "b"}]
        result = _run(LateralMovementDetectTool().execute(
            {"edges": edges}, _ctx(),
        ))
        assert result.output["new_edges"] == []

    def test_distinct_ports_triggers(self):
        edges = [
            {"src": "scanner", "dst": "victim", "port": p}
            for p in range(20, 50)
        ]
        result = _run(LateralMovementDetectTool().execute(
            {"edges": edges, "thresholds": {"distinct_ports_min": 20}}, _ctx(),
        ))
        assert len(result.output["distinct_ports"]) == 1
        d = result.output["distinct_ports"][0]
        assert d["src"] == "scanner" and d["dst"] == "victim"
        assert d["ports"] == 30

    def test_node_count_correct(self):
        edges = [
            {"src": "a", "dst": "b"},
            {"src": "b", "dst": "c"},  # b is both src and dst
            {"src": "a", "dst": "c"},
        ]
        result = _run(LateralMovementDetectTool().execute(
            {"edges": edges}, _ctx(),
        ))
        assert result.output["node_count"] == 3


# ===========================================================================
# Registration sanity
# ===========================================================================
class TestRegistration:
    def test_all_b2_tools_register(self):
        from forest_soul_forge.tools import ToolRegistry
        from forest_soul_forge.tools.builtin import register_builtins
        reg = ToolRegistry()
        register_builtins(reg)
        for name in ("behavioral_baseline", "anomaly_score",
                     "log_correlate", "lateral_movement_detect"):
            assert reg.has(name, "1"), f"{name} not registered"
