"""ADR-0064 T1 (B348) — SqliteTelemetryStore tests.

Coverage:
  Lifecycle:
    - store creates db file + applies schema cleanly
    - close() is idempotent
  Single ingest:
    - happy path returns event_id
    - integrity_hash mismatch raises TelemetryStoreError
    - duplicate event_id raises
  Batch ingest:
    - empty batch refuses (we don't anchor zero-event batches)
    - happy path returns batch_id, all rows committed with same batch_id
    - any bad hash in the batch causes the WHOLE batch to roll back
  Query:
    - filter by event_type / source / severity / correlation_id / since
    - limit caps result set
    - limit > 10000 raises
    - results ordered by timestamp DESC
  query_by_correlation:
    - returns all events with that id
  query_by_batch:
    - returns events sorted by event_id ASC (deterministic for hash)
  count_by_retention_class:
    - returns {class: count} for live rows
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from forest_soul_forge.security.telemetry.events import (
    TelemetryEvent,
    compute_integrity_hash,
)
from forest_soul_forge.security.telemetry.retention import RetentionPolicy
from forest_soul_forge.security.telemetry.store import (
    SqliteTelemetryStore,
    TelemetryStoreError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    s = SqliteTelemetryStore(tmp_path / "telemetry.sqlite")
    try:
        yield s
    finally:
        s.close()


def _make_event(
    *,
    event_id: str = "evt-001",
    timestamp: str = "2026-05-17T12:00:00+00:00",
    source: str = "process_monitor",
    event_type: str = "process_spawn",
    severity: str = "info",
    payload: dict | None = None,
    correlation_id: str | None = None,
    ingested_at: str = "2026-05-17T12:00:01+00:00",
    retention_class: str = "standard",
    integrity_hash: str | None = None,
) -> TelemetryEvent:
    """Build a TelemetryEvent with auto-computed hash unless overridden."""
    if payload is None:
        payload = {"pid": 1234, "cmd": "/bin/zsh"}
    if integrity_hash is None:
        integrity_hash = compute_integrity_hash(
            timestamp=timestamp, source=source, event_type=event_type,
            severity=severity, payload=payload,
            correlation_id=correlation_id, retention_class=retention_class,
        )
    return TelemetryEvent(
        event_id=event_id, timestamp=timestamp, source=source,
        event_type=event_type, severity=severity, payload=payload,
        correlation_id=correlation_id, integrity_hash=integrity_hash,
        ingested_at=ingested_at, retention_class=retention_class,
    )


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_store_creates_db_file_and_schema(tmp_path):
    """Construction is enough to materialize the DB + apply the schema.
    No explicit init step needed."""
    db_path = tmp_path / "subdir" / "telemetry.sqlite"
    s = SqliteTelemetryStore(db_path)
    try:
        assert db_path.exists()
        # Confirm the table exists by querying sqlite_master.
        with sqlite3.connect(db_path) as raw:
            rows = raw.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='telemetry_events'"
            ).fetchall()
            assert len(rows) == 1
    finally:
        s.close()


def test_close_is_idempotent(store):
    store.close()
    store.close()  # no exception


# ---------------------------------------------------------------------------
# Single ingest
# ---------------------------------------------------------------------------


def test_ingest_happy_path(store):
    ev = _make_event(event_id="evt-001")
    out = store.ingest(ev)
    assert out == "evt-001"
    # Verify via query.
    rows = store.query(event_type="process_spawn", limit=10)
    assert len(rows) == 1
    assert rows[0].event_id == "evt-001"


def test_ingest_integrity_hash_mismatch_raises(store):
    ev = _make_event(
        event_id="evt-002",
        integrity_hash="0" * 64,  # 64 chars but wrong content
    )
    with pytest.raises(TelemetryStoreError, match="integrity_hash mismatch"):
        store.ingest(ev)


def test_ingest_duplicate_event_id_raises(store):
    ev = _make_event(event_id="evt-dup")
    store.ingest(ev)
    with pytest.raises(TelemetryStoreError, match="duplicate event_id|insert failed"):
        store.ingest(_make_event(event_id="evt-dup", payload={"pid": 999}))


# ---------------------------------------------------------------------------
# Batch ingest
# ---------------------------------------------------------------------------


def test_ingest_batch_empty_refuses(store):
    with pytest.raises(TelemetryStoreError, match="empty iterable"):
        store.ingest_batch([])


def test_ingest_batch_happy_path(store):
    events = [_make_event(event_id=f"batch-{i}") for i in range(5)]
    batch_id = store.ingest_batch(events)
    assert isinstance(batch_id, str)
    assert len(batch_id) == 32  # uuid4 hex
    # All rows landed under the same batch_id.
    rows = store.query_by_batch(batch_id)
    assert len(rows) == 5
    assert {r.event_id for r in rows} == {f"batch-{i}" for i in range(5)}


def test_ingest_batch_rolls_back_on_any_bad_hash(store):
    """Critical: a partial batch commit would leave the audit chain
    anchor's count out of sync with reality. The ingest path must
    abort the WHOLE batch if any event fails its hash check."""
    good = [_make_event(event_id=f"good-{i}") for i in range(3)]
    bad = _make_event(event_id="bad-1", integrity_hash="0" * 64)
    with pytest.raises(TelemetryStoreError, match="integrity_hash mismatch"):
        store.ingest_batch([*good, bad])
    # No rows should have landed.
    assert store.query(limit=100) == []


def test_query_by_batch_sorted_by_event_id(store):
    """The verifier path recomputes the integrity_root by concatenating
    hashes in event_id order. Both ingest AND verify must sort the
    same way; pinning the sort here prevents drift."""
    events = [_make_event(event_id=f"z-{i}") for i in [3, 1, 2, 5, 4]]
    bid = store.ingest_batch(events)
    rows = store.query_by_batch(bid)
    assert [r.event_id for r in rows] == ["z-1", "z-2", "z-3", "z-4", "z-5"]


# ---------------------------------------------------------------------------
# Query filters
# ---------------------------------------------------------------------------


def _seed_diverse(store):
    """Insert 4 events spanning multiple types/sources/severities/times
    so the query filter tests have something to discriminate on."""
    events = [
        _make_event(
            event_id="e1", timestamp="2026-05-17T10:00:00+00:00",
            source="process_monitor", event_type="process_spawn",
            severity="info",
        ),
        _make_event(
            event_id="e2", timestamp="2026-05-17T11:00:00+00:00",
            source="auth_subsystem", event_type="auth_event",
            severity="warn", correlation_id="incident-1",
        ),
        _make_event(
            event_id="e3", timestamp="2026-05-17T12:00:00+00:00",
            source="auth_subsystem", event_type="auth_event",
            severity="critical", correlation_id="incident-1",
        ),
        _make_event(
            event_id="e4", timestamp="2026-05-17T13:00:00+00:00",
            source="fsevents", event_type="file_change",
            severity="info",
        ),
    ]
    for ev in events:
        store.ingest(ev)


def test_query_filter_event_type(store):
    _seed_diverse(store)
    rows = store.query(event_type="auth_event")
    assert {r.event_id for r in rows} == {"e2", "e3"}


def test_query_filter_source(store):
    _seed_diverse(store)
    rows = store.query(source="auth_subsystem")
    assert {r.event_id for r in rows} == {"e2", "e3"}


def test_query_filter_severity(store):
    _seed_diverse(store)
    rows = store.query(severity="critical")
    assert {r.event_id for r in rows} == {"e3"}


def test_query_filter_correlation_id(store):
    _seed_diverse(store)
    rows = store.query(correlation_id="incident-1")
    assert {r.event_id for r in rows} == {"e2", "e3"}


def test_query_filter_since(store):
    _seed_diverse(store)
    rows = store.query(since="2026-05-17T12:00:00+00:00")
    assert {r.event_id for r in rows} == {"e3", "e4"}


def test_query_combines_filters_with_and(store):
    _seed_diverse(store)
    rows = store.query(
        source="auth_subsystem",
        severity="warn",
    )
    assert {r.event_id for r in rows} == {"e2"}


def test_query_orders_by_timestamp_desc(store):
    _seed_diverse(store)
    rows = store.query(limit=10)
    timestamps = [r.timestamp for r in rows]
    assert timestamps == sorted(timestamps, reverse=True)


def test_query_limit_caps_at_10000(store):
    with pytest.raises(TelemetryStoreError, match="exceeds 10_000"):
        store.query(limit=10_001)


def test_query_by_correlation_returns_all_for_id(store):
    _seed_diverse(store)
    rows = store.query_by_correlation("incident-1")
    assert {r.event_id for r in rows} == {"e2", "e3"}


# ---------------------------------------------------------------------------
# count_by_retention_class
# ---------------------------------------------------------------------------


def test_count_by_retention_class(store):
    """Operator-dashboard helper. Returns live counts per class."""
    store.ingest(_make_event(
        event_id="r1", retention_class="standard",
    ))
    store.ingest(_make_event(
        event_id="r2", retention_class="security_relevant",
    ))
    store.ingest(_make_event(
        event_id="r3", retention_class="security_relevant",
    ))
    counts = store.count_by_retention_class()
    assert counts == {"standard": 1, "security_relevant": 2}
