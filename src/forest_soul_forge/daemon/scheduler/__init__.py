"""``daemon/scheduler`` — set-and-forget orchestrator (ADR-0041).

The daemon-internal scheduler that runs in the FastAPI app's
asyncio loop. Per ADR-0041, this is the substrate that
recurring drivers (verifier scans, scenario runs, cleanup tasks)
dispatch through.

Public surface:
- :class:`Scheduler` — the runtime. Started/stopped in the FastAPI
  lifespan context. Holds tasks, runs the poll loop.
- :class:`ScheduledTask` — one configured task. Has schedule,
  type, config, and runtime state (last_run, next_run, failure
  counters).
- :class:`Schedule` — parsed schedule definition. T2 supports
  interval-based only ("every 5m", "every 24h"); cron syntax
  is queued.

What's NOT here yet (per the tranche plan in ADR-0041):
- Task type implementations — Burst 87 (T3) adds tool_call;
  Burst 87/88 add the scenario runner.
- Persistence — Burst 88 (T2.3) adds the SQLite v13 schema +
  read/write so state survives daemon restart.
- HTTP control endpoints — list+status are minimal in this
  burst; trigger/enable/disable/reset land in Burst 89 (T5).
"""
from __future__ import annotations

from forest_soul_forge.daemon.scheduler.runtime import (
    Scheduler,
    ScheduledTask,
    TaskState,
    build_task_from_config,
)
from forest_soul_forge.daemon.scheduler.schedule import (
    Schedule,
    ScheduleParseError,
    parse_schedule,
)

__all__ = [
    "Scheduler",
    "ScheduledTask",
    "TaskState",
    "build_task_from_config",
    "Schedule",
    "ScheduleParseError",
    "parse_schedule",
]
