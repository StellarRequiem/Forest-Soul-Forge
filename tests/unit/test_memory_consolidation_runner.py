"""ADR-0074 T4 — memory_consolidation scheduler task-type runner.

The runner (daemon/scheduler/task_types/memory_consolidation.py) is
the thin daemon-side wrapper that resolves the registry connection,
the active provider, and the write lock from the scheduler context,
then calls core.memory_consolidation.run_consolidation_pass. The
end-to-end pass itself is covered by test_memory_consolidation_selector.py;
this file pins the wrapper's context-resolution + config handling.

Coverage:
  - happy path: aged episodic entries → pass runs, ok=True, result
    fields surfaced, sources flipped to consolidated, bookend events
  - empty DB → ok=True with zero counts
  - missing 'app'/'registry' in context → ok=False
  - missing write_lock → ok=False
  - invalid policy config (negative min_age_days) → ok=False
  - config policy knob (min_age_days) is honored
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from forest_soul_forge.daemon.scheduler.task_types import (
    memory_consolidation_runner,
)
from forest_soul_forge.registry.schema import MIGRATIONS


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

class _MockProvider:
    async def complete(self, prompt, *, task_kind=None, max_tokens=None, **kw):
        return "mock summary"


class _MockChain:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def append(self, event_type, payload, *, agent_dna=None):
        self.events.append((event_type, dict(payload)))


class _Providers:
    def __init__(self, provider):
        self._provider = provider

    def active(self):
        return self._provider


class _State:
    pass


class _App:
    def __init__(self, state):
        self.state = state


class _Registry:
    def __init__(self, conn):
        self._conn = conn


def _fresh_db() -> sqlite3.Connection:
    """In-memory SQLite at schema v23 with one pre-seeded agent 'a1'."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("CREATE TABLE agents (instance_id TEXT PRIMARY KEY)")
    conn.execute(
        """
        CREATE TABLE memory_entries (
            entry_id TEXT PRIMARY KEY,
            instance_id TEXT NOT NULL,
            agent_dna TEXT NOT NULL,
            layer TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT 'private',
            content TEXT NOT NULL,
            content_digest TEXT NOT NULL,
            tags_json TEXT NOT NULL DEFAULT '[]',
            consented_to_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            deleted_at TEXT,
            disclosed_from_entry TEXT,
            disclosed_summary TEXT,
            disclosed_at TEXT,
            claim_type TEXT NOT NULL DEFAULT 'observation',
            confidence TEXT NOT NULL DEFAULT 'medium',
            last_challenged_at TEXT,
            content_encrypted INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    for stmt in MIGRATIONS[23]:
        conn.execute(stmt)
    conn.execute("INSERT INTO agents (instance_id) VALUES ('a1')")
    return conn


def _seed(conn: sqlite3.Connection, entry_id: str, *, age_days: int = 60) -> None:
    """Seed one pending episodic observation. created_at is computed
    from the real clock so eligibility doesn't depend on the absolute
    date — only on age relative to the runner's own datetime.now()."""
    created = (
        datetime.now(timezone.utc) - timedelta(days=age_days)
    ).isoformat()
    conn.execute(
        "INSERT INTO memory_entries ("
        "  entry_id, instance_id, agent_dna, layer, content, "
        "  content_digest, created_at, claim_type, consolidation_state"
        ") VALUES (?, 'a1', 'dna', 'episodic', ?, 'd', ?, "
        "'observation', 'pending')",
        (entry_id, f"content_{entry_id}", created),
    )


def _context(
    conn: sqlite3.Connection,
    *,
    with_app: bool = True,
    with_registry: bool = True,
    with_lock: bool = True,
    audit_chain=None,
) -> dict:
    state = _State()
    if with_lock:
        state.write_lock = threading.RLock()
    state.providers = _Providers(_MockProvider())
    ctx: dict = {"audit_chain": audit_chain}
    if with_app:
        ctx["app"] = _App(state)
    if with_registry:
        ctx["registry"] = _Registry(conn)
    return ctx


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_runner_happy_path_runs_pass_and_returns_ok():
    conn = _fresh_db()
    for i in range(3):
        _seed(conn, f"e_{i}", age_days=60 + i)
    chain = _MockChain()
    out = asyncio.run(memory_consolidation_runner(
        {}, _context(conn, audit_chain=chain),
    ))
    assert out["ok"] is True
    assert out["sources_consolidated"] == 3
    assert out["summaries_created"] == 1
    assert out["batches_processed"] == 1
    assert out["run_id"]
    assert out["errors"] == []
    # DB state flipped: 3 sources consolidated, 1 summary inserted.
    states = [
        r[0] for r in conn.execute(
            "SELECT consolidation_state FROM memory_entries"
        ).fetchall()
    ]
    assert states.count("consolidated") == 3
    assert states.count("summary") == 1
    # Bookend audit events emitted through run_consolidation_pass.
    types = [e[0] for e in chain.events]
    assert "memory_consolidation_run_started" in types
    assert "memory_consolidation_run_completed" in types


def test_runner_empty_db_is_clean_ok():
    conn = _fresh_db()
    out = asyncio.run(memory_consolidation_runner({}, _context(conn)))
    assert out["ok"] is True
    assert out["sources_consolidated"] == 0
    assert out["summaries_created"] == 0


def test_runner_missing_app_or_registry_returns_ok_false():
    conn = _fresh_db()
    out = asyncio.run(memory_consolidation_runner(
        {}, _context(conn, with_app=False),
    ))
    assert out["ok"] is False
    assert "app" in out["error"] or "registry" in out["error"]


def test_runner_missing_write_lock_returns_ok_false():
    conn = _fresh_db()
    out = asyncio.run(memory_consolidation_runner(
        {}, _context(conn, with_lock=False),
    ))
    assert out["ok"] is False
    assert "write_lock" in out["error"]


def test_runner_invalid_policy_config_returns_ok_false():
    conn = _fresh_db()
    out = asyncio.run(memory_consolidation_runner(
        {"min_age_days": -5}, _context(conn),
    ))
    assert out["ok"] is False
    assert "policy" in out["error"]


def test_runner_honors_min_age_days_override():
    """A 5-day-old entry is below the default 14-day floor but
    becomes eligible once config drops min_age_days to 0."""
    conn = _fresh_db()
    _seed(conn, "e_recent", age_days=5)
    # Default policy (min_age_days=14): the 5-day entry is too young.
    out_default = asyncio.run(memory_consolidation_runner(
        {}, _context(conn),
    ))
    assert out_default["ok"] is True
    assert out_default["sources_consolidated"] == 0
    # Override min_age_days=0: the entry is now eligible.
    out_override = asyncio.run(memory_consolidation_runner(
        {"min_age_days": 0}, _context(conn),
    ))
    assert out_override["ok"] is True
    assert out_override["sources_consolidated"] == 1
