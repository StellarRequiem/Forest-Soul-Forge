"""ADR-0064 T1 — TelemetryStore interface + SQLite reference impl.

The store is the canonical home for telemetry events. Separate file
from the registry (`data/registry.sqlite`) per ADR-0064 Decision 3:
different lifecycle, different lock discipline, different encryption
salt.

Interface (TelemetryStore Protocol):
  ingest(event)              → event_id
  ingest_batch(events)       → batch_id (audit chain anchor)
  query(filters)             → list[TelemetryEvent]
  query_by_correlation(id)   → list[TelemetryEvent]
  retention_sweep(policy, now) → dict[class, count_deleted]
  count_by_retention_class() → dict[class, count]  (for ops dashboards)
  close()                    → release connection

Reference implementation: SqliteTelemetryStore. ADR-0050 encryption-
at-rest wrapping is the operator's responsibility (the daemon
constructs the store with the decrypted path); we don't reach into
the encryption layer here because that would couple substrate to
key-management.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterable, Protocol

from .events import TelemetryEvent, compute_integrity_hash
from .retention import RetentionPolicy


class TelemetryStoreError(Exception):
    """Storage-layer failures: bad schema, FK violation, lock contention.

    Distinct from TelemetryEventError (event-shape issues) so callers
    can distinguish 'the event is malformed' from 'the store is
    broken'."""


# ADR-0064 Decision 3 schema. v1.
# Composite index on (retention_class, timestamp) supports the
# retention sweep's DELETE WHERE without a full table scan.
SQLITE_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS telemetry_events (
    event_id        TEXT PRIMARY KEY,
    timestamp       TEXT NOT NULL,
    source          TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    severity        TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    correlation_id  TEXT,
    integrity_hash  TEXT NOT NULL,
    ingested_at     TEXT NOT NULL,
    retention_class TEXT NOT NULL DEFAULT 'standard',
    batch_id        TEXT
);

CREATE INDEX IF NOT EXISTS idx_telemetry_timestamp
    ON telemetry_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_telemetry_correlation
    ON telemetry_events(correlation_id);
CREATE INDEX IF NOT EXISTS idx_telemetry_severity
    ON telemetry_events(severity);
CREATE INDEX IF NOT EXISTS idx_telemetry_event_type
    ON telemetry_events(event_type);
CREATE INDEX IF NOT EXISTS idx_telemetry_retention
    ON telemetry_events(retention_class, timestamp);
CREATE INDEX IF NOT EXISTS idx_telemetry_batch
    ON telemetry_events(batch_id);
"""


class TelemetryStore(Protocol):
    """Storage contract. SqliteTelemetryStore is the reference impl;
    a future Phase B+ tranche may add an in-memory test store or a
    sharded-by-day variant."""

    def ingest(self, event: TelemetryEvent) -> str: ...

    def ingest_batch(self, events: Iterable[TelemetryEvent]) -> str: ...

    def query(
        self,
        *,
        event_type: str | None = None,
        source: str | None = None,
        severity: str | None = None,
        correlation_id: str | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> list[TelemetryEvent]: ...

    def query_by_correlation(self, correlation_id: str) -> list[TelemetryEvent]: ...

    def query_by_batch(self, batch_id: str) -> list[TelemetryEvent]: ...

    def retention_sweep(
        self,
        *,
        policy: RetentionPolicy,
        now: datetime,
    ) -> dict[str, int]: ...

    def count_by_retention_class(self) -> dict[str, int]: ...

    def close(self) -> None: ...


class SqliteTelemetryStore:
    """SQLite-backed telemetry store.

    Thread-safety:
      One sqlite3 connection per store instance. All writes go through
      a per-instance threading.Lock so multiple adapters can ingest
      without corrupting WAL. Reads also take the lock — sqlite3's
      default connection isn't thread-safe by default and
      check_same_thread=False without external locking is unsafe.

    The store does NOT take app.state.write_lock from the daemon —
    that lock guards the REGISTRY's single-writer invariant. The
    telemetry store is intentionally a SEPARATE writer (ADR-0064
    Decision 3) so a high-volume adapter doesn't backpressure
    governance writes.
    """

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        # Ensure parent exists; the SQLite client won't create it.
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None gets us autocommit semantics; we'll
        # use BEGIN/COMMIT explicitly for batches.
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        # WAL mode = concurrent readers + one writer with much better
        # ingest latency than rollback journal. Acceptable for
        # telemetry; we don't need cross-machine fsync semantics.
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        # Apply schema; CREATE IF NOT EXISTS makes this idempotent.
        self._conn.executescript(SQLITE_SCHEMA_V1)

    # ---- ingest -----------------------------------------------------------

    def ingest(self, event: TelemetryEvent) -> str:
        """Insert one event. Returns event_id.

        Re-verifies integrity_hash before insert — catches in-transit
        tampering between the adapter and the store. A mismatch raises
        TelemetryStoreError; the caller (adapter or daemon) decides
        whether to retry or alert.
        """
        expected = compute_integrity_hash(
            timestamp=event.timestamp,
            source=event.source,
            event_type=event.event_type,
            severity=event.severity,
            payload=event.payload,
            correlation_id=event.correlation_id,
            retention_class=event.retention_class,
        )
        if expected != event.integrity_hash:
            raise TelemetryStoreError(
                f"integrity_hash mismatch on ingest for event_id="
                f"{event.event_id!r}: expected {expected}, "
                f"got {event.integrity_hash}"
            )

        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO telemetry_events
                      (event_id, timestamp, source, event_type, severity,
                       payload_json, correlation_id, integrity_hash,
                       ingested_at, retention_class, batch_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        event.event_id,
                        event.timestamp,
                        event.source,
                        event.event_type,
                        event.severity,
                        json.dumps(event.payload, sort_keys=True,
                                   ensure_ascii=False),
                        event.correlation_id,
                        event.integrity_hash,
                        event.ingested_at,
                        event.retention_class,
                    ),
                )
            except sqlite3.IntegrityError as e:
                raise TelemetryStoreError(
                    f"insert failed (likely duplicate event_id "
                    f"{event.event_id!r}): {e}"
                ) from e
        return event.event_id

    def ingest_batch(self, events: Iterable[TelemetryEvent]) -> str:
        """Insert N events under a shared batch_id.

        Returns the batch_id (uuid4 hex). The caller (telemetry_steward
        or its substrate hook) is responsible for emitting the
        `telemetry_batch_ingested` audit chain entry referencing this
        batch_id + the integrity_root (Merkle-like sha256 of sorted
        event integrity_hashes).

        Batches are transactional: if any row fails, the whole batch
        rolls back. This is the right shape because the audit chain
        anchor commits to the WHOLE batch; a partial commit would
        leave the chain entry's count out of sync with reality.
        """
        events_list = list(events)
        if not events_list:
            raise TelemetryStoreError(
                "ingest_batch called with empty iterable; refuse to "
                "anchor a zero-event batch in the audit chain"
            )

        # Pre-verify all integrity hashes BEFORE taking the lock.
        # Failing fast on the first bad hash avoids holding the
        # write lock while we crunch sha256s.
        for ev in events_list:
            expected = compute_integrity_hash(
                timestamp=ev.timestamp,
                source=ev.source,
                event_type=ev.event_type,
                severity=ev.severity,
                payload=ev.payload,
                correlation_id=ev.correlation_id,
                retention_class=ev.retention_class,
            )
            if expected != ev.integrity_hash:
                raise TelemetryStoreError(
                    f"integrity_hash mismatch in batch for event_id="
                    f"{ev.event_id!r}: expected {expected}"
                )

        batch_id = uuid.uuid4().hex

        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.executemany(
                    """
                    INSERT INTO telemetry_events
                      (event_id, timestamp, source, event_type, severity,
                       payload_json, correlation_id, integrity_hash,
                       ingested_at, retention_class, batch_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            ev.event_id, ev.timestamp, ev.source,
                            ev.event_type, ev.severity,
                            json.dumps(ev.payload, sort_keys=True,
                                       ensure_ascii=False),
                            ev.correlation_id, ev.integrity_hash,
                            ev.ingested_at, ev.retention_class,
                            batch_id,
                        )
                        for ev in events_list
                    ],
                )
                self._conn.execute("COMMIT")
            except sqlite3.IntegrityError as e:
                self._conn.execute("ROLLBACK")
                raise TelemetryStoreError(
                    f"batch insert failed (rolled back): {e}"
                ) from e
        return batch_id

    # ---- query ------------------------------------------------------------

    def query(
        self,
        *,
        event_type: str | None = None,
        source: str | None = None,
        severity: str | None = None,
        correlation_id: str | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> list[TelemetryEvent]:
        """Filter events. All filter args are AND-combined.

        ``since`` is an ISO 8601 string; events with timestamp >= since
        are returned. ``limit`` is hard-capped at 10_000 to avoid
        accidental full-table dumps.
        """
        if limit > 10_000:
            raise TelemetryStoreError(
                f"limit {limit} exceeds 10_000 cap; use streaming if "
                f"larger result sets are needed"
            )

        where: list[str] = []
        params: list[object] = []
        if event_type is not None:
            where.append("event_type = ?")
            params.append(event_type)
        if source is not None:
            where.append("source = ?")
            params.append(source)
        if severity is not None:
            where.append("severity = ?")
            params.append(severity)
        if correlation_id is not None:
            where.append("correlation_id = ?")
            params.append(correlation_id)
        if since is not None:
            where.append("timestamp >= ?")
            params.append(since)

        sql = "SELECT * FROM telemetry_events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_event(r) for r in rows]

    def query_by_correlation(self, correlation_id: str) -> list[TelemetryEvent]:
        """Convenience for the common "walk an incident chain" query.

        Capped at 1000 because a single correlation_id with > 1000
        events probably means the correlator made a mistake (likely
        every event was tagged with the same id by accident)."""
        return self.query(correlation_id=correlation_id, limit=1000)

    def query_by_batch(self, batch_id: str) -> list[TelemetryEvent]:
        """Walk one batch — used by the chain-verifier path.

        Returns events sorted by event_id (deterministic order) so the
        verifier can recompute the integrity_root the same way the
        ingest path did."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM telemetry_events WHERE batch_id = ? "
                "ORDER BY event_id ASC",
                (batch_id,),
            ).fetchall()
        return [self._row_to_event(r) for r in rows]

    # ---- retention --------------------------------------------------------

    def retention_sweep(
        self,
        *,
        policy: RetentionPolicy,
        now: datetime,
    ) -> dict[str, int]:
        """Delete events past their retention TTL.

        Returns a {class_name: count_deleted} dict so the caller can
        emit ONE telemetry_retention_sweep audit chain event with the
        totals (ADR-0064 Decision 4 — no per-row audit).

        Idempotent: re-running with the same `now` returns zero counts
        because the eligible rows are already gone.
        """
        counts: dict[str, int] = {}
        with self._lock:
            for cls, ttl_days in policy.ttls.items():
                cutoff = policy.cutoff_for(cls, now=now).isoformat()
                cursor = self._conn.execute(
                    "DELETE FROM telemetry_events "
                    "WHERE retention_class = ? AND timestamp < ?",
                    (cls, cutoff),
                )
                counts[cls] = cursor.rowcount
        return counts

    def count_by_retention_class(self) -> dict[str, int]:
        """Operator-dashboard helper. Returns the live count per class
        without touching retention TTLs. Used by `fsf telemetry status`
        (ships in T3)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT retention_class, COUNT(*) AS n "
                "FROM telemetry_events GROUP BY retention_class"
            ).fetchall()
        return {r["retention_class"]: r["n"] for r in rows}

    # ---- lifecycle --------------------------------------------------------

    def close(self) -> None:
        """Release the connection. Idempotent."""
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.ProgrammingError:
                # Already closed; nothing to do.
                pass

    # ---- internals --------------------------------------------------------

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> TelemetryEvent:
        return TelemetryEvent(
            event_id=row["event_id"],
            timestamp=row["timestamp"],
            source=row["source"],
            event_type=row["event_type"],
            severity=row["severity"],
            payload=json.loads(row["payload_json"]),
            correlation_id=row["correlation_id"],
            integrity_hash=row["integrity_hash"],
            ingested_at=row["ingested_at"],
            retention_class=row["retention_class"],
        )
