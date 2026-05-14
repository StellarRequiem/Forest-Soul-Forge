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


# ---- Persistence (Burst 90) ---------------------------------------------

def _fresh_persistence_db():
    """Build an in-memory SQLite with the current schema for testing.

    ADR-0075 T1 (v22, B293) bumped this DDL to include
    `budget_per_minute` and the matching partial index so the
    persistence module's SELECT/INSERT against the new column
    succeed under test. Schema kept inline (not loaded from
    `registry/schema.py`) so the test stays a focused
    persistence-layer probe rather than a full registry boot.
    """
    import sqlite3
    conn = sqlite3.connect(":memory:")
    # Apply only the scheduled_task_state DDL — no need for the full
    # schema in these tests.
    conn.execute("""
        CREATE TABLE scheduled_task_state (
            task_id                  TEXT PRIMARY KEY,
            last_run_at              TEXT,
            next_run_at              TEXT,
            consecutive_failures     INTEGER NOT NULL DEFAULT 0,
            circuit_breaker_open     INTEGER NOT NULL DEFAULT 0,
            total_runs               INTEGER NOT NULL DEFAULT 0,
            total_successes          INTEGER NOT NULL DEFAULT 0,
            total_failures           INTEGER NOT NULL DEFAULT 0,
            last_failure_reason      TEXT,
            last_run_outcome         TEXT,
            updated_at               TEXT NOT NULL,
            budget_per_minute        INTEGER NOT NULL DEFAULT 6
                CHECK (budget_per_minute >= 0)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_scheduled_task_state_next_run_at "
        "ON scheduled_task_state(next_run_at) "
        "WHERE next_run_at IS NOT NULL"
    )
    return conn


def test_persistence_repo_upsert_then_read_roundtrip():
    from forest_soul_forge.daemon.scheduler.persistence import (
        PersistedState,
        SchedulerStateRepo,
    )
    conn = _fresh_persistence_db()
    repo = SchedulerStateRepo(conn)
    state = PersistedState(
        task_id="verifier_24h",
        last_run_at="2026-05-04T10:00:00+00:00",
        next_run_at="2026-05-05T10:00:00+00:00",
        consecutive_failures=2,
        circuit_breaker_open=True,
        total_runs=10,
        total_successes=7,
        total_failures=3,
        last_failure_reason="provider_unavailable",
        last_run_outcome="failed",
    )
    repo.upsert(state)
    out = repo.read_all()
    assert "verifier_24h" in out
    got = out["verifier_24h"]
    assert got.consecutive_failures == 2
    assert got.circuit_breaker_open is True
    assert got.total_runs == 10
    assert got.last_failure_reason == "provider_unavailable"
    assert got.last_run_outcome == "failed"


def test_persistence_repo_upsert_overwrites():
    from forest_soul_forge.daemon.scheduler.persistence import (
        PersistedState,
        SchedulerStateRepo,
    )
    conn = _fresh_persistence_db()
    repo = SchedulerStateRepo(conn)
    s1 = PersistedState(
        task_id="t1", last_run_at=None, next_run_at=None,
        consecutive_failures=1, circuit_breaker_open=False,
        total_runs=1, total_successes=0, total_failures=1,
        last_failure_reason="first", last_run_outcome="failed",
    )
    repo.upsert(s1)
    s2 = PersistedState(
        task_id="t1", last_run_at="2026-05-04T11:00:00+00:00",
        next_run_at="2026-05-04T12:00:00+00:00",
        consecutive_failures=0, circuit_breaker_open=False,
        total_runs=2, total_successes=1, total_failures=1,
        last_failure_reason=None, last_run_outcome="succeeded",
    )
    repo.upsert(s2)
    out = repo.read_all()
    assert len(out) == 1
    assert out["t1"].consecutive_failures == 0
    assert out["t1"].last_run_outcome == "succeeded"


def test_persistence_repo_delete():
    from forest_soul_forge.daemon.scheduler.persistence import (
        PersistedState,
        SchedulerStateRepo,
    )
    conn = _fresh_persistence_db()
    repo = SchedulerStateRepo(conn)
    repo.upsert(PersistedState(
        task_id="t1", last_run_at=None, next_run_at=None,
        consecutive_failures=0, circuit_breaker_open=False,
        total_runs=0, total_successes=0, total_failures=0,
        last_failure_reason=None, last_run_outcome=None,
    ))
    assert repo.delete("t1") is True
    assert repo.delete("t1") is False  # idempotent on missing
    assert repo.read_all() == {}


def test_scheduler_persists_state_after_dispatch():
    """Every dispatch outcome upserts to the repo. Restart-survives."""
    from forest_soul_forge.daemon.scheduler.persistence import (
        SchedulerStateRepo,
    )
    async def run():
        async def runner(config, ctx):
            return {"ok": False, "error": "test failure"}
        conn = _fresh_persistence_db()
        repo = SchedulerStateRepo(conn)
        sched = Scheduler(state_repo=repo)
        sched.register_task_type("test_type", runner)
        task = _task(task_type="test_type", max_consecutive_failures=10)
        sched.add_task(task)
        await sched._dispatch(task, datetime.now(timezone.utc))
        # State was persisted.
        rows = repo.read_all()
        assert "t1" in rows
        assert rows["t1"].consecutive_failures == 1
        assert rows["t1"].total_failures == 1
        assert rows["t1"].last_failure_reason == "test failure"
        assert rows["t1"].last_run_outcome == "failed"
    asyncio.run(run())


def test_scheduler_hydrates_state_on_start():
    """On Scheduler.start, persisted rows mutate registered tasks."""
    from forest_soul_forge.daemon.scheduler.persistence import (
        PersistedState,
        SchedulerStateRepo,
    )
    async def run():
        conn = _fresh_persistence_db()
        repo = SchedulerStateRepo(conn)
        # Pre-populate as if a prior daemon had run this task and
        # tripped its breaker.
        repo.upsert(PersistedState(
            task_id="t1",
            last_run_at="2026-05-04T09:00:00+00:00",
            next_run_at="2026-05-04T10:00:00+00:00",
            consecutive_failures=3,
            circuit_breaker_open=True,
            total_runs=12, total_successes=9, total_failures=3,
            last_failure_reason="provider_unavailable",
            last_run_outcome="failed",
        ))
        sched = Scheduler(poll_interval_seconds=0.05, state_repo=repo)
        sched.add_task(_task())  # registers t1
        await sched.start()
        try:
            t = sched.get_task("t1")
            assert t.state.consecutive_failures == 3
            assert t.state.circuit_breaker_open is True
            assert t.state.total_runs == 12
            assert t.state.last_failure_reason == "provider_unavailable"
            assert t.state.last_run_outcome == "failed"
            # Datetime fields parsed back to aware datetimes.
            assert t.state.last_run_at is not None
            assert t.state.last_run_at.tzinfo is not None
        finally:
            await sched.stop()
    asyncio.run(run())


def test_scheduler_no_state_repo_works_in_memory_only():
    """Without a state_repo, the scheduler still functions; tests
    that don't care about persistence don't need to provide one."""
    async def run():
        async def runner(config, ctx):
            return {"ok": True}
        sched = Scheduler()
        sched.register_task_type("test_type", runner)
        sched.add_task(_task(task_type="test_type"))
        await sched.start()
        await asyncio.sleep(0.1)
        await sched.stop()
        # No assertion on persistence; just verify it doesn't crash.
    asyncio.run(run())


def test_scheduler_persist_failure_does_not_break_dispatch():
    """A broken state_repo must not fail the dispatch."""
    class _BrokenRepo:
        def read_all(self):
            return {}
        def upsert(self, _state):
            raise RuntimeError("disk full")
    async def run():
        called = []
        async def runner(config, ctx):
            called.append(1)
            return {"ok": True}
        sched = Scheduler(state_repo=_BrokenRepo())
        sched.register_task_type("test_type", runner)
        task = _task(task_type="test_type")
        sched.add_task(task)
        # Should not raise even though upsert raises.
        await sched._dispatch(task, datetime.now(timezone.utc))
        assert called == [1]
        assert task.state.total_successes == 1
    asyncio.run(run())


def test_parse_iso_or_none_handles_z_suffix_and_offset():
    from forest_soul_forge.daemon.scheduler.runtime import _parse_iso_or_none
    # +00:00 form
    dt = _parse_iso_or_none("2026-05-04T10:00:00+00:00")
    assert dt is not None and dt.tzinfo is not None
    # Z form (3.10 needs the Z->+00:00 fixup)
    dt2 = _parse_iso_or_none("2026-05-04T10:00:00Z")
    assert dt2 is not None and dt2.tzinfo is not None
    # None passthrough
    assert _parse_iso_or_none(None) is None
    assert _parse_iso_or_none("") is None
    # Garbage doesn't crash
    assert _parse_iso_or_none("not a date") is None


# ---- Operator control (Burst 91, ADR-0041 T6) ---------------------------

def test_trigger_dispatches_immediately():
    """trigger() runs the task right now, even if next_run_at is future."""
    async def run():
        calls = []
        async def runner(config, ctx):
            calls.append(1)
            return {"ok": True}
        sched = Scheduler()
        sched.register_task_type("test_type", runner)
        task = _task(task_type="test_type")
        # Make next_run_at far in the future so a normal tick wouldn't fire.
        task.state.next_run_at = datetime(2099, 1, 1, tzinfo=timezone.utc)
        sched.add_task(task)
        result = await sched.trigger("t1")
        assert result["ok"] is True
        assert result["outcome"] == "succeeded"
        assert len(calls) == 1
        # Manual trigger STILL counts as a real run.
        assert task.state.total_runs == 1
        assert task.state.total_successes == 1
    asyncio.run(run())


def test_trigger_unknown_task_returns_not_found():
    async def run():
        sched = Scheduler()
        result = await sched.trigger("nope")
        assert result == {"ok": False, "reason": "task_not_found"}
    asyncio.run(run())


def test_trigger_disabled_task_returns_disabled():
    async def run():
        async def runner(config, ctx):
            return {"ok": True}
        sched = Scheduler()
        sched.register_task_type("test_type", runner)
        sched.add_task(_task(task_type="test_type", enabled=False))
        result = await sched.trigger("t1")
        assert result == {"ok": False, "reason": "task_disabled"}
    asyncio.run(run())


def test_trigger_breaker_open_refuses():
    async def run():
        async def runner(config, ctx):
            return {"ok": True}
        sched = Scheduler()
        sched.register_task_type("test_type", runner)
        task = _task(task_type="test_type")
        task.state.circuit_breaker_open = True
        sched.add_task(task)
        result = await sched.trigger("t1")
        assert result == {"ok": False, "reason": "circuit_breaker_open"}
    asyncio.run(run())


def test_set_enabled_toggles_and_emits_audit():
    chain = _FakeChain()
    sched = Scheduler(context={"audit_chain": chain})
    sched.add_task(_task())
    assert sched.set_enabled("t1", False) is True
    assert sched.get_task("t1").enabled is False
    assert sched.set_enabled("t1", True) is True
    assert sched.get_task("t1").enabled is True
    types = [e[0] for e in chain.events]
    assert types == ["scheduled_task_disabled", "scheduled_task_enabled"]


def test_set_enabled_unknown_task_returns_false():
    sched = Scheduler()
    assert sched.set_enabled("nope", True) is False


def test_reset_clears_breaker_and_counters():
    chain = _FakeChain()
    sched = Scheduler(context={"audit_chain": chain})
    task = _task()
    task.state.consecutive_failures = 5
    task.state.circuit_breaker_open = True
    task.state.last_failure_reason = "old failure"
    task.state.last_run_outcome = "failed"  # NOT cleared by reset
    sched.add_task(task)
    assert sched.reset("t1") is True
    t = sched.get_task("t1")
    assert t.state.consecutive_failures == 0
    assert t.state.circuit_breaker_open is False
    assert t.state.last_failure_reason is None
    # last_run_outcome left intact so operator can see context.
    assert t.state.last_run_outcome == "failed"
    assert chain.events[0][0] == "scheduled_task_circuit_breaker_reset"


def test_reset_unknown_task_returns_false():
    sched = Scheduler()
    assert sched.reset("nope") is False


def test_reset_persists_cleared_state():
    """After reset, the persisted row reflects the cleared state."""
    from forest_soul_forge.daemon.scheduler.persistence import (
        SchedulerStateRepo,
    )
    conn = _fresh_persistence_db()
    repo = SchedulerStateRepo(conn)
    sched = Scheduler(state_repo=repo)
    task = _task()
    task.state.consecutive_failures = 3
    task.state.circuit_breaker_open = True
    sched.add_task(task)
    assert sched.reset("t1") is True
    rows = repo.read_all()
    assert rows["t1"].consecutive_failures == 0
    assert rows["t1"].circuit_breaker_open is False
