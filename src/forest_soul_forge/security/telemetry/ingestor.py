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

B377 (T3) — audit chain emission landed here:
  When flush_pending() successfully calls store.ingest_batch(), it
  now ALSO emits a `telemetry_batch_ingested` audit chain entry
  recording batch_id + source + event_count + integrity_root +
  first_timestamp + last_timestamp. The integrity_root is the
  sha256 of the sorted concatenation of each event's
  integrity_hash — a single anchor digest the verify CLI can
  recompute from the store and compare against the chain entry.

What still ships later:
  - ADR-0051 sandbox wrapping of the subprocess (T6's micro-
    batching layer is the natural place to add it).
"""
from __future__ import annotations

import hashlib
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from .adapter import Adapter
from .events import TelemetryEvent
from .retention import classify_retention
from .store import TelemetryStore, TelemetryStoreError

if TYPE_CHECKING:
    # Import-cycle avoidance: the ingestor is constructed inside
    # the daemon's lifespan, which also constructs the audit chain.
    # Importing AuditChain at type-check time only keeps the cycle
    # broken at runtime.
    from forest_soul_forge.core.audit_chain import AuditChain


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
        # B377 (T3) — audit chain handle. When set, every successful
        # flush_pending emits a `telemetry_batch_ingested` event so
        # the chain records the batch_id + integrity_root anchor.
        # Optional with default None so legacy callers (T2 tests
        # constructing AdapterIngestor without the daemon's
        # AuditChain) keep working.
        audit_chain: "AuditChain | None" = None,
        # The chain's `agent_dna` field is the actor of the event.
        # Telemetry batch ingestion is system-attributed by default
        # (the ingestor isn't itself an agent), so None is the
        # canonical value. When telemetry_steward (T4) lands and
        # drives ingestors on behalf of an agent, that agent's
        # dna gets passed here instead.
        chain_agent_dna: str | None = None,
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
        self.audit_chain = audit_chain
        self.chain_agent_dna = chain_agent_dna
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
        per ADR-0064 D5.

        B377 (T3): after a successful ingest_batch, also emit the
        `telemetry_batch_ingested` chain entry — batch_id + source
        + event_count + integrity_root + first/last_timestamp. If
        the chain emission fails (chain handle wasn't passed, or
        append raised), the store insert is still durable; we just
        log to `stats.last_error` so the operator can spot it.
        Store + chain emission are NOT atomic across each other —
        the store transaction commits before the chain entry
        lands. Mid-failure surface: store has the batch, chain
        doesn't reference it, verify CLI flags `chain_entry_missing`
        on read. Acceptable for telemetry (the canonical pattern
        is store-first then-anchor; the inverse would risk anchor-
        without-data which is worse)."""
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

        # B377 (T3) — anchor the batch in the audit chain.
        if self.audit_chain is not None:
            try:
                integrity_root = _compute_integrity_root(batch)
                timestamps = sorted(ev.timestamp for ev in batch)
                self.audit_chain.append(
                    "telemetry_batch_ingested",
                    {
                        "batch_id":         batch_id,
                        "source":           self.adapter.SOURCE,
                        "event_count":      len(batch),
                        "integrity_root":   integrity_root,
                        "first_timestamp":  timestamps[0],
                        "last_timestamp":   timestamps[-1],
                    },
                    agent_dna=self.chain_agent_dna,
                )
            except Exception as e:
                # The store insert is durable; we don't roll it
                # back if chain emission fails. Record so the
                # operator can investigate.
                self.stats.last_error = (
                    f"telemetry_batch_ingested chain append failed "
                    f"for batch_id={batch_id}: {e!r}"
                )

        return batch_id


def _compute_integrity_root(events: list[TelemetryEvent]) -> str:
    """Merkle-like root over the sorted concatenation of each
    event's integrity_hash.

    Sorted-before-concat keeps the root invariant under permutation
    of the events within a batch (the store's INSERT order is the
    iteration order of the input list; a re-run with the same set
    of events in a different order should produce the same root,
    or the verify CLI's recompute won't match a chain anchor
    written from a different order)."""
    h = hashlib.sha256()
    for ih in sorted(ev.integrity_hash for ev in events):
        h.update(ih.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()
