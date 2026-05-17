"""ADR-0064 T2 — AdapterIngestor: drives one adapter into a store.

Owns the subprocess lifecycle for one adapter, drains its stdout
on a worker thread, parses each line via the adapter's parse(),
and batch-ingests into the TelemetryStore.

Why batch-ingest: the store's ingest_batch() is the audit chain
anchor unit (ADR-0064 D5). Single-event ingest would 100× the
chain entries. The ingestor accumulates parsed events and flushes
either when the batch size hits a threshold (default 100) or when
a flush interval elapses (default 5s). Both bounds together cover
both ends: bursty sources (flush by size) and quiet sources (flush
by time so events don't sit in memory forever).

Test-friendliness: the ingestor exposes inject_lines() which feeds
lines into the parse pipeline WITHOUT spawning a subprocess. Tests
use this to exercise the parser + batching + store wiring without
the timing complexity of real subprocess I/O.

What ships in T3 (NOT in this file):
  - Audit chain emission for telemetry_batch_ingested events
  - ADR-0051 sandbox wrapping of the subprocess
For T2, subprocess.Popen runs the adapter command directly. The
sandbox hookup is a wrapper around the same lifecycle, so adding
it later doesn't require restructuring this code.
"""
from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .adapter import Adapter
from .events import TelemetryEvent
from .retention import classify_retention
from .store import TelemetryStore, TelemetryStoreError


DEFAULT_BATCH_SIZE = 100
DEFAULT_FLUSH_INTERVAL_S = 5.0


class IngestorError(Exception):
    """Raised when the ingestor's lifecycle is violated:
    start-after-start, stop-without-start, etc."""


@dataclass
class IngestorStats:
    """Snapshot of one ingestor's lifetime counts. Used by the
    operator dashboard + retention sweep audit logging."""

    lines_seen: int = 0
    lines_parsed_to_event: int = 0
    lines_dropped: int = 0       # parse returned None
    events_ingested: int = 0
    batches_flushed: int = 0
    last_flush_at: float | None = None
    last_error: str | None = None


class AdapterIngestor:
    """Drives one adapter into one store.

    Lifecycle:
        i = AdapterIngestor(adapter, store)
        i.start()          # spawns subprocess + worker thread
        ...
        i.stop()           # terminates subprocess + flushes pending

    Or in-process (for tests):
        i = AdapterIngestor(adapter, store)
        i.inject_lines(["line 1", "line 2", ...])
        i.flush_pending()  # force batch into the store
    """

    def __init__(
        self,
        adapter: Adapter,
        store: TelemetryStore,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        flush_interval_s: float = DEFAULT_FLUSH_INTERVAL_S,
        # Override for tests that want to avoid real subprocess time.
        spawn: Callable[[list[str]], subprocess.Popen] | None = None,
    ) -> None:
        if batch_size < 1:
            raise IngestorError(f"batch_size must be >= 1; got {batch_size}")
        if flush_interval_s <= 0:
            raise IngestorError(
                f"flush_interval_s must be > 0; got {flush_interval_s}"
            )
        self.adapter = adapter
        self.store = store
        self.batch_size = batch_size
        self.flush_interval_s = flush_interval_s
        self.stats = IngestorStats()

        self._spawn = spawn or self._default_spawn
        self._proc: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._pending: list[TelemetryEvent] = []
        self._pending_lock = threading.Lock()
        self._last_flush_clock = time.monotonic()
        self._started = False

    # ---- lifecycle -------------------------------------------------------

    def start(self) -> None:
        """Spawn the adapter subprocess + drain it on a worker thread."""
        if self._started:
            raise IngestorError("already started")
        cmd = self.adapter.command()
        if not cmd or not isinstance(cmd, list):
            raise IngestorError(
                f"adapter {self.adapter.SOURCE} returned bad command: {cmd!r}"
            )
        self._proc = self._spawn(cmd)
        self._reader_thread = threading.Thread(
            target=self._read_loop,
            daemon=True,
            name=f"adapter-ingestor-{self.adapter.SOURCE}",
        )
        self._reader_thread.start()
        self._started = True

    def stop(self, *, timeout_s: float = 5.0) -> None:
        """Terminate subprocess + drain pending batch + join worker."""
        if not self._started:
            raise IngestorError("not started")
        self._stop_event.set()
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=timeout_s)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait(timeout=timeout_s)
            finally:
                pass
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=timeout_s)
        self.flush_pending()
        self._started = False

    # ---- test injection --------------------------------------------------

    def inject_lines(self, lines: list[str]) -> None:
        """Test-only: feed lines into the parse pipeline directly.

        Bypasses the subprocess. Useful for unit tests that want to
        exercise the parser + batching + store wiring without
        timing nondeterminism. Does NOT auto-flush; call
        flush_pending() afterward (or let the size-based auto-flush
        fire when the batch hits batch_size)."""
        for line in lines:
            self._handle_line(line)

    # ---- internals -------------------------------------------------------

    @staticmethod
    def _default_spawn(cmd: list[str]) -> subprocess.Popen:
        # text=True + line-buffered stdout. The adapter contract says
        # each line is one event candidate, so we want line-buffering.
        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )

    def _read_loop(self) -> None:
        """Worker thread body: drain subprocess stdout line by line,
        handing each to _handle_line."""
        if self._proc is None or self._proc.stdout is None:
            return
        try:
            for raw in self._proc.stdout:
                if self._stop_event.is_set():
                    break
                line = raw.rstrip("\n")
                self._handle_line(line)
                # Time-based flush check between lines so quiet
                # sources don't pin the batch in memory forever.
                if (
                    time.monotonic() - self._last_flush_clock
                    >= self.flush_interval_s
                ):
                    self.flush_pending()
        except Exception as e:  # subprocess died mid-read, encoding error, etc.
            self.stats.last_error = f"_read_loop: {type(e).__name__}: {e}"

    def _handle_line(self, line: str) -> None:
        self.stats.lines_seen += 1
        try:
            event = self.adapter.parse(line)
        except Exception as e:
            # Adapter contract says parse MUST NOT raise; if one
            # does, we treat it as a dropped line + log the error.
            # We do NOT let it kill the ingestor.
            self.stats.lines_dropped += 1
            self.stats.last_error = (
                f"parse raised on line {line[:80]!r}: "
                f"{type(e).__name__}: {e}"
            )
            return
        if event is None:
            self.stats.lines_dropped += 1
            return

        # Apply retention override or fall through to classifier.
        override = self.adapter.retention_override(event)
        final_retention = override or classify_retention(
            event_type=event.event_type,
            severity=event.severity,
            payload=event.payload,
        )
        if final_retention != event.retention_class:
            # Rebuild the event with the new retention_class +
            # recompute integrity_hash. The hash MUST include
            # retention_class because the chain anchors to it
            # (see ADR-0064 D4 + the test_canonical_form_changes_
            # when_retention_class_changes pin).
            event = self.adapter.make_event(
                timestamp=event.timestamp,
                event_type=event.event_type,
                severity=event.severity,
                payload=event.payload,
                correlation_id=event.correlation_id,
                retention_class=final_retention,
                ingested_at=event.ingested_at,
            )

        self.stats.lines_parsed_to_event += 1
        with self._pending_lock:
            self._pending.append(event)
            should_flush = len(self._pending) >= self.batch_size
        if should_flush:
            self.flush_pending()

    def flush_pending(self) -> str | None:
        """Drain the pending buffer into the store as ONE batch.

        Returns the batch_id if anything was flushed, else None
        (empty pending). The batch_id is the audit-chain anchor
        T3 will record per ADR-0064 D5."""
        with self._pending_lock:
            batch = list(self._pending)
            self._pending.clear()
        if not batch:
            return None
        try:
            batch_id = self.store.ingest_batch(batch)
        except TelemetryStoreError as e:
            self.stats.last_error = f"ingest_batch failed: {e}"
            return None
        self.stats.events_ingested += len(batch)
        self.stats.batches_flushed += 1
        self.stats.last_flush_at = time.time()
        self._last_flush_clock = time.monotonic()
        return batch_id
