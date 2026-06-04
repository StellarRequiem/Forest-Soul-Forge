"""Tests for the training runner + report (ADR-0096) over a fake dispatch — the
runner's scoring + trust-recording logic, independent of real tools."""
import asyncio

from forest_soul_forge.synapse import TrustGraph
from forest_soul_forge.training import (
    StepOutcome, TrainingStep, TrainingTask, report, run_suite,
)


def _run(coro):
    return asyncio.run(coro)


def _task(id_, tier, pc, expect):
    return TrainingTask(id_, tier, pc, "", "read_only",
                        (TrainingStep("toolA", "1", {}, expect),))


def test_suite_scores_by_tier_and_records_passes_to_trust():
    g = TrustGraph()
    tasks = [_task("a", 0, "training.t0", {"path": "v", "equals": 1}),
             _task("b", 1, "training.t1", {"path": "ok", "truthy": True})]

    async def dispatch(agent, tool, ver, args):
        return StepOutcome("succeeded", {"v": 1, "ok": True}, audit_seq=7)

    suite = _run(run_suite(tasks, dispatch, agent_id="trainee", trust_graph=g))
    assert (suite.passed, suite.total) == (2, 2)
    assert suite.by_tier() == {0: (1, 1), 1: (1, 1)}
    # each pass recorded an audited outcome under the task's problem_class
    assert g.trust("trainee", "training.t0").mean > 0.5
    assert g.trust("trainee", "training.t1").n == 1


def test_failed_acceptance_fails_task_and_records_failure():
    g = TrustGraph()
    tasks = [_task("a", 2, "training.t2.audit", {"path": "ok", "truthy": True})]

    async def dispatch(agent, tool, ver, args):
        return StepOutcome("succeeded", {"ok": False})   # ran, but assertion fails

    suite = _run(run_suite(tasks, dispatch, agent_id="trainee", trust_graph=g))
    assert suite.passed == 0
    assert g.trust("trainee", "training.t2.audit").mean < 0.5   # failure moved trust down


def test_dispatch_that_raises_is_a_failed_step():
    tasks = [_task("a", 0, "p", {})]

    async def dispatch(agent, tool, ver, args):
        raise RuntimeError("boom")

    suite = _run(run_suite(tasks, dispatch, agent_id="t"))
    assert suite.passed == 0
    assert suite.results[0].steps[0].status == "error"


def test_multi_step_task_needs_all_steps():
    two = TrainingTask("multi", 3, "training.t3", "", "read_only", (
        TrainingStep("toolA", "1", {}, {"path": "a", "equals": 1}),
        TrainingStep("toolB", "1", {}, {"path": "b", "equals": 2}),
    ))

    async def dispatch(agent, tool, ver, args):
        return StepOutcome("succeeded", {"a": 1, "b": 99})   # second step fails

    suite = _run(run_suite([two], dispatch, agent_id="t"))
    assert suite.passed == 0 and suite.results[0].steps[0].passed is True
    assert suite.results[0].steps[1].passed is False


def test_report_renders_markdown_and_dict():
    tasks = [_task("a", 0, "p", {})]

    async def dispatch(agent, tool, ver, args):
        return StepOutcome("succeeded", {})

    suite = _run(run_suite(tasks, dispatch, agent_id="trainee"))
    md = report.to_markdown(suite, audit_ok=True, trust_ok=True)
    assert "Training report" in md and "1/1 tasks passed" in md and "Baseline" in md
    d = report.to_dict(suite, audit_ok=True, trust_ok=True)
    assert d["schema"] == "fsf.training_report.v1"
    assert d["passed"] == 1 and d["audit_chain_ok"] is True
