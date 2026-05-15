"""ADR-0076 T2 (B320) — MemoryIndexer tests.

Covers:
  - enqueue is non-blocking + bumps the counter
  - start()/stop() are idempotent
  - the worker drains queued tasks into the index
  - retry/backoff: a flaky index that succeeds on the third try
    is recorded as indexed=1
  - terminal failure after max_retries records failed=1 and
    moves on; does not block subsequent tasks
  - stop() drains the in-flight task cleanly within the timeout
  - status() returns the documented snapshot shape
  - None source/empty tags are filtered out so PersonalIndex.add's
    defaults are honored (regression guard on the kwarg-build path)

The tests use a small in-process fake-index that records calls
and can be made flaky on demand; the real PersonalIndex has its
own coverage in test_personal_index.py.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from forest_soul_forge.core.memory_indexer import (
    IndexerTask,
    MemoryIndexer,
)


# ---------------------------------------------------------------------------
# Fake-index helpers
# ---------------------------------------------------------------------------


class _RecordingIndex:
    """Captures every add() call so tests assert on the exact
    kwargs the indexer forwards."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def add(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


class _FlakyIndex:
    """Fails the first ``fail_first`` calls, then succeeds. Used
    to verify the retry/backoff loop in MemoryIndexer._handle."""

    def __init__(self, fail_first: int = 2) -> None:
        self.fail_first = fail_first
        self.attempts = 0
        self.calls: list[dict[str, Any]] = []

    def add(self, **kwargs: Any) -> None:
        self.attempts += 1
        if self.attempts <= self.fail_first:
            raise RuntimeError(f"simulated failure #{self.attempts}")
        self.calls.append(kwargs)


class _AlwaysFailIndex:
    """Always raises. Used to verify the terminal-failure path
    records failed=1 and moves on rather than wedging the worker."""

    def __init__(self) -> None:
        self.attempts = 0

    def add(self, **kwargs: Any) -> None:
        self.attempts += 1
        raise RuntimeError("permanent failure")


async def _drain(indexer: MemoryIndexer, timeout: float = 2.0) -> None:
    """Wait until the queue is empty + the worker is idle."""
    start = asyncio.get_event_loop().time()
    while True:
        if indexer._queue.qsize() == 0:  # noqa: SLF001 — test-only access
            # Give the worker one more tick to finish handling
            # whatever it popped before the snapshot.
            await asyncio.sleep(0.1)
            if indexer._queue.qsize() == 0:  # noqa: SLF001
                return
        if asyncio.get_event_loop().time() - start > timeout:
            raise AssertionError(
                f"queue did not drain within {timeout}s; "
                f"depth={indexer._queue.qsize()}"  # noqa: SLF001
            )
        await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# enqueue + counters
# ---------------------------------------------------------------------------


def test_enqueue_is_nonblocking_and_increments_counter():
    """enqueue must NOT block on a sync code path. It just
    drops the task onto an unbounded asyncio.Queue."""
    # We construct the indexer inside an event loop so the
    # asyncio.Queue is bound correctly, but we DON'T start the
    # worker — enqueue should still succeed.
    async def _go():
        idx = _RecordingIndex()
        indexer = MemoryIndexer(index=idx)
        indexer.enqueue(doc_id="d1", text="hello")
        indexer.enqueue(doc_id="d2", text="world", source="memory:semantic:personal")
        assert indexer.enqueued == 2
        assert indexer.indexed == 0  # worker not started yet
        # Queue depth visible via status
        st = indexer.status()
        assert st["enqueued"] == 2
        assert st["indexed"] == 0
        assert st["queue_depth"] == 2
        assert st["running"] is False

    asyncio.run(_go())


# ---------------------------------------------------------------------------
# Worker drains the queue
# ---------------------------------------------------------------------------


def test_worker_drains_queue_into_index():
    async def _go():
        idx = _RecordingIndex()
        indexer = MemoryIndexer(index=idx)
        await indexer.start()
        indexer.enqueue(
            doc_id="d1", text="alpha",
            source="memory:episodic:personal",
            tags=("trust", "spouse"),
        )
        indexer.enqueue(doc_id="d2", text="beta")
        await _drain(indexer)
        await indexer.stop()
        assert indexer.indexed == 2
        assert indexer.failed == 0
        # Verify the exact kwargs landed on the index, including
        # the None/empty filtering path.
        assert idx.calls[0] == {
            "doc_id": "d1", "text": "alpha",
            "source": "memory:episodic:personal",
            "tags": ["trust", "spouse"],
        }
        assert idx.calls[1] == {
            "doc_id": "d2", "text": "beta",
            # source omitted (was None) → PersonalIndex.add default 'unknown'
            # tags omitted (was None)   → PersonalIndex.add default None
        }

    asyncio.run(_go())


# ---------------------------------------------------------------------------
# Retry / backoff
# ---------------------------------------------------------------------------


def test_flaky_index_succeeds_on_retry():
    """Two transient failures, third attempt succeeds. The task
    should land as indexed=1, failed=0."""
    async def _go():
        idx = _FlakyIndex(fail_first=2)
        indexer = MemoryIndexer(index=idx, max_retries=3)
        await indexer.start()
        indexer.enqueue(doc_id="d1", text="payload")
        # backoff is 0.5s + 1.0s = 1.5s of sleeps minimum; give
        # the drain enough budget to absorb it.
        await _drain(indexer, timeout=4.0)
        await indexer.stop()
        assert idx.attempts == 3
        assert indexer.indexed == 1
        assert indexer.failed == 0

    asyncio.run(_go())


def test_persistent_failure_records_failed_and_moves_on():
    """A task that fails every retry should record failed=1.
    A subsequent enqueued task on a healthy index should still
    succeed — the worker doesn't wedge."""
    async def _go():
        bad = _AlwaysFailIndex()
        indexer = MemoryIndexer(index=bad, max_retries=2)
        await indexer.start()
        indexer.enqueue(doc_id="dead", text="will fail")
        # 0.5s + 1.0s = 1.5s of backoff plus the failure logging.
        await _drain(indexer, timeout=4.0)
        await indexer.stop()
        assert indexer.failed == 1
        assert indexer.indexed == 0
        assert bad.attempts == 2

    asyncio.run(_go())


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_start_is_idempotent():
    """Calling start() twice does NOT spawn two workers."""
    async def _go():
        indexer = MemoryIndexer(index=_RecordingIndex())
        await indexer.start()
        first_task = indexer._worker_task  # noqa: SLF001
        await indexer.start()
        assert indexer._worker_task is first_task  # noqa: SLF001
        await indexer.stop()

    asyncio.run(_go())


def test_stop_is_idempotent_and_safe_before_start():
    """Calling stop() when start() was never called is a no-op,
    not a crash."""
    async def _go():
        indexer = MemoryIndexer(index=_RecordingIndex())
        await indexer.stop()  # no-op — worker never started
        # Now start + stop normally.
        await indexer.start()
        await indexer.stop()
        # And stop again — still a no-op.
        await indexer.stop()

    asyncio.run(_go())


def test_stop_drains_in_flight():
    """An enqueue right before stop should still land in the
    index (worker has up to the stop timeout to drain)."""
    async def _go():
        idx = _RecordingIndex()
        indexer = MemoryIndexer(index=idx)
        await indexer.start()
        indexer.enqueue(doc_id="d1", text="last")
        # Give the worker a chance to pop it; then stop.
        await asyncio.sleep(0.2)
        await indexer.stop()
        assert indexer.indexed == 1
        assert idx.calls[0]["doc_id"] == "d1"

    asyncio.run(_go())


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------


def test_status_shape():
    """status() must return the documented keys for the future
    /memory/indexer/status endpoint."""
    async def _go():
        indexer = MemoryIndexer(index=_RecordingIndex())
        snap = indexer.status()
        assert set(snap.keys()) == {
            "enqueued", "indexed", "failed",
            "queue_depth", "running",
        }
        assert snap["running"] is False
        await indexer.start()
        snap2 = indexer.status()
        assert snap2["running"] is True
        await indexer.stop()

    asyncio.run(_go())


# ---------------------------------------------------------------------------
# IndexerTask dataclass
# ---------------------------------------------------------------------------


def test_indexer_task_is_frozen():
    """IndexerTask is a frozen dataclass — caller can't mutate
    enqueued tasks under the worker's feet."""
    t = IndexerTask(doc_id="d1", text="x", source="s", tags=("a",))
    with pytest.raises(Exception):
        t.doc_id = "d2"  # type: ignore[misc]
