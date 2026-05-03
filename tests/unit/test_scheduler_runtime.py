"""Unit tests for ADR-0041 T2 scheduler runtime.

Coverage targets:
- Schedule parsing (valid + invalid inputs)
- ScheduledTask.due() across enabled/breaker/next_run states
- Scheduler lifecycle (start/stop is idempotent + safe)
- Tick logic dispatches due tasks, skips not-due
- Failure path bumps consecutive_failures + trips breaker at threshold
- Success path clears counters
- build_task_from_config validates input
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

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


# ---- Schedule parsing ----------------------------------------------------

def test_parse_schedule_seconds():
    s = parse_schedule("every 30s")
    assert s.interval_seconds == 30


def test_parse_schedule_minutes():
    s = parse_schedule("every 5m")
    assert s.interval_seconds == 300


def test_parse_schedule_hours():
    s = parse_schedule("every 6h")
    assert s.interval_seconds == 21600


def test_parse_schedule_days():
    s = parse_schedule("every 2d")
    assert s.interval_seconds == 172800


def test_parse_schedule_case_insensitive():
    assert parse_schedule("Every 5M").interval_seconds == 300
    assert parse_schedule("EVERY 1H").interval_seconds == 3600


def test_parse_schedule_rejects_bare_int():
    with pytest.raises(ScheduleParseError):
        parse_schedule("30")


def test_parse_schedule_rejects_negative():
    with pytest.raises(ScheduleParseError):
        parse_schedule("every -5m")


def test_parse_schedule_rejects_zero():
    with pytest.raises(ScheduleParseError):
        parse_schedule("every 0s")


def test_parse_schedule_rejects_unknown_unit():
    with pytest.raises(ScheduleParseError):
        parse_schedule("every 5y")  # years not supported


def test_parse_schedule_rejects_cron_for_now():
    with pytest.raises(ScheduleParseError):
        parse_schedule("0 */6 * * *")


def test_parse_schedule_rejects_non_string():
    with pytest.raises(ScheduleParseError):
        parse_schedule(300)  # type: ignore


# ---- Schedule.next_after -------------------------------------------------

def test_schedule_next_after_first_run_uses_now():
    s = parse_schedule("every 1h")
    now = datetime(2026, 5, 3, 12, 0, tzinfo=timezone.utc)
    assert s.next_after(None, now) == now


def test_schedule_next_after_subsequent_uses_last():
    s = parse_schedule("every 1h")
    last = datetime(2026, 5, 3, 12, 0, tzinfo=timezone.utc)
    now = datetime(2026, 5, 3, 12, 5, tzinfo=timezone.utc)  # tick 5min later
    nxt = s.next_after(last, now)
    assert nxt == datetime(2026, 5, 3, 13, 0, tzinfo=timezone.utc)


# ---- ScheduledTask.due ---------------------------------------------------

def _task(**overrides):
    base = dict(
        id="t1",
        description="test task",
        schedule=parse_schedule("every 1h"),
        task_type="dummy",
        config={},
        enabled=True,
        max_consecutive_failures=3,
    )
    base.update(overrides)
    return ScheduledTask(**base)


def test_task_due_when_never_run():
    t = _task()
    assert t.due(datetime.now(timezone.utc))


def test_task_not_due_when_disabled():
    t = _task(enabled=False)
    assert not t.due(datetime.now(timezone.utc))


def test_task_not_due_when_breaker_open():
    t = _task()
    t.state.circuit_breaker_open = True
    assert not t.due(datetime.now(timezone.utc))


def test_task_due_when_next_run_past():
    t = _task()
    now = datetime(2026, 5, 3, 12, 0, tzinfo=timezone.utc)
    t.state.next_run_at = datetime(2026, 5, 3, 11, 0, tzinfo=timezone.utc)
    assert t.due(now)


def test_task_not_due_when_next_run_future():
    t = _task()
    now = datetime(2026, 5, 3, 12, 0, tzinfo=timezone.utc)
    t.state.next_run_at = datetime(2026, 5, 3, 13, 0, tzinfo=timezone.utc)
    assert not t.due(now)


# ---- Scheduler lifecycle -------------------------------------------------

def test_scheduler_start_stop():
    async def run():
        sched = Scheduler(poll_interval_seconds=0.05)
        await sched.start()
        assert sched.status()["running"]
        # let it tick a couple times
        await asyncio.sleep(0.15)
        await sched.stop()
        assert not sched.status()["running"]
    asyncio.run(run())


def test_scheduler_double_start_raises():
    async def run():
        sched = Scheduler(poll_interval_seconds=0.05)
        await sched.start()
        try:
            with pytest.raises(RuntimeError):
                await sched.start()
        finally:
            await sched.stop()
    asyncio.run(run())


def test_scheduler_stop_when_not_started_is_safe():
    async def run():
        sched = Scheduler()
        await sched.stop()  # no-op, no error
    asyncio.run(run())


# ---- Dispatch + outcome --------------------------------------------------

def test_scheduler_dispatches_due_task_on_tick():
    async def run():
        calls = []
        async def runner(config, ctx):
            calls.append(config["mark"])
            return {"ok": True}
        sched = Scheduler(poll_interval_seconds=0.05)
        sched.register_task_type("test_type", runner)
        sched.add_task(_task(task_type="test_type", config={"mark": "fired"}))
        await sched.start()
        await asyncio.sleep(0.2)  # several ticks
        await sched.stop()
        assert len(calls) >= 1
        assert calls[0] == "fired"
    asyncio.run(run())


def test_scheduler_skips_unknown_task_type():
    async def run():
        sched = Scheduler(poll_interval_seconds=0.05)
        sched.add_task(_task(task_type="not_registered"))
        await sched.start()
        await asyncio.sleep(0.15)
        await sched.stop()
        # Task should still have last_run_at None (no runner means skip)
        t = sched.get_task("t1")
        assert t.state.total_runs == 0
    asyncio.run(run())


def test_scheduler_failure_increments_counter():
    """Trip the breaker by dispatching directly multiple times.

    Using direct ._dispatch() instead of the poll loop because the
    interval-based schedule won't re-fire within a test's wall-clock
    window — first dispatch sets next_run_at to `now + 1h`.
    """
    async def run():
        async def runner(config, ctx):
            return {"ok": False, "error": "test failure"}
        sched = Scheduler(poll_interval_seconds=0.05)
        sched.register_task_type("test_type", runner)
        task = _task(task_type="test_type", max_consecutive_failures=2)
        sched.add_task(task)
        # Dispatch twice to trip a 2-failure breaker.
        now = datetime.now(timezone.utc)
        await sched._dispatch(task, now)
        await sched._dispatch(task, now)
        assert task.state.total_failures == 2
        assert task.state.circuit_breaker_open
        assert task.state.last_failure_reason == "test failure"
    asyncio.run(run())


def test_scheduler_runner_exception_counts_as_failure():
    async def run():
        async def runner(config, ctx):
            raise RuntimeError("boom")
        sched = Scheduler(poll_interval_seconds=0.05)
        sched.register_task_type("test_type", runner)
        sched.add_task(_task(task_type="test_type", max_consecutive_failures=1))
        await sched.start()
        await asyncio.sleep(0.15)
        await sched.stop()
        t = sched.get_task("t1")
        assert t.state.total_failures >= 1
        assert t.state.circuit_breaker_open
        assert "RuntimeError" in (t.state.last_failure_reason or "")
    asyncio.run(run())


def test_scheduler_success_clears_consecutive_failures():
    """Run a task that fails once then succeeds; counter should reset.

    Direct ._dispatch() pattern (see test_scheduler_failure_increments_counter
    for the rationale).
    """
    async def run():
        outcomes = iter([{"ok": False, "error": "first"}, {"ok": True}])
        async def runner(config, ctx):
            return next(outcomes)
        sched = Scheduler()
        sched.register_task_type("test_type", runner)
        task = _task(task_type="test_type", max_consecutive_failures=10)
        sched.add_task(task)
        now = datetime.now(timezone.utc)
        await sched._dispatch(task, now)
        assert task.state.consecutive_failures == 1
        await sched._dispatch(task, now)
        # success should reset
        assert task.state.consecutive_failures == 0
        assert task.state.last_run_outcome == "succeeded"
        assert not task.state.circuit_breaker_open
        assert task.state.total_successes == 1
        assert task.state.total_failures == 1
    asyncio.run(run())


# ---- build_task_from_config ----------------------------------------------

def test_build_task_from_config_minimal():
    spec = {
        "id": "x1",
        "description": "test",
        "schedule": "every 5m",
        "type": "tool_call",
        "config": {"agent_id": "a1", "tool_name": "noop"},
    }
    task = build_task_from_config(spec)
    assert task.id == "x1"
    assert task.task_type == "tool_call"
    assert task.schedule.interval_seconds == 300
    assert task.enabled  # default True
    assert task.max_consecutive_failures == 3  # default


def test_build_task_from_config_with_overrides():
    spec = {
        "id": "x2",
        "description": "test",
        "schedule": "every 1h",
        "type": "scenario",
        "config": {},
        "enabled": False,
        "max_consecutive_failures": 5,
    }
    task = build_task_from_config(spec)
    assert not task.enabled
    assert task.max_consecutive_failures == 5


def test_build_task_from_config_missing_field():
    with pytest.raises(ValueError, match="missing required key"):
        build_task_from_config({"id": "x", "description": "d"})


def test_build_task_from_config_bad_schedule():
    spec = {
        "id": "x3",
        "description": "d",
        "schedule": "garbage",
        "type": "tool_call",
        "config": {},
    }
    with pytest.raises(ValueError, match="bad schedule"):
        build_task_from_config(spec)
