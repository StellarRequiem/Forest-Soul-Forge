"""Training runner (ADR-0096) — runs the tiered ladder, scores it deterministically,
and records each task outcome to the synaptic layer.

Decoupled from the daemon: the caller injects an async ``dispatch`` callable, so
the same runner drives an in-process dispatcher (tests + headless runs) today and
a daemon scheduler task-type tomorrow. Records task-level outcomes to an optional
trust graph under each task's ``problem_class`` — so per-tier competence is
evidence-backed and trends over time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from forest_soul_forge.training.acceptance import check_step
from forest_soul_forge.training.catalog import TrainingTask


@dataclass(frozen=True)
class StepOutcome:
    """Normalized result of one tool dispatch — what a ``dispatch`` fn returns."""
    status: str                       # "succeeded" | "failed" | "refused" | "error"
    output: Any = None
    audit_seq: int | None = None
    exception_type: str | None = None


# dispatch(agent_id, tool, version, args) -> StepOutcome
DispatchFn = Callable[[str, str, str, dict], Awaitable[StepOutcome]]


@dataclass
class StepResult:
    tool: str
    passed: bool
    reason: str
    status: str
    audit_seq: int | None


@dataclass
class TaskResult:
    id: str
    tier: int
    problem_class: str
    passed: bool
    steps: list[StepResult] = field(default_factory=list)


@dataclass
class SuiteResult:
    agent_id: str
    results: list[TaskResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def total(self) -> int:
        return len(self.results)

    def by_tier(self) -> dict[int, tuple[int, int]]:
        """{tier: (passed, total)} sorted by tier."""
        agg: dict[int, list[int]] = {}
        for r in self.results:
            cell = agg.setdefault(r.tier, [0, 0])
            cell[1] += 1
            if r.passed:
                cell[0] += 1
        return {t: (p, n) for t, (p, n) in sorted(agg.items())}


async def run_task(task: TrainingTask, dispatch: DispatchFn, agent_id: str) -> TaskResult:
    """Run every step; the task passes iff ALL steps pass their acceptance check."""
    steps: list[StepResult] = []
    all_passed = True
    for st in task.steps:
        try:
            outcome = await dispatch(agent_id, st.tool, st.version, st.args)
        except Exception as e:  # a dispatch that itself raised is a failed step
            steps.append(StepResult(
                st.tool, False, f"dispatch raised: {type(e).__name__}: {e}", "error", None))
            all_passed = False
            continue
        ok, reason = check_step(st.expect, outcome.status, outcome.output)
        steps.append(StepResult(st.tool, ok, reason, outcome.status, outcome.audit_seq))
        if not ok:
            all_passed = False
    return TaskResult(task.id, task.tier, task.problem_class, all_passed, steps)


async def run_suite(tasks: list[TrainingTask], dispatch: DispatchFn, *, agent_id: str,
                    trust_graph: Any = None) -> SuiteResult:
    """Run the whole ladder. When ``trust_graph`` is given, record each task's
    pass/fail as an audited outcome for ``(agent_id, task.problem_class)`` — the
    synaptic layer learns per-tier competence. Recording never breaks a run."""
    suite = SuiteResult(agent_id=agent_id)
    for task in tasks:
        res = await run_task(task, dispatch, agent_id)
        suite.results.append(res)
        if trust_graph is not None:
            try:
                trust_graph.record(agent_id, task.problem_class, res.passed,
                                   evidence=f"training:{task.id}")
            except Exception:
                pass
    return suite
