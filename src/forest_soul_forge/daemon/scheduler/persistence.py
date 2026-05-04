"""SQLite persistence for the scheduler's per-task state.

ADR-0041 T5, Burst 90. Schema v13 ``scheduled_task_state`` table.

Keeps the audit chain as the source of truth — every state change
already emits ``scheduled_task_dispatched/completed/failed/...``
through ``Scheduler._dispatch`` (Burst 89). This table is the
**indexed view** so :meth:`Scheduler.start` can hydrate the
in-memory ``ScheduledTask.state`` in O(1) per task instead of
replaying the entire chain.

Two contracts:

* :class:`SchedulerStateRepo` — read-all + upsert-one. Read-all
  runs once at startup; upsert-one runs after every dispatch
  outcome, inside the daemon's write_lock so single-writer SQLite
  discipline (ADR-0001) is preserved.
* The repo is intentionally **dumb** — it doesn't know about
  ``ScheduledTask`` or ``Scheduler``. The scheduler runtime owns
  the read→hydrate and the post-dispatch upsert. That keeps the
  persistence layer testable with a bare sqlite connection.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class PersistedState:
    """Snapshot of one task's state as stored in
    ``scheduled_task_state``.

    Mirrors :class:`forest_soul_forge.daemon.scheduler.runtime.TaskState`
    but uses primitives only — datetime → ISO string, bool → int.
    The scheduler runtime is responsible for the typed conversion
    in both directions; the repo only speaks SQLite.
    """

    task_id: str
    last_run_at: str | None
    next_run_at: str | None
    consecutive_failures: int
    circuit_breaker_open: bool
    total_runs: int
    total_successes: int
    total_failures: int
    last_failure_reason: str | None
    last_run_outcome: str | None


class SchedulerStateRepo:
    """SQLite-backed repository for ``scheduled_task_state``.

    Construct once per daemon lifespan with the registry's
    connection. Both methods are safe to call from outside an
    explicit transaction — SQLite auto-wraps each statement in its
    own transaction unless one is already open. The scheduler
    holds the daemon's ``write_lock`` around upserts, so concurrent
    writers don't collide.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def read_all(self) -> dict[str, PersistedState]:
        """Return every persisted task state, keyed by task_id.

        Called exactly once on :meth:`Scheduler.start`. The
        scheduler iterates its registered tasks and applies the
        matching state; tasks without a persisted row keep their
        default-constructed ``TaskState`` (next_run_at=None →
        fires on first tick).
        """
        cur = self._conn.execute(
            "SELECT task_id, last_run_at, next_run_at, "
            "consecutive_failures, circuit_breaker_open, "
            "total_runs, total_successes, total_failures, "
            "last_failure_reason, last_run_outcome "
            "FROM scheduled_task_state"
        )
        rows = cur.fetchall()
        out: dict[str, PersistedState] = {}
        for r in rows:
            out[r[0]] = PersistedState(
                task_id=r[0],
                last_run_at=r[1],
                next_run_at=r[2],
                consecutive_failures=int(r[3]),
                circuit_breaker_open=bool(r[4]),
                total_runs=int(r[5]),
                total_successes=int(r[6]),
                total_failures=int(r[7]),
                last_failure_reason=r[8],
                last_run_outcome=r[9],
            )
        return out

    def upsert(self, state: PersistedState) -> None:
        """Insert or replace one task's persisted state.

        Called by :meth:`Scheduler._dispatch` after every outcome.
        The caller MUST hold the daemon's ``write_lock`` — the
        scheduler does so naturally because dispatches happen
        serially.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO scheduled_task_state ("
            "  task_id, last_run_at, next_run_at, "
            "  consecutive_failures, circuit_breaker_open, "
            "  total_runs, total_successes, total_failures, "
            "  last_failure_reason, last_run_outcome, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(task_id) DO UPDATE SET "
            "  last_run_at=excluded.last_run_at, "
            "  next_run_at=excluded.next_run_at, "
            "  consecutive_failures=excluded.consecutive_failures, "
            "  circuit_breaker_open=excluded.circuit_breaker_open, "
            "  total_runs=excluded.total_runs, "
            "  total_successes=excluded.total_successes, "
            "  total_failures=excluded.total_failures, "
            "  last_failure_reason=excluded.last_failure_reason, "
            "  last_run_outcome=excluded.last_run_outcome, "
            "  updated_at=excluded.updated_at",
            (
                state.task_id,
                state.last_run_at,
                state.next_run_at,
                state.consecutive_failures,
                int(state.circuit_breaker_open),
                state.total_runs,
                state.total_successes,
                state.total_failures,
                state.last_failure_reason,
                state.last_run_outcome,
                now_iso,
            ),
        )
        self._conn.commit()

    def delete(self, task_id: str) -> bool:
        """Remove a persisted task's row. Returns True if a row
        was removed. Used by future operator-control endpoints
        (Burst 92) when a scheduled task is permanently removed
        from config — without delete the row would linger and
        scheduler.start() would try to re-hydrate state for a
        task that no longer exists.
        """
        cur = self._conn.execute(
            "DELETE FROM scheduled_task_state WHERE task_id = ?",
            (task_id,),
        )
        self._conn.commit()
        return cur.rowcount > 0
