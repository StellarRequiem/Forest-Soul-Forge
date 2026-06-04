"""Forest training harness (ADR-0096).

The tiered, repeatable, autonomously-runnable self-test ladder: Baseline + L1–4
deterministic, read-only tasks that exercise dispatch → audit → trust → docs, feed
the synaptic layer, and keep the audit + documentation systems honest by
construction. Decoupled from the daemon — the runner takes an injected dispatch
callable, so it drives an in-process dispatcher (tests / headless runs) today and
a daemon scheduler task-type tomorrow.
"""
from forest_soul_forge.training.acceptance import check_step
from forest_soul_forge.training.catalog import (
    SCHEMA,
    TrainingStep,
    TrainingTask,
    load_catalog,
)
from forest_soul_forge.training.runner import (
    StepOutcome,
    StepResult,
    SuiteResult,
    TaskResult,
    run_suite,
    run_task,
)
from forest_soul_forge.training import report

__all__ = [
    "SCHEMA", "TrainingStep", "TrainingTask", "load_catalog", "check_step",
    "StepOutcome", "StepResult", "TaskResult", "SuiteResult",
    "run_task", "run_suite", "report",
]
