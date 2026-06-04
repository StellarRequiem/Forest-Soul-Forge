"""Tests for the training catalog loader + deterministic acceptance (ADR-0096)."""
from pathlib import Path

import pytest

from forest_soul_forge.training import SCHEMA, check_step, load_catalog

SHIPPED = Path("config/tasks/training.yaml")


def test_shipped_ladder_loads_with_all_tiers():
    tasks = load_catalog(SHIPPED)
    assert len(tasks) >= 6
    assert sorted({t.tier for t in tasks}) == [0, 1, 2, 3, 4]   # Baseline..L4
    assert all(t.side_effects == "read_only" for t in tasks)    # auto-run rail
    assert len({t.id for t in tasks}) == len(tasks)             # unique ids
    # every step names a tool + has an acceptance spec
    for t in tasks:
        assert t.steps
        for s in t.steps:
            assert s.tool and isinstance(s.expect, dict)


def test_rejects_non_read_only_task(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        "schema: fsf.training.v1\ntasks:\n"
        "- {id: x, tier: 0, problem_class: p, side_effects: filesystem, "
        "steps: [{tool: t, expect: {}}]}\n")
    with pytest.raises(ValueError, match="read_only"):
        load_catalog(p)


def test_rejects_unknown_schema(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("schema: nope\ntasks: []\n")
    with pytest.raises(ValueError, match="schema"):
        load_catalog(p)


def test_rejects_duplicate_id(tmp_path):
    p = tmp_path / "d.yaml"
    p.write_text(
        "schema: fsf.training.v1\ntasks:\n"
        "- {id: x, tier: 0, problem_class: p, steps: [{tool: t, expect: {}}]}\n"
        "- {id: x, tier: 1, problem_class: q, steps: [{tool: t, expect: {}}]}\n")
    with pytest.raises(ValueError, match="duplicate"):
        load_catalog(p)


# -- acceptance ---------------------------------------------------------------

def test_acceptance_status_default_and_override():
    assert check_step({}, "succeeded", {})[0] is True
    assert check_step({}, "failed", {})[0] is False
    assert check_step({"status": "failed"}, "failed", {})[0] is True


def test_acceptance_equals():
    assert check_step({"path": "span_seconds", "equals": 300}, "succeeded",
                      {"span_seconds": 300})[0] is True
    ok, reason = check_step({"path": "span_seconds", "equals": 300}, "succeeded",
                            {"span_seconds": 600})
    assert ok is False and "span_seconds" in reason


def test_acceptance_truthy_and_missing_path():
    assert check_step({"path": "ok", "truthy": True}, "succeeded", {"ok": True})[0] is True
    assert check_step({"path": "ok", "truthy": True}, "succeeded", {"ok": False})[0] is False
    assert check_step({"path": "n", "truthy": True}, "succeeded", {"n": 5})[0] is True
    assert check_step({"path": "n", "truthy": True}, "succeeded", {"n": 0})[0] is False
    assert check_step({"path": "a.b", "truthy": True}, "succeeded", {})[0] is False  # missing


def test_acceptance_contains_case_insensitive():
    # the deterministic correctness check for known-answer LLM benchmark tasks
    assert check_step({"path": "response", "contains": "4"}, "succeeded",
                      {"response": "The answer is 4."})[0] is True
    assert check_step({"path": "response", "contains": "Paris"}, "succeeded",
                      {"response": "paris"})[0] is True              # case-insensitive
    ok, reason = check_step({"path": "response", "contains": "56"}, "succeeded",
                            {"response": "forty-two"})
    assert ok is False and "contain" in reason
    assert check_step({"path": "response", "contains": "x"}, "succeeded", {})[0] is False


def test_benchmark_catalog_loads():
    cat = load_catalog(Path("config/tasks/benchmark.yaml"))
    assert len(cat) >= 4
    assert all(t.side_effects == "read_only" for t in cat)          # auto-run rail
    assert all(t.steps[0].tool == "llm_think" for t in cat)         # the model under test
    assert all("contains" in t.steps[0].expect for t in cat)        # deterministic scoring
