"""Integration: the tiered ladder runs green against REAL tools + a real audit
chain through the actual dispatcher (ADR-0096). Proves the harness end-to-end —
dispatch → audit verification (T2/T4) → trust recording → the report artifact —
and that re-running is repeatable."""
import asyncio

from forest_soul_forge.training import load_catalog, report, run_suite
from forest_soul_forge.training.harness import build_env

CATALOG = "config/tasks/training.yaml"


def _run(coro):
    return asyncio.run(coro)


def test_full_ladder_runs_green_end_to_end(tmp_path):
    env = build_env(tmp_path)
    tasks = load_catalog(CATALOG)
    suite = _run(run_suite(tasks, env.dispatch, agent_id=env.agent_id,
                           trust_graph=env.trust_graph))

    failures = [(r.id, [s.reason for s in r.steps if not s.passed])
                for r in suite.results if not r.passed]
    assert suite.passed == suite.total, failures        # every tier green
    assert set(suite.by_tier()) == {0, 1, 2, 3, 4}

    # T2/T4 dispatched audit_chain_verify against the real chain — the AUDIT
    # system, exercised live.
    t2 = next(r for r in suite.results if r.tier == 2)
    assert t2.passed

    # outcomes recorded to the synaptic layer under training.tN.* + verifies.
    assert env.trust_graph.trust(env.agent_id, "training.t0.dispatch").mean > 0.5
    assert env.trust_graph.trust(env.agent_id, "training.t2.audit").n == 1
    assert env.trust_graph.verify()[0]

    # the documentation artifact (the L4 "documentation system" exercise).
    trust_ok = env.trust_graph.verify()[0]
    md = report.to_markdown(suite, audit_ok=t2.passed, trust_ok=trust_ok)
    assert f"{suite.passed}/{suite.total} tasks passed" in md
    d = report.to_dict(suite, audit_ok=t2.passed, trust_ok=trust_ok)
    assert d["audit_chain_ok"] is True and d["trust_graph_ok"] is True


def test_rerun_is_repeatable(tmp_path):
    """Same ladder, two fresh runs -> identical pass profile (the repeatability
    spine that makes a score drop a real regression signal)."""
    tasks = load_catalog(CATALOG)
    a = build_env(tmp_path / "a")
    b = build_env(tmp_path / "b")
    sa = _run(run_suite(tasks, a.dispatch, agent_id="t", trust_graph=a.trust_graph))
    sb = _run(run_suite(tasks, b.dispatch, agent_id="t", trust_graph=b.trust_graph))
    assert sa.by_tier() == sb.by_tier()
    assert sa.passed == sb.passed == sa.total
