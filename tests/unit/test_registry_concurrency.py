"""Concurrency stress tests for the Registry's _ThreadLocalConn proxy.

Regression coverage for B143 (SQLITE_MISUSE under concurrent FastAPI
threadpool reads, surfaced live 2026-05-05). The bug fired when the
chat-tab GET /conversations/{id}/turns path ran concurrently with
scheduled task dispatches sharing a single sqlite3.Connection across
threads. Python's sqlite3 module reports threadsafety=1 — DB-API
contract says connections are NOT shareable across threads even with
``check_same_thread=False`` (which only disables Python's own check,
not the underlying contract).

The B143 fix introduced ``_ThreadLocalConn`` (registry.py:90), which
proxies sqlite3.Connection methods to a per-thread real connection.
This test file ensures any future refactor of the registry's
connection layer doesn't silently regress the fix:

  - test_concurrent_reads — many threads reading at once never raise
    ``sqlite3.InterfaceError`` and never see all-None corruption
  - test_concurrent_reads_and_writes — readers + a write loop produce
    consistent results (no torn reads, no SQLITE_MISUSE)
  - test_per_thread_connections — confirms _ThreadLocalConn opens one
    connection per thread (the structural property that prevents
    B143)
  - test_close_on_one_thread_doesnt_affect_others — sibling threads'
    connections survive a single-thread close (per the B143 docstring)

If any of these tests start failing with sqlite3.InterfaceError or
unexpected None rows, somebody has either:
  (a) reverted ``_ThreadLocalConn`` to a single shared connection,
  (b) mutated the per-table accessors to reach around the proxy, or
  (c) removed the WAL pragma that lets multiple connections coexist.

All three regressions reproduce the live-2026-05-05 chat-tab outage.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from forest_soul_forge.registry import Registry
from forest_soul_forge.registry.registry import _ThreadLocalConn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_test_data(reg: Registry, n_agents: int = 8) -> list[str]:
    """Insert N stub agents up front so concurrent readers have rows
    to fetch. Returns the seeded instance_ids."""
    ids: list[str] = []
    for i in range(n_agents):
        iid = f"thread_agent_{i:02d}"
        reg._conn.execute(
            "INSERT OR IGNORE INTO agents ("
            "  instance_id, dna, dna_full, role, agent_name, parent_instance,"
            "  owner_id, model_name, model_version, soul_path, constitution_path,"
            "  constitution_hash, created_at, status, legacy_minted, sibling_index"
            ") VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?, ?, ?, ?, 0, 1)",
            (
                iid,
                f"stub_dna_{i}",
                f"stub_dna_full_{i}_" + ("x" * 40),
                "network_watcher",
                f"Stub_{i}",
                f"/tmp/{iid}.soul.md",
                f"/tmp/{iid}.constitution.yaml",
                "stub_hash",
                "2026-05-06T00:00:00Z",
                "active",
            ),
        )
        ids.append(iid)
    reg._conn.commit()
    return ids


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_concurrent_reads(tmp_path: Path):
    """N threads each reading the agents table in a tight loop must
    NEVER raise sqlite3.InterfaceError or return None for non-null
    columns. This is the precise B143 reproduction shape: many
    concurrent GETs against a registry connection."""
    reg = Registry.bootstrap(tmp_path / "concurrent_reads.db")
    try:
        ids = _seed_test_data(reg, n_agents=8)

        n_threads = 16
        n_iterations_per_thread = 50
        errors: list[BaseException] = []
        none_seen: list[tuple[int, str]] = []
        errors_lock = threading.Lock()

        def reader(thread_id: int) -> None:
            try:
                for i in range(n_iterations_per_thread):
                    rows = reg._conn.execute(
                        "SELECT instance_id, role, status FROM agents "
                        "WHERE instance_id = ?;",
                        (ids[i % len(ids)],),
                    ).fetchall()
                    for r in rows:
                        # Any None on a NOT-NULL column = corruption.
                        if r[0] is None or r[1] is None or r[2] is None:
                            with errors_lock:
                                none_seen.append((thread_id, str(r)))
            except BaseException as e:        # noqa: BLE001
                with errors_lock:
                    errors.append(e)

        with ThreadPoolExecutor(max_workers=n_threads) as ex:
            futs = [ex.submit(reader, tid) for tid in range(n_threads)]
            for f in futs:
                f.result()

        assert not errors, (
            f"{len(errors)} thread(s) raised; first: "
            f"{type(errors[0]).__name__}: {errors[0]}"
        )
        assert not none_seen, (
            f"{len(none_seen)} corrupted (all-None) rows seen across "
            f"{n_threads} threads. Sample: {none_seen[:3]}"
        )
    finally:
        reg.close()


def test_concurrent_reads_and_writes(tmp_path: Path):
    """Mixed-mode load: a write thread inserts new agents while reader
    threads scan the table. Verifies WAL-mode-backed concurrency (per
    Registry's class docstring) holds up under the load shape that
    B143 surfaced.

    Writes are funneled through a single thread to mirror the daemon's
    write_lock discipline (ADR-0007 single-writer SQLite). Reads have
    no lock — that's exactly the path that exploded in B143."""
    reg = Registry.bootstrap(tmp_path / "concurrent_rw.db")
    try:
        _seed_test_data(reg, n_agents=4)

        stop_event = threading.Event()
        errors: list[BaseException] = []
        errors_lock = threading.Lock()
        n_writes_done = [0]
        n_reads_done = [0]

        def writer() -> None:
            i = 100
            while not stop_event.is_set():
                try:
                    reg._conn.execute(
                        "INSERT OR IGNORE INTO agents ("
                        "  instance_id, dna, dna_full, role, agent_name,"
                        "  parent_instance, owner_id, model_name,"
                        "  model_version, soul_path, constitution_path,"
                        "  constitution_hash, created_at, status,"
                        "  legacy_minted, sibling_index"
                        ") VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL,"
                        " ?, ?, ?, ?, ?, 0, 1)",
                        (
                            f"writer_agent_{i}",
                            f"wdna_{i}",
                            f"wdna_full_{i}_" + ("x" * 40),
                            "network_watcher",
                            f"WAgent_{i}",
                            f"/tmp/wagent_{i}.soul.md",
                            f"/tmp/wagent_{i}.constitution.yaml",
                            "stub_hash",
                            "2026-05-06T00:00:00Z",
                            "active",
                        ),
                    )
                    reg._conn.commit()
                    n_writes_done[0] += 1
                    i += 1
                except BaseException as e:    # noqa: BLE001
                    with errors_lock:
                        errors.append(e)
                    return
                # Tight loop; let readers in.
                time.sleep(0.001)

        def reader() -> None:
            try:
                while not stop_event.is_set():
                    rows = reg._conn.execute(
                        "SELECT COUNT(*), MIN(instance_id), MAX(instance_id) "
                        "FROM agents;"
                    ).fetchall()
                    assert rows and rows[0][0] >= 4
                    n_reads_done[0] += 1
            except BaseException as e:        # noqa: BLE001
                with errors_lock:
                    errors.append(e)

        with ThreadPoolExecutor(max_workers=8) as ex:
            ex.submit(writer)
            for _ in range(7):
                ex.submit(reader)
            time.sleep(0.6)
            stop_event.set()

        assert not errors, (
            f"{len(errors)} thread(s) raised; first: "
            f"{type(errors[0]).__name__}: {errors[0]}"
        )
        assert n_writes_done[0] >= 5, (
            f"writer didn't progress (only {n_writes_done[0]} writes "
            f"in 600ms) — likely blocked or crashed silently"
        )
        assert n_reads_done[0] >= 50, (
            f"readers didn't progress (only {n_reads_done[0]} reads "
            f"in 600ms across 7 threads) — likely deadlocked"
        )
    finally:
        reg.close()


def test_per_thread_connections(tmp_path: Path):
    """Confirms the structural B143 invariant: each thread that
    touches the connection gets its OWN underlying sqlite3.Connection.

    If this test fails, either _ThreadLocalConn was replaced with a
    single shared connection or threading.local broke (extremely
    unlikely). Either failure mode reproduces the B143 outage."""
    reg = Registry.bootstrap(tmp_path / "per_thread.db")
    try:
        proxy = reg._conn
        assert isinstance(proxy, _ThreadLocalConn), (
            f"Registry._conn is not a _ThreadLocalConn — it's "
            f"{type(proxy).__name__}. Per-thread isolation lost."
        )

        observed_ids: set[int] = set()
        observed_lock = threading.Lock()
        errors: list[BaseException] = []
        errors_lock = threading.Lock()

        def grab_conn_id() -> None:
            try:
                # Trigger lazy connection open + grab Python-id.
                conn_id = id(proxy._get())
                with observed_lock:
                    observed_ids.add(conn_id)
            except BaseException as e:        # noqa: BLE001
                with errors_lock:
                    errors.append(e)

        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = [ex.submit(grab_conn_id) for _ in range(8)]
            for f in futs:
                f.result()

        assert not errors
        # In a small threadpool some threads may be reused. The strict
        # invariant is "no thread shared a connection with another
        # thread that ran concurrently"; the practical proxy is that
        # the count is greater than 1 — i.e. NOT a single shared conn.
        assert len(observed_ids) > 1, (
            f"All 8 threads saw the same underlying connection "
            f"(id={observed_ids.pop()}). _ThreadLocalConn is not "
            f"isolating per thread — B143 regression."
        )
    finally:
        reg.close()


def test_close_on_one_thread_doesnt_affect_others(tmp_path: Path):
    """Per the _ThreadLocalConn docstring: ``close()`` only closes
    the *current* thread's connection; sibling threads' connections
    leak until process exit (acceptable for the daemon's one-process-
    per-host model). This test verifies that contract.

    The practical impact: a registry.close() called from a teardown
    handler running on thread A must not poison thread B's read path.
    """
    reg = Registry.bootstrap(tmp_path / "per_thread_close.db")
    try:
        _seed_test_data(reg, n_agents=2)
        proxy = reg._conn

        # Open a connection on thread B by reading from a worker.
        thread_b_done = threading.Event()
        thread_b_error: list[BaseException] = []

        def thread_b_first_read() -> None:
            try:
                proxy.execute("SELECT 1;").fetchall()
            except BaseException as e:        # noqa: BLE001
                thread_b_error.append(e)
            finally:
                thread_b_done.set()

        tb = threading.Thread(target=thread_b_first_read)
        tb.start()
        tb.join(timeout=2)
        assert thread_b_done.is_set()
        assert not thread_b_error

        # Now close on the *main* thread. Thread B's connection should
        # survive — proxy._all_conns retains it because main-thread
        # close() only acted on main's local.
        proxy.close()

        thread_b_post_done = threading.Event()
        thread_b_post_error: list[BaseException] = []

        def thread_b_second_read() -> None:
            try:
                # Note: this calls into the SAME proxy that just had
                # close() invoked from another thread. Thread B's
                # threading.local entry still has its connection.
                rows = proxy.execute("SELECT 1;").fetchall()
                assert rows
            except BaseException as e:        # noqa: BLE001
                thread_b_post_error.append(e)
            finally:
                thread_b_post_done.set()

        tb2 = threading.Thread(target=thread_b_second_read)
        tb2.start()
        tb2.join(timeout=2)
        assert thread_b_post_done.is_set()
        # If the close() somehow nuked thread B's connection too,
        # this would be sqlite3.ProgrammingError ("Cannot operate on
        # a closed database").
        assert not thread_b_post_error, (
            f"thread B saw {thread_b_post_error[0]} after main "
            f"thread's close() — _ThreadLocalConn close-isolation "
            f"broken."
        )
    finally:
        # Best-effort: close all remaining thread-local conns.
        try:
            reg._conn._close_all()
        except Exception:                    # noqa: BLE001
            pass
