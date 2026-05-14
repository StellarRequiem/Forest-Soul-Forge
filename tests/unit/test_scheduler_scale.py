"""ADR-0075 T1 (B293) — scheduler scale substrate tests.

Covers the schema-additive substrate this tranche ships:

* Schema v22 migration adds `budget_per_minute` column with
  default 6 + CHECK (>= 0) on `scheduled_task_state`.
* Schema v22 migration adds partial index
  `idx_scheduled_task_state_next_run_at` filtered on
  `next_run_at IS NOT NULL`.
* `PersistedState` round-trips `budget_per_minute` through the
  `SchedulerStateRepo.upsert` / `read_all` path.
* `PersistedState` defaults `budget_per_minute` to 6 when callers
  don't supply it (matches ADR Decision 2's "ten-second floor"
  default and the column-level DEFAULT clause).
* `scheduler_lag` is registered in `KNOWN_EVENT_TYPES` so the
  audit chain accepts T2/T3 emits when those land.

Enforcement logic itself (T2: tick-over-budget detection; T3:
per-task sliding-window enforcement) is out of scope for T1 —
those bursts carry their own tests.
"""
from __future__ import annotations

import sqlite3

import pytest

from forest_soul_forge.core.audit_chain import KNOWN_EVENT_TYPES
from forest_soul_forge.daemon.scheduler.persistence import (
    PersistedState,
    SchedulerStateRepo,
)
from forest_soul_forge.registry.schema import (
    DDL_STATEMENTS,
    MIGRATIONS,
    SCHEMA_VERSION,
)


# ---------------------------------------------------------------------------
# Schema substrate
# ---------------------------------------------------------------------------

def test_schema_version_is_v22():
    """Schema version bumped to v22 for the ADR-0075 T1 migration."""
    assert SCHEMA_VERSION == 22


def test_v22_migration_present():
    """MIGRATIONS[22] exists and ships exactly the two ADR-0075 T1 statements:
    the ALTER TABLE adding `budget_per_minute` and the CREATE INDEX adding
    the partial `next_run_at` index."""
    assert 22 in MIGRATIONS
    stmts = MIGRATIONS[22]
    assert len(stmts) == 2
    joined = "\n".join(stmts)
    assert "ALTER TABLE scheduled_task_state" in joined
    assert "ADD COLUMN budget_per_minute" in joined
    assert "DEFAULT 6" in joined
    assert "CHECK (budget_per_minute >= 0)" in joined
    assert "idx_scheduled_task_state_next_run_at" in joined
    assert "WHERE next_run_at IS NOT NULL" in joined


def test_canonical_schema_has_budget_column_and_index():
    """The DDL_STATEMENTS used for fresh DB creation matches the
    post-migration shape — no fresh-vs-migrated drift."""
    joined = "\n".join(DDL_STATEMENTS)
    assert "budget_per_minute" in joined
    assert "idx_scheduled_task_state_next_run_at" in joined


# ---------------------------------------------------------------------------
# v22 migration applied to a v21-shaped DB
# ---------------------------------------------------------------------------

def _v21_scheduled_task_state(conn: sqlite3.Connection) -> None:
    """Apply the pre-v22 (v13-era) shape of `scheduled_task_state`
    so we can prove the v22 migration brings it forward.
    """
    conn.execute(
        """
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
            updated_at               TEXT NOT NULL
        )
        """
    )


def test_v22_migration_applies_cleanly_on_v21_table():
    """Running MIGRATIONS[22] against a v21-shaped table adds the column
    and the index without touching existing rows. ADR-0075 T1 promised
    pure-additive — this test pins the promise."""
    conn = sqlite3.connect(":memory:")
    _v21_scheduled_task_state(conn)
    # Pre-existing row to prove the migration doesn't disturb data.
    conn.execute(
        "INSERT INTO scheduled_task_state (task_id, updated_at) VALUES (?, ?)",
        ("legacy_task", "2026-05-13T00:00:00+00:00"),
    )
    conn.commit()

    for stmt in MIGRATIONS[22]:
        conn.execute(stmt)
    conn.commit()

    # Column added with the default for the pre-existing row.
    cur = conn.execute(
        "SELECT budget_per_minute FROM scheduled_task_state "
        "WHERE task_id = 'legacy_task'"
    )
    assert cur.fetchone()[0] == 6

    # Index registered under the right name.
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' "
        "AND name = 'idx_scheduled_task_state_next_run_at'"
    )
    assert cur.fetchone() is not None


def test_v22_budget_check_constraint_rejects_negative():
    """The CHECK constraint enforces budget >= 0. Soft-pause (0) is legal;
    negative values are not. The constraint is the schema-level guard for
    the T3 enforcement code that will trust the column value at runtime."""
    conn = sqlite3.connect(":memory:")
    _v21_scheduled_task_state(conn)
    for stmt in MIGRATIONS[22]:
        conn.execute(stmt)
    conn.commit()

    # 0 (soft-pause) is legal.
    conn.execute(
        "INSERT INTO scheduled_task_state "
        "(task_id, updated_at, budget_per_minute) VALUES (?, ?, ?)",
        ("paused_task", "2026-05-14T00:00:00+00:00", 0),
    )
    conn.commit()

    # Negative is refused at the SQL layer.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO scheduled_task_state "
            "(task_id, updated_at, budget_per_minute) VALUES (?, ?, ?)",
            ("broken_task", "2026-05-14T00:00:00+00:00", -1),
        )


# ---------------------------------------------------------------------------
# PersistedState round-trip
# ---------------------------------------------------------------------------

def _v22_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    _v21_scheduled_task_state(conn)
    for stmt in MIGRATIONS[22]:
        conn.execute(stmt)
    conn.commit()
    return conn


def test_persistedstate_defaults_budget_to_six():
    """ADR Decision 2 default: callers that don't supply budget_per_minute
    pick up 6. Pins the dataclass-level default against drift."""
    s = PersistedState(
        task_id="t1",
        last_run_at=None,
        next_run_at=None,
        consecutive_failures=0,
        circuit_breaker_open=False,
        total_runs=0,
        total_successes=0,
        total_failures=0,
        last_failure_reason=None,
        last_run_outcome=None,
    )
    assert s.budget_per_minute == 6


def test_repo_upsert_persists_budget_per_minute():
    """A non-default budget round-trips through the repo: upsert writes the
    column, read_all returns it."""
    conn = _v22_db()
    repo = SchedulerStateRepo(conn)
    repo.upsert(
        PersistedState(
            task_id="t_budget",
            last_run_at=None,
            next_run_at="2026-05-14T12:00:00+00:00",
            consecutive_failures=0,
            circuit_breaker_open=False,
            total_runs=0,
            total_successes=0,
            total_failures=0,
            last_failure_reason=None,
            last_run_outcome=None,
            budget_per_minute=2,
        )
    )
    rows = repo.read_all()
    assert "t_budget" in rows
    assert rows["t_budget"].budget_per_minute == 2


def test_repo_upsert_does_not_overwrite_budget_on_conflict():
    """ADR Decision 2 says budget is operator-owned: scheduler-driven
    outcome upserts must NOT stomp an out-of-band budget change. Pin the
    ON CONFLICT clause: the column update list excludes budget_per_minute,
    so the original INSERT value sticks even when subsequent upserts
    supply a different one."""
    conn = _v22_db()
    repo = SchedulerStateRepo(conn)
    # First upsert lands the row with budget=3 (operator's choice).
    repo.upsert(
        PersistedState(
            task_id="t_keep",
            last_run_at=None,
            next_run_at=None,
            consecutive_failures=0,
            circuit_breaker_open=False,
            total_runs=0,
            total_successes=0,
            total_failures=0,
            last_failure_reason=None,
            last_run_outcome=None,
            budget_per_minute=3,
        )
    )
    # Second upsert (simulating a dispatch outcome) carries the runtime
    # snapshot's budget value — which is 6 because the in-memory layer
    # hasn't been taught about the operator override yet. The repo must
    # keep the persisted 3, not stomp to 6.
    repo.upsert(
        PersistedState(
            task_id="t_keep",
            last_run_at="2026-05-14T12:30:00+00:00",
            next_run_at="2026-05-14T12:31:00+00:00",
            consecutive_failures=0,
            circuit_breaker_open=False,
            total_runs=1,
            total_successes=1,
            total_failures=0,
            last_failure_reason=None,
            last_run_outcome="succeeded",
            budget_per_minute=6,
        )
    )
    rows = repo.read_all()
    assert rows["t_keep"].budget_per_minute == 3
    # Outcome state still got captured (the rest of the upsert path works).
    assert rows["t_keep"].total_runs == 1
    assert rows["t_keep"].last_run_outcome == "succeeded"


def test_repo_read_all_defaults_budget_for_legacy_rows():
    """A row inserted before T3 enforcement logic exists still reads back
    with budget=6 because the v22 migration's DEFAULT clause covers
    pre-existing data."""
    conn = _v22_db()
    # Insert WITHOUT specifying budget_per_minute — simulates a row that
    # existed before the migration.
    conn.execute(
        "INSERT INTO scheduled_task_state "
        "(task_id, updated_at) VALUES (?, ?)",
        ("t_legacy", "2026-05-13T23:59:59+00:00"),
    )
    conn.commit()
    repo = SchedulerStateRepo(conn)
    rows = repo.read_all()
    assert "t_legacy" in rows
    assert rows["t_legacy"].budget_per_minute == 6


# ---------------------------------------------------------------------------
# Audit event registration
# ---------------------------------------------------------------------------

def test_scheduler_lag_event_registered():
    """`scheduler_lag` is in KNOWN_EVENT_TYPES so T2/T3 emits won't trip
    the audit verifier's unknown-event-type check."""
    assert "scheduler_lag" in KNOWN_EVENT_TYPES


# ---------------------------------------------------------------------------
# ADR-0075 T2 (B295) — tick_over_budget detection
# ---------------------------------------------------------------------------
#
# Substrate-level enforcement: Scheduler._tick() measures wall-clock
# duration; if a tick that dispatched >0 tasks exceeds the budget,
# emits scheduler_lag(reason="tick_over_budget"). Idle over-budget
# ticks DON'T emit — those are GC / OS scheduler hiccups, not
# scheduler problems, and emitting would spam the chain.

import asyncio
from datetime import datetime, timezone

from forest_soul_forge.daemon.scheduler.runtime import Scheduler
from forest_soul_forge.daemon.scheduler.schedule import parse_schedule
from forest_soul_forge.daemon.scheduler.runtime import ScheduledTask


class _RecordingChain:
    """Captures audit.append calls so assertions can target them."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def append(self, event_type, payload, *, agent_dna=None):
        self.events.append((event_type, dict(payload)))


def _slow_task(task_id: str, sleep_ms: float) -> ScheduledTask:
    """Build a task whose runner deliberately sleeps `sleep_ms` ms."""
    return ScheduledTask(
        id=task_id,
        description=f"slow task ({sleep_ms}ms)",
        schedule=parse_schedule("every 1h"),
        task_type="slow",
        config={"sleep_ms": sleep_ms},
        enabled=True,
        max_consecutive_failures=3,
    )


def test_tick_under_budget_does_not_emit_scheduler_lag():
    """A tick that finishes under budget MUST NOT emit. Pin the
    no-false-positive case so future refactors can't silently start
    spamming."""
    async def run():
        async def fast_runner(config, ctx):
            return {"ok": True}

        chain = _RecordingChain()
        # Generous 500ms budget; fast_runner takes microseconds.
        sched = Scheduler(
            context={"audit_chain": chain},
            tick_budget_ms=500.0,
        )
        sched.register_task_type("slow", fast_runner)
        sched.add_task(_slow_task("t1", 0))
        await sched._tick()
        lag_events = [e for e in chain.events if e[0] == "scheduler_lag"]
        assert lag_events == []

    asyncio.run(run())


def test_tick_over_budget_emits_scheduler_lag():
    """When a dispatching tick exceeds the budget, exactly one
    scheduler_lag(reason="tick_over_budget") event lands with the
    expected payload shape."""
    async def run():
        async def slow_runner(config, ctx):
            sleep_ms = float(config.get("sleep_ms", 50))
            await asyncio.sleep(sleep_ms / 1000.0)
            return {"ok": True}

        chain = _RecordingChain()
        # 10ms budget; runner sleeps 80ms — guaranteed over-budget.
        sched = Scheduler(
            context={"audit_chain": chain},
            tick_budget_ms=10.0,
        )
        sched.register_task_type("slow", slow_runner)
        sched.add_task(_slow_task("t1", 80))
        await sched._tick()

        lag_events = [e for e in chain.events if e[0] == "scheduler_lag"]
        assert len(lag_events) == 1, (
            f"expected exactly one scheduler_lag emit, got {chain.events!r}"
        )
        payload = lag_events[0][1]
        assert payload["reason"] == "tick_over_budget"
        assert payload["task_id"] is None
        assert payload["tick_budget_ms"] == 10.0
        assert payload["tick_duration_ms"] > 10.0
        assert payload["dispatches_in_tick"] == 1
        # T3-only fields are present and null in T2 payloads — payload
        # shape is locked across T2/T3 per ADR.
        assert payload["budget_per_minute"] is None
        assert payload["dispatches_in_window"] is None
        assert payload["details"] is None

    asyncio.run(run())


def test_tick_over_budget_with_zero_dispatches_does_not_emit():
    """An idle tick that happens to blow the budget (e.g., a GC pause)
    MUST NOT emit. ADR-0075 T2 explicit: only emit when at least one
    task fired AND the tick exceeded the budget."""
    async def run():
        chain = _RecordingChain()
        # Zero budget — every tick exceeds.
        sched = Scheduler(
            context={"audit_chain": chain},
            tick_budget_ms=0.0,
        )
        # No tasks registered — _tick has nothing to dispatch.
        await sched._tick()
        lag_events = [e for e in chain.events if e[0] == "scheduler_lag"]
        assert lag_events == []

    asyncio.run(run())


def test_tick_budget_default_is_500ms():
    """ADR Decision 3 pins the default at 500ms. Pin it at the
    constructor-default level so a refactor that changes the default
    becomes a visible test diff."""
    s = Scheduler()
    assert s._tick_budget_ms == 500.0
