"""``/scheduler`` router — observability for the ADR-0041 set-and-forget orchestrator.

Burst 86 (T2) ships GET endpoints only:
- ``GET /scheduler/status`` — scheduler running? task count? breaker count?
- ``GET /scheduler/tasks`` — list all tasks + per-task state.
- ``GET /scheduler/tasks/{task_id}`` — full state for one task.

POST endpoints (trigger / enable / disable / reset) land in
Burst 89 (T5). Operators in v0.4 can configure tasks via
``config/scheduled_tasks.yaml`` and observe them via these GETs;
mutating the runtime requires a daemon restart for now.

Read endpoints don't touch the write_lock and don't require the
api_token gate. Same posture as the audit/health routers.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from forest_soul_forge.daemon.scheduler.runtime import Scheduler


router = APIRouter(tags=["scheduler"])


def _scheduler(request: Request) -> Scheduler:
    sched = getattr(request.app.state, "scheduler", None)
    if sched is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="scheduler not running",
        )
    return sched


def _serialize_task(task) -> dict[str, Any]:
    """Render a ScheduledTask as a JSON-friendly dict. Includes
    runtime state so the operator can see last_run / next_run /
    failure counters at a glance.
    """
    return {
        "id": task.id,
        "description": task.description,
        "schedule": {
            "raw": task.schedule.raw,
            "interval_seconds": task.schedule.interval_seconds,
        },
        "type": task.task_type,
        "config": task.config,
        "enabled": task.enabled,
        "max_consecutive_failures": task.max_consecutive_failures,
        "state": {
            "last_run_at": (
                task.state.last_run_at.isoformat()
                if task.state.last_run_at
                else None
            ),
            "next_run_at": (
                task.state.next_run_at.isoformat()
                if task.state.next_run_at
                else None
            ),
            "consecutive_failures": task.state.consecutive_failures,
            "circuit_breaker_open": task.state.circuit_breaker_open,
            "total_runs": task.state.total_runs,
            "total_successes": task.state.total_successes,
            "total_failures": task.state.total_failures,
            "last_failure_reason": task.state.last_failure_reason,
            "last_run_outcome": task.state.last_run_outcome,
        },
    }


@router.get("/scheduler/status")
def scheduler_status(request: Request) -> dict[str, Any]:
    """Top-level health/summary of the scheduler.

    Returns 503 if the scheduler isn't running (e.g., disabled in
    settings or failed to start).
    """
    return _scheduler(request).status()


@router.get("/scheduler/tasks")
def list_scheduled_tasks(request: Request) -> dict[str, Any]:
    """List every registered scheduled task with full per-task state."""
    sched = _scheduler(request)
    tasks = [_serialize_task(t) for t in sched.list_tasks()]
    return {"count": len(tasks), "tasks": tasks}


@router.get("/scheduler/tasks/{task_id}")
def get_scheduled_task(task_id: str, request: Request) -> dict[str, Any]:
    """One task's full state. 404 if no task with that id."""
    sched = _scheduler(request)
    task = sched.get_task(task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no scheduled task with id {task_id!r}",
        )
    return _serialize_task(task)
