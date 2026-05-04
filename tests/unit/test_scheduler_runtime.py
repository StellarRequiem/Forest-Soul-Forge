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


# ---- Audit emit (Burst 89) ----------------------------------------------

class _FakeChain:
    """Captures audit.append calls so tests can assert event sequence."""

    def __init__(self, raise_on_append: bool = False):
        self.events: list[tuple[str, dict]] = []
        self._raise = raise_on_append

    def append(self, event_type, payload, *, agent_dna=None):
        if self._raise:
            raise RuntimeError("simulated chain failure")
        self.events.append((event_type, dict(payload)))


def test_dispatch_emits_dispatched_then_completed_on_success():
    async def run():
        async def runner(config, ctx):
            return {"ok": True, "agent_id": "a1", "tool": "x.v1"}
        chain = _FakeChain()
        sched = Scheduler(context={"audit_chain": chain})
        sched.register_task_type("test_type", runner)
        task = _task(task_type="test_type")
        sched.add_task(task)
        await sched._dispatch(task, datetime.now(timezone.utc))
        types = [e[0] for e in chain.events]
        assert types == ["scheduled_task_dispatched", "scheduled_task_completed"]
        # Dispatched event has scheduled_at + total_runs
        assert chain.events[0][1]["task_id"] == "t1"
        assert chain.events[0][1]["total_runs"] == 1
        # Completed event has the redacted outcome
        assert chain.events[1][1]["outcome"] == {"ok": True, "agent_id": "a1", "tool": "x.v1"}
    asyncio.run(run())


def test_dispatch_emits_dispatched_then_failed_on_failure():
    async def run():
        async def runner(config, ctx):
            return {"ok": False, "error": "kaboom"}
        chain = _FakeChain()
        sched = Scheduler(context={"audit_chain": chain})
        sched.register_task_type("test_type", runner)
        task = _task(task_type="test_type", max_consecutive_failures=10)
        sched.add_task(task)
        await sched._dispatch(task, datetime.now(timezone.utc))
        types = [e[0] for e in chain.events]
        assert types == ["scheduled_task_dispatched", "scheduled_task_failed"]
        assert chain.events[1][1]["error"] == "kaboom"
        assert chain.events[1][1]["consecutive_failures"] == 1
    asyncio.run(run())


def test_dispatch_emits_breaker_tripped_exactly_once_on_threshold():
    async def run():
        async def runner(config, ctx):
            return {"ok": False, "error": "fail"}
        chain = _FakeChain()
        sched = Scheduler(context={"audit_chain": chain})
        sched.register_task_type("test_type", runner)
        task = _task(task_type="test_type", max_consecutive_failures=2)
        sched.add_task(task)
        now = datetime.now(timezone.utc)
        await sched._dispatch(task, now)  # failure 1
        await sched._dispatch(task, now)  # failure 2 → trip
        types = [e[0] for e in chain.events]
        # Each dispatch emits dispatched + failed; the second dispatch
        # also emits breaker_tripped.
        assert types == [
            "scheduled_task_dispatched", "scheduled_task_failed",
            "scheduled_task_dispatched", "scheduled_task_failed",
            "scheduled_task_circuit_breaker_tripped",
        ]
        # Tripped event has the threshold + last_error
        tripped = chain.events[-1][1]
        assert tripped["consecutive_failures"] == 2
        assert tripped["max_consecutive_failures"] == 2
        assert tripped["last_error"] == "fail"
    asyncio.run(run())


def test_dispatch_audit_emit_failure_does_not_break_scheduler():
    """A broken audit chain must not stop the scheduler from doing its job.

    The chain is the evidence layer; if it's down, the operator sees
    the gap on chain inspection. Better to lose the audit event than
    to lose the dispatch entirely.
    """
    async def run():
        called = []
        async def runner(config, ctx):
            called.append(1)
            return {"ok": True}
        broken_chain = _FakeChain(raise_on_append=True)
        sched = Scheduler(context={"audit_chain": broken_chain})
        sched.register_task_type("test_type", runner)
        task = _task(task_type="test_type")
        sched.add_task(task)
        # Should not raise even though chain.append raises
        await sched._dispatch(task, datetime.now(timezone.utc))
        assert called == [1]
        assert task.state.total_successes == 1
    asyncio.run(run())


def test_dispatch_no_audit_chain_in_context_is_silent():
    """Scheduler still runs when audit_chain is missing entirely.

    Tests that don't pass a chain should work; lifespan-failure
    contexts where audit_chain didn't load should also work.
    """
    async def run():
        async def runner(config, ctx):
            return {"ok": True}
        sched = Scheduler(context={})  # no audit_chain key
        sched.register_task_type("test_type", runner)
        task = _task(task_type="test_type")
        sched.add_task(task)
        await sched._dispatch(task, datetime.now(timezone.utc))
        assert task.state.total_successes == 1
    asyncio.run(run())


# ---- _redact_outcome (Burst 89) -----------------------------------------

def test_redact_outcome_keeps_small_fields():
    from forest_soul_forge.daemon.scheduler.runtime import _redact_outcome
    raw = {
        "ok": True, "agent_id": "a1", "tool": "x.v1",
        "session_id": "s", "tokens_used": 42,
        "result_digest": "abc", "error": None,
    }
    assert _redact_outcome(raw) == raw


def test_redact_outcome_drops_large_fields():
    from forest_soul_forge.daemon.scheduler.runtime import _redact_outcome
    raw = {
        "ok": True,
        "agent_id": "a1",
        "raw_output": "x" * 100_000,  # huge LLM blob
        "metadata": {"deeply": {"nested": "junk"}},
    }
    out = _redact_outcome(raw)
    assert out == {"ok": True, "agent_id": "a1"}
    assert "raw_output" not in out
    assert "metadata" not in out


# ---- tool_call_runner (Burst 89) ----------------------------------------

def test_tool_call_runner_rejects_missing_required_keys():
    from forest_soul_forge.daemon.scheduler.task_types.tool_call import (
        tool_call_runner,
    )
    async def run():
        out = await tool_call_runner({}, {})
        assert out["ok"] is False
        assert "missing required keys" in out["error"]
        assert "agent_id" in out["error"]
        assert "tool_name" in out["error"]
        assert "tool_version" in out["error"]
    asyncio.run(run())


def test_tool_call_runner_rejects_missing_context():
    from forest_soul_forge.daemon.scheduler.task_types.tool_call import (
        tool_call_runner,
    )
    async def run():
        config = {"agent_id": "a1", "tool_name": "x", "tool_version": "1"}
        out = await tool_call_runner(config, {})  # empty context
        assert out["ok"] is False
        assert "missing 'app' or 'registry'" in out["error"]
    asyncio.run(run())


def test_tool_call_runner_handles_agent_lookup_failure():
    from forest_soul_forge.daemon.scheduler.task_types.tool_call import (
        tool_call_runner,
    )
    class _FakeRegistry:
        def get_agent(self, _id):
            raise KeyError("agent not found")
    async def run():
        config = {"agent_id": "a1", "tool_name": "x", "tool_version": "1"}
        ctx = {"app": object(), "registry": _FakeRegistry()}
        out = await tool_call_runner(config, ctx)
        assert out["ok"] is False
        assert "lookup failed" in out["error"]
        assert "KeyError" in out["error"]
    asyncio.run(run())
