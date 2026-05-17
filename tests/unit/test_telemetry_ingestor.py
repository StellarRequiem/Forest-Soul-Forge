"""ADR-0064 T2 (B349) — AdapterIngestor tests.

Coverage:
  Construction:
    - bad batch_size raises
    - bad flush_interval_s raises
  inject_lines + flush:
    - lines flowing through the adapter parse into events that hit
      the store as ONE batch
    - empty lines / None-returning lines are dropped, not batched
    - parse exceptions caught + counted, NOT raised
  Auto-flush:
    - reaching batch_size triggers ingest mid-stream
    - flush returns batch_id on success, None on empty
  Retention override:
    - adapter's retention_override is honored
    - changing the retention_class rebuilds the event with a fresh
      integrity_hash (so the chain anchors to the corrected event)
  Stats:
    - counts increment correctly
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from forest_soul_forge.security.telemetry.adapter import Adapter
from forest_soul_forge.security.telemetry.events import (
    TelemetryEvent,
    compute_integrity_hash,
)
from forest_soul_forge.security.telemetry.ingestor import (
    AdapterIngestor,
    IngestorError,
)
from forest_soul_forge.security.telemetry.store import (
    SqliteTelemetryStore,
)


# ---------------------------------------------------------------------------
# Fixture adapters
# ---------------------------------------------------------------------------


class _OneEventPerLineAdapter(Adapter):
    """Each non-empty line → one log_line/info event."""
    SOURCE = "test_one_per_line"

    def command(self) -> list[str]:
        return ["true"]

    def parse(self, line: str) -> TelemetryEvent | None:
        if not line.strip():
            return None
        return self.make_event(
            timestamp="2026-05-17T12:00:00+00:00",
            event_type="log_line",
            severity="info",
            payload={"raw": line},
        )


class _CrashingAdapter(Adapter):
    """Adapter whose parse() raises. The ingestor must catch + count
    + keep going. Adapter contract says parse MUST NOT raise; this
    adapter intentionally violates the contract to test ingestor
    resilience."""
    SOURCE = "test_crashing"

    def command(self) -> list[str]:
        return ["true"]

    def parse(self, line: str) -> TelemetryEvent | None:
        raise RuntimeError("boom")


class _AuthOverrideAdapter(Adapter):
    """Emits log_line events but overrides retention to
    security_relevant. Exercises the retention_override path."""
    SOURCE = "test_auth_override"

    def command(self) -> list[str]:
        return ["true"]

    def parse(self, line: str) -> TelemetryEvent | None:
        if not line.strip():
            return None
        return self.make_event(
            timestamp="2026-05-17T12:00:00+00:00",
            event_type="log_line",
            severity="info",
            payload={"raw": line},
        )

    def retention_override(self, event: TelemetryEvent) -> str | None:
        return "security_relevant"


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_bad_batch_size_raises(tmp_path):
    store = SqliteTelemetryStore(tmp_path / "t.sqlite")
    try:
        with pytest.raises(IngestorError, match="batch_size"):
            AdapterIngestor(_OneEventPerLineAdapter(), store, batch_size=0)
    finally:
        store.close()


def test_bad_flush_interval_raises(tmp_path):
    store = SqliteTelemetryStore(tmp_path / "t.sqlite")
    try:
        with pytest.raises(IngestorError, match="flush_interval_s"):
            AdapterIngestor(
                _OneEventPerLineAdapter(), store, flush_interval_s=0,
            )
    finally:
        store.close()


# ---------------------------------------------------------------------------
# inject_lines + flush
# ---------------------------------------------------------------------------


def test_inject_lines_then_flush_one_batch(tmp_path):
    store = SqliteTelemetryStore(tmp_path / "t.sqlite")
    try:
        ing = AdapterIngestor(
            _OneEventPerLineAdapter(), store,
            batch_size=100,  # high enough that we won't auto-flush
        )
        ing.inject_lines(["a", "b", "c"])
        batch_id = ing.flush_pending()
        assert batch_id is not None
        # All three events landed under the same batch_id.
        rows = store.query_by_batch(batch_id)
        assert len(rows) == 3
        # Stats reflect the 3 parsed events + 1 batch.
        assert ing.stats.lines_seen == 3
        assert ing.stats.lines_parsed_to_event == 3
        assert ing.stats.lines_dropped == 0
        assert ing.stats.events_ingested == 3
        assert ing.stats.batches_flushed == 1
    finally:
        store.close()


def test_empty_lines_dropped(tmp_path):
    store = SqliteTelemetryStore(tmp_path / "t.sqlite")
    try:
        ing = AdapterIngestor(_OneEventPerLineAdapter(), store, batch_size=100)
        ing.inject_lines(["", "   ", "real", ""])
        ing.flush_pending()
        rows = store.query(limit=10)
        assert len(rows) == 1
        assert rows[0].payload["raw"] == "real"
        assert ing.stats.lines_seen == 4
        assert ing.stats.lines_dropped == 3
        assert ing.stats.lines_parsed_to_event == 1
    finally:
        store.close()


def test_flush_empty_returns_none(tmp_path):
    store = SqliteTelemetryStore(tmp_path / "t.sqlite")
    try:
        ing = AdapterIngestor(_OneEventPerLineAdapter(), store)
        assert ing.flush_pending() is None
        assert ing.stats.batches_flushed == 0
    finally:
        store.close()


def test_parse_exception_is_caught_and_counted(tmp_path):
    """An adapter whose parse() raises must not take the ingestor
    down. The contract says parse MUST NOT raise; this tests that we
    enforce the contract defensively rather than trusting adapters."""
    store = SqliteTelemetryStore(tmp_path / "t.sqlite")
    try:
        ing = AdapterIngestor(_CrashingAdapter(), store)
        ing.inject_lines(["a", "b", "c"])
        assert ing.stats.lines_seen == 3
        assert ing.stats.lines_dropped == 3
        assert ing.stats.lines_parsed_to_event == 0
        assert ing.stats.last_error is not None
        assert "boom" in ing.stats.last_error
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Auto-flush
# ---------------------------------------------------------------------------


def test_auto_flush_when_batch_size_reached(tmp_path):
    """When pending hits batch_size, the ingestor auto-flushes
    mid-stream. Pending should drop to 0 + the store should hold
    the events."""
    store = SqliteTelemetryStore(tmp_path / "t.sqlite")
    try:
        ing = AdapterIngestor(
            _OneEventPerLineAdapter(), store, batch_size=3,
        )
        # Inject 7 → expect two auto-flushes (3 + 3) + 1 leftover.
        ing.inject_lines(["a", "b", "c", "d", "e", "f", "g"])
        assert ing.stats.batches_flushed == 2
        assert ing.stats.events_ingested == 6
        # Flush the leftover.
        ing.flush_pending()
        assert ing.stats.batches_flushed == 3
        assert ing.stats.events_ingested == 7
        # All 7 events live in the store.
        rows = store.query(limit=20)
        assert len(rows) == 7
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Retention override
# ---------------------------------------------------------------------------


def test_retention_override_rewrites_event(tmp_path):
    """When the adapter's retention_override returns a different
    class than the parsed event carries, the ingestor MUST rebuild
    the event with the new class + recompute the integrity_hash.
    The hash is part of the chain anchor; using the old hash would
    leave the chain pointing at an event the store doesn't have."""
    store = SqliteTelemetryStore(tmp_path / "t.sqlite")
    try:
        ing = AdapterIngestor(_AuthOverrideAdapter(), store, batch_size=10)
        ing.inject_lines(["auth1", "auth2"])
        ing.flush_pending()
        rows = store.query(limit=10)
        assert len(rows) == 2
        # Override applied — both events ended up security_relevant.
        for r in rows:
            assert r.retention_class == "security_relevant"
            # Hash must match canonical_form with the NEW class.
            expected = compute_integrity_hash(
                timestamp=r.timestamp,
                source=r.source,
                event_type=r.event_type,
                severity=r.severity,
                payload=r.payload,
                correlation_id=r.correlation_id,
                retention_class="security_relevant",
            )
            assert r.integrity_hash == expected
    finally:
        store.close()


def test_classifier_used_when_no_override(tmp_path):
    """Adapter that DOESN'T override retention — events go through
    classify_retention. log_line + info → ephemeral per Rule 4."""
    store = SqliteTelemetryStore(tmp_path / "t.sqlite")
    try:
        ing = AdapterIngestor(
            _OneEventPerLineAdapter(), store, batch_size=10,
        )
        ing.inject_lines(["info-level log line"])
        ing.flush_pending()
        rows = store.query(limit=10)
        assert len(rows) == 1
        # _OneEventPerLineAdapter emits log_line+info → ephemeral.
        assert rows[0].retention_class == "ephemeral"
    finally:
        store.close()
