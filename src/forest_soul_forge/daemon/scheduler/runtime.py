"""Scheduler runtime — the asyncio poll loop + task registry.

ADR-0041 T2. Runs inside the daemon's asyncio loop alongside the
FastAPI app. Each tick scans registered tasks; for any task whose
``next_run`` is at-or-before now AND whose circuit breaker is
closed, dispatch the task type's runner.

What this burst implements:
- Scheduler lifecycle (start/stop/poll loop).
- ScheduledTask data model + TaskState (in-memory).
- Schedule parsing (interval-based via :mod:`.schedule`).
- Task registration from a config dict.
- The dispatch hook (calls a registered task type's runner) —
  task types themselves land in Burst 87.

What this burst does NOT do:
- Persistence. State is in-memory; daemon restart loses state.
  Burst 88 adds the SQLite v13 schema + read/write.
- Audit-chain emit. Tasks dispatch through this without yet
  emitting `scheduled_task_dispatched` etc. Burst 87 adds that
  alongside the first task type.
- HTTP control endpoints (trigger/enable/disable/reset). Burst
  89 adds those. Status/list endpoint exists in this burst.

The split is deliberate: ship the heartbeat first, prove it
ticks, then layer task types on top. If the heartbeat is broken
we want to find out before we have a half-implemented scenario
runner sitting on top of it.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from forest_soul_forge.daemon.scheduler.schedule import (
    Schedule,
    ScheduleParseError,
    parse_schedule,
)

logger = logging.getLogger(__name__)

# A task-type runner is an async callable that accepts the task's
# config dict + a context dict (giving access to registry, audit
# chain, etc.) and returns an outcome dict. Burst 87 adds the
# first concrete runner (tool_call). Until then, the scheduler
# can run with zero registered runners — it just won't dispatch
# anything.
TaskRunner = Callable[[dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]]


def _redact_outcome(outcome: dict[str, Any]) -> dict[str, Any]:
    """Drop large/noisy fields from a runner outcome before logging
    it to the audit chain.

    The chain stores small structured events; if a runner returns a
    large blob (e.g., an LLM response), shoving it into ``event_data``
    bloats the chain and slows /audit/tail. Keep the small structured
    fields (ok, agent_id, tool, session_id, tokens_used, result_digest,
    error) and drop everything else. Burst 89 establishes the
    redaction discipline; future runners follow the same pattern.
    """
    keep = {
        "ok",
        "agent_id",
        "tool",
        "session_id",
        "tokens_used",
        "result_digest",
        "error",
    }
    return {k: v for k, v in outcome.items() if k in keep}


@dataclass
class TaskState:
    """In-memory runtime state for a :class:`ScheduledTask`.

    Burst 88 will mirror this to SQLite for survive-restart. For
    now it's reset on every daemon start.
    """

    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    consecutive_failures: int = 0
    circuit_breaker_open: bool = False
    total_runs: int = 0
    total_successes: int = 0
    total_failures: int = 0
    last_failure_reason: str | None = None
    last_run_outcome: str | None = None  # "succeeded" | "failed"


@dataclass
class ScheduledTask:
    """One configured task. Dataclass over Pydantic because the
    scheduler holds these in a hot loop; mutation has to be cheap.
    """

    id: str
    description: str
    schedule: Schedule
    task_type: str  # "tool_call" | "scenario" | (future)
    config: dict[str, Any]
    enabled: bool = True
    max_consecutive_failures: int = 3
    state: TaskState = field(default_factory=TaskState)

    def due(self, now: datetime) -> bool:
        """True iff the task should fire on this tick.

        A task is due when it's enabled, its circuit breaker is
        closed, and ``next_run_at <= now``. ``next_run_at`` is
        None for never-run tasks; treat as "fire on first tick".
        """
        if not self.enabled:
            return False
        if self.state.circuit_breaker_open:
            return False
        if self.state.next_run_at is None:
            return True
        return self.state.next_run_at <= now


class Scheduler:
    """The daemon-internal scheduler.

    Lifecycle::

        scheduler = Scheduler(...)
        await scheduler.start()   # spawns the poll task
        ...
        await scheduler.stop()    # cancels + awaits the task

    Registration::

        scheduler.register_task_type("tool_call", tool_call_runner)
        scheduler.add_task(ScheduledTask(...))

    The scheduler does not validate ``task_type`` against
    registered runners at ``add_task`` time on purpose — it only
    checks at dispatch time. That lets the operator configure
    tasks for runners not yet loaded (e.g., a plugin that adds a
    runner after startup) without rejecting the config.
    """

    def __init__(
        self,
        *,
        poll_interval_seconds: float = 30.0,
        context: dict[str, Any] | None = None,
    ) -> None:
        self._poll_interval = poll_interval_seconds
        self._context: dict[str, Any] = dict(context or {})
        self._tasks: dict[str, ScheduledTask] = {}
        self._runners: dict[str, TaskRunner] = {}
        self._poll_task: asyncio.Task | None = None
        self._stop_event: asyncio.Event = asyncio.Event()
        self._started = False
        self._lock = asyncio.Lock()

    # ---- task type registration -------------------------------------------
    def register_task_type(self, name: str, runner: TaskRunner) -> None:
        """Register a runner for a task type. Called at startup
        before :meth:`start` (or any time, but tasks of that type
        won't dispatch until the runner exists).
        """
        if name in self._runners:
            raise ValueError(f"task type {name!r} already registered")
        self._runners[name] = runner

    # ---- task management --------------------------------------------------
    def add_task(self, task: ScheduledTask) -> None:
        """Register a task. Idempotent on re-add of same id (last
        wins) — operators editing scheduled_tasks.yaml between
        daemon restarts shouldn't see duplicate-id errors.
        """
        self._tasks[task.id] = task

    def remove_task(self, task_id: str) -> bool:
        """Remove a task. Returns True if removed, False if not present."""
        return self._tasks.pop(task_id, None) is not None

    def get_task(self, task_id: str) -> ScheduledTask | None:
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[ScheduledTask]:
        return list(self._tasks.values())

    # ---- lifecycle --------------------------------------------------------
    async def start(self) -> None:
        if self._started:
            raise RuntimeError("scheduler already started")
        self._started = True
        self._stop_event.clear()
        self._poll_task = asyncio.create_task(
            self._run_loop(), name="forest-scheduler-poll"
        )
        logger.info(
            "scheduler started (poll_interval=%ss, tasks=%d, runners=%s)",
            self._poll_interval,
            len(self._tasks),
            sorted(self._runners.keys()),
        )

    async def stop(self) -> None:
        if not self._started:
            return
        self._stop_event.set()
        if self._poll_task is not None:
            try:
                # Wait for the loop to drain naturally — sleep is
                # interruptible via _stop_event in _run_loop.
                await asyncio.wait_for(self._poll_task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("scheduler poll task didn't stop in 5s; cancelling")
                self._poll_task.cancel()
                try:
                    await self._poll_task
                except (asyncio.CancelledError, Exception):
                    pass
            self._poll_task = None
        self._started = False
        logger.info("scheduler stopped")

    # ---- the loop ---------------------------------------------------------
    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._tick()
            except Exception:
                # The loop never dies. Individual task failures are
                # bounded by their own circuit breakers; a logic
                # error in the loop itself shouldn't take the
                # scheduler down.
                logger.exception("scheduler tick raised; continuing")

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._poll_interval
                )
            except asyncio.TimeoutError:
                pass  # interval elapsed; next tick

    async def _tick(self) -> None:
        """One pass over all tasks, dispatching due ones serially.

        Serial dispatch is deliberate for now: the underlying
        write_lock is RLock-based and would serialize them anyway,
        and serial is easier to reason about for the audit chain.
        Future tranches may parallelize read-only tasks.
        """
        now = datetime.now(timezone.utc)
        async with self._lock:
            tasks_snapshot = list(self._tasks.values())
        for task in tasks_snapshot:
            if not task.due(now):
                continue
            await self._dispatch(task, now)

    async def _dispatch(self, task: ScheduledTask, now: datetime) -> None:
        """Dispatch one task. Records outcome; updates schedule;
        flips circuit breaker on consecutive failures; emits audit
        events for every state transition.

        Audit emit policy (Burst 89):
        * ``scheduled_task_dispatched`` — before runner invocation,
          mirrors the dispatcher's pre-execute event. Pairs with one
          of completed/failed (or its absence is itself diagnostic
          if the daemon crashes mid-runner).
        * ``scheduled_task_completed`` — runner returned ok=True.
        * ``scheduled_task_failed`` — runner returned ok=False or
          raised.
        * ``scheduled_task_circuit_breaker_tripped`` — emitted exactly
          once when ``consecutive_failures`` first crosses
          ``max_consecutive_failures``. Subsequent ticks while the
          breaker is open never reach this method (``due()`` returns
          False), so this fires once per trip.

        Emits are best-effort — a failed audit append must NOT
        cause the scheduler to lose state. The audit chain is the
        evidence layer; if it's down, the scheduler still does its
        job and the operator sees the gap on chain inspection.
        """
        runner = self._runners.get(task.task_type)
        if runner is None:
            logger.warning(
                "task %s: no runner registered for type %r; skipping",
                task.id,
                task.task_type,
            )
            return

        task.state.last_run_at = now
        task.state.next_run_at = task.schedule.next_after(now, now)
        task.state.total_runs += 1

        self._emit_audit("scheduled_task_dispatched", {
            "task_id":       task.id,
            "task_type":     task.task_type,
            "description":   task.description,
            "scheduled_at":  now.isoformat(),
            "total_runs":    task.state.total_runs,
        })

        try:
            outcome = await runner(task.config, self._context)
            ok = bool(outcome.get("ok", True))
        except Exception as e:
            ok = False
            outcome = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            logger.exception("task %s runner raised", task.id)

        if ok:
            task.state.consecutive_failures = 0
            task.state.circuit_breaker_open = False
            task.state.total_successes += 1
            task.state.last_run_outcome = "succeeded"
            task.state.last_failure_reason = None
            self._emit_audit("scheduled_task_completed", {
                "task_id":       task.id,
                "task_type":     task.task_type,
                "outcome":       _redact_outcome(outcome),
                "total_successes": task.state.total_successes,
            })
        else:
            task.state.consecutive_failures += 1
            task.state.total_failures += 1
            task.state.last_run_outcome = "failed"
            task.state.last_failure_reason = str(outcome.get("error", "unknown"))
            self._emit_audit("scheduled_task_failed", {
                "task_id":       task.id,
                "task_type":     task.task_type,
                "error":         task.state.last_failure_reason,
                "consecutive_failures": task.state.consecutive_failures,
                "total_failures": task.state.total_failures,
            })
            if (
                task.state.consecutive_failures >= task.max_consecutive_failures
                and not task.state.circuit_breaker_open
            ):
                task.state.circuit_breaker_open = True
                logger.warning(
                    "task %s: circuit breaker tripped after %d consecutive failures",
                    task.id,
                    task.state.consecutive_failures,
                )
                self._emit_audit("scheduled_task_circuit_breaker_tripped", {
                    "task_id":       task.id,
                    "task_type":     task.task_type,
                    "consecutive_failures": task.state.consecutive_failures,
                    "max_consecutive_failures": task.max_consecutive_failures,
                    "last_error":    task.state.last_failure_reason,
                })

    def _emit_audit(self, event_type: str, payload: dict[str, Any]) -> None:
        """Best-effort audit emit. Never raises out of the scheduler."""
        chain = self._context.get("audit_chain")
        if chain is None:
            return
        try:
            chain.append(event_type, payload, agent_dna=None)
        except Exception:
            # The scheduler MUST keep running even if the chain is
            # down — it's the evidence layer, not the control plane.
            # Log and continue; the operator sees the gap on chain
            # inspection.
            logger.exception("scheduler audit emit failed for %s", event_type)

    # ---- introspection (for /scheduler/status) ----------------------------
    def status(self) -> dict[str, Any]:
        return {
            "running": self._started,
            "poll_interval_seconds": self._poll_interval,
            "registered_runners": sorted(self._runners.keys()),
            "task_count": len(self._tasks),
            "tasks_enabled": sum(1 for t in self._tasks.values() if t.enabled),
            "tasks_breaker_open": sum(
                1 for t in self._tasks.values() if t.state.circuit_breaker_open
            ),
        }


def build_task_from_config(spec: dict[str, Any]) -> ScheduledTask:
    """Build a :class:`ScheduledTask` from a config dict (one
    entry from ``scheduled_tasks.yaml``).

    Raises ValueError for malformed specs. Does NOT validate that
    ``task_type`` has a registered runner — the scheduler skips
    unregistered types at dispatch with a warning.
    """
    required = ("id", "description", "schedule", "type", "config")
    for key in required:
        if key not in spec:
            raise ValueError(f"task spec missing required key {key!r}")
    try:
        sched = parse_schedule(spec["schedule"])
    except ScheduleParseError as e:
        raise ValueError(f"task {spec['id']}: bad schedule: {e}") from e
    return ScheduledTask(
        id=str(spec["id"]),
        description=str(spec["description"]),
        schedule=sched,
        task_type=str(spec["type"]),
        config=dict(spec["config"]),
        enabled=bool(spec.get("enabled", True)),
        max_consecutive_failures=int(spec.get("max_consecutive_failures", 3)),
    )
