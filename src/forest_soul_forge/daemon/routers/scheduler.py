"""``/scheduler`` router — observability + operator control for the ADR-0041 orchestrator.

Burst 86 (T2) shipped GET endpoints (status / tasks / one-task).
Burst 91 (T6) adds the four POST endpoints from ADR-0041:

- ``POST /scheduler/tasks/{id}/trigger`` — force-run now, out-of-band.
- ``POST /scheduler/tasks/{id}/enable`` — un-pause the task.
- ``POST /scheduler/tasks/{id}/disable`` — pause the task; in-flight
  dispatches complete normally, no new ones start.
- ``POST /scheduler/tasks/{id}/reset`` — clear the circuit breaker
  and zero the consecutive_failures counter.

POSTs are gated by ``require_writes_enabled + require_api_token``
(same posture as the writes routes). Reads remain ungated — same
posture as audit/health.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from forest_soul_forge.daemon.deps import (
    require_api_token,
    require_writes_enabled,
)
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


def _serialize_task(task, scheduler: Scheduler | None = None) -> dict[str, Any]:
    """Render a ScheduledTask as a JSON-friendly dict. Includes
    runtime state so the operator can see last_run / next_run /
    failure counters at a glance.

    ADR-0075 T4 (B297) extension: when a scheduler is supplied,
    pulls the live dispatch-window count for this task so the
    operator sees "how close to budget" alongside the configured
    budget itself. Window count is best-effort — a task that's
    never dispatched has no window deque.
    """
    in_window = 0
    if scheduler is not None:
        # noqa: SLF001 — accessing the private dict is the documented
        # interface for /scheduler/status; treating it as a typed
        # accessor surface would require a separate ADR.
        in_window = len(scheduler._dispatch_windows.get(task.id, ()))
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
        # ADR-0075 T4 (B297): budget + window snapshot.
        "budget_per_minute": task.budget_per_minute,
        "dispatches_in_window": in_window,
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
    tasks = [_serialize_task(t, sched) for t in sched.list_tasks()]
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
    return _serialize_task(task, sched)


# ---- Operator control (Burst 91, ADR-0041 T6) ---------------------------
# All gated by require_writes_enabled + require_api_token. Same posture as
# the /writes routes: 403 fires before 401 when writes are disabled, which
# is the more informative error.

@router.post(
    "/scheduler/tasks/{task_id}/trigger",
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
async def trigger_scheduled_task(task_id: str, request: Request) -> dict[str, Any]:
    """Force-run a task immediately, out-of-band. Counts as a real
    run (updates next_run_at, increments total_runs). Returns 404
    if the task is unknown, 409 if it's disabled or its breaker is
    open — operator must enable / reset first.
    """
    sched = _scheduler(request)
    if sched.get_task(task_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no scheduled task with id {task_id!r}",
        )
    result = await sched.trigger(task_id)
    if not result.get("ok"):
        # task_disabled / circuit_breaker_open are operator-recoverable
        # states, not "task not found" — 409 Conflict is more accurate
        # than 404 here.
        reason = result.get("reason", "unknown")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"cannot trigger task {task_id!r}: {reason}",
        )
    return result


@router.post(
    "/scheduler/tasks/{task_id}/enable",
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
def enable_scheduled_task(task_id: str, request: Request) -> dict[str, Any]:
    """Un-pause a task so it dispatches on its next due tick."""
    sched = _scheduler(request)
    if not sched.set_enabled(task_id, True):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no scheduled task with id {task_id!r}",
        )
    return {"ok": True, "task_id": task_id, "enabled": True}


@router.post(
    "/scheduler/tasks/{task_id}/disable",
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
def disable_scheduled_task(task_id: str, request: Request) -> dict[str, Any]:
    """Pause a task — no new dispatches until enabled. In-flight
    dispatches complete normally."""
    sched = _scheduler(request)
    if not sched.set_enabled(task_id, False):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no scheduled task with id {task_id!r}",
        )
    return {"ok": True, "task_id": task_id, "enabled": False}


@router.post(
    "/scheduler/tasks/{task_id}/reset",
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
def reset_scheduled_task(task_id: str, request: Request) -> dict[str, Any]:
    """Clear the circuit breaker + zero consecutive_failures. Used
    after the operator has fixed whatever was making the task fail.
    Persists the cleared state."""
    sched = _scheduler(request)
    if not sched.reset(task_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no scheduled task with id {task_id!r}",
        )
    return {
        "ok": True,
        "task_id": task_id,
        "circuit_breaker_open": False,
        "consecutive_failures": 0,
    }
