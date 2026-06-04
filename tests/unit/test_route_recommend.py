"""Tests for route_recommend.v1 (ADR-0095) — the agent-consultable, read-only
trust-routing recommendation tool. It ranks; it never selects or executes."""
import asyncio

import pytest

from forest_soul_forge.synapse import TrustGraph
from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.route_recommend import RouteRecommendTool


def _run(coro):
    return asyncio.run(coro)


def _ctx(graph) -> ToolContext:
    return ToolContext(instance_id="caller", agent_dna="d" * 12, role="x",
                       genre=None, session_id="s", trust_graph=graph)


def _seeded() -> TrustGraph:
    g = TrustGraph()
    for _ in range(20):
        g.record("claude", "regulatory", True)      # proven
        g.record("rotten", "regulatory", False)      # confidently bad
    return g


def test_tool_is_read_only():
    assert RouteRecommendTool().side_effects == "read_only"


def test_recommends_proven_node_and_is_reproducible_with_seed():
    t = RouteRecommendTool(); g = _seeded()
    a = _run(t.execute({"problem_class": "regulatory", "seed": 7}, _ctx(g)))
    b = _run(t.execute({"problem_class": "regulatory", "seed": 7}, _ctx(g)))
    assert a.output == b.output                          # same seed → identical
    assert a.output["recommended"] == "claude"           # exploit the proven node
    assert {r["node"] for r in a.output["ranking"]} == {"claude", "rotten"}
    claude = next(r for r in a.output["ranking"] if r["node"] == "claude")
    assert claude["trust"] > 0.8 and claude["observations"] == 20.0
    assert "human-gated" in a.output["note"]             # the ADR-0095 boundary reminder


def test_candidates_default_to_nodes_with_track_record_for_class():
    t = RouteRecommendTool(); g = _seeded()
    g.record("other", "code_review", True)               # different problem_class
    out = _run(t.execute({"problem_class": "regulatory"}, _ctx(g))).output
    assert set(out["candidates"]) == {"claude", "rotten"}  # 'other' excluded


def test_respects_explicit_candidates():
    t = RouteRecommendTool(); g = _seeded()
    out = _run(t.execute(
        {"problem_class": "regulatory", "candidates": ["rotten"]}, _ctx(g))).output
    assert out["candidates"] == ["rotten"] and out["recommended"] == "rotten"


def test_empty_for_unknown_problem_class():
    t = RouteRecommendTool(); g = _seeded()
    out = _run(t.execute({"problem_class": "no_such_class"}, _ctx(g))).output
    assert out["recommended"] is None and out["ranking"] == []


def test_refuses_cleanly_when_no_trust_graph_wired():
    t = RouteRecommendTool()
    with pytest.raises(ToolValidationError, match="no trust graph"):
        _run(t.execute({"problem_class": "regulatory"}, _ctx(None)))


def test_validate_rejects_bad_args():
    t = RouteRecommendTool()
    for bad in (
        {},                                                  # missing problem_class
        {"problem_class": ""},                               # empty
        {"problem_class": "x", "top_k": 0},                  # top_k too low
        {"problem_class": "x", "top_k": 999},                # too high
        {"problem_class": "x", "candidates": "notalist"},    # candidates not a list
        {"problem_class": "x", "candidates": [1, 2]},        # not strings
        {"problem_class": "x", "seed": "nope"},              # seed not int
    ):
        with pytest.raises(ToolValidationError):
            t.validate(bad)


def test_validate_accepts_good_args():
    t = RouteRecommendTool()
    t.validate({"problem_class": "x"})
    t.validate({"problem_class": "x", "candidates": ["a", "b"], "top_k": 3, "seed": 1})
