"""Background memory indexer — ADR-0076 T2 (B320).

When the Memory subsystem writes a new entry, the indexer hook
enqueues an embed task that lands the entry's content into
PersonalIndex (B292). Search via memory_recall.v1 then sees the
new content within ms-to-seconds (embed time + scheduling).

## Design

Memory.append is synchronous; indexing must NOT block it. The
indexer holds an asyncio queue + a worker coroutine. Memory hooks
call ``indexer.enqueue(entry)`` which is non-blocking; the worker
drains the queue and calls PersonalIndex.add for each.

Failure posture:
  - Embedder unreachable → enqueue keeps building up; worker
    retries with exponential backoff. The Memory writes
    themselves never fail because of indexer state.
  - PersonalIndex.add raises → log + skip that entry. Recall over
    the un-indexed entry returns nothing for that text but the
    audit-chain + SQL truth is intact.

## Lifecycle

``start()`` spawns the worker task; ``stop()`` cancels + awaits
clean shutdown. The daemon's lifespan hook (app.py) drives both.
Tests construct an indexer manually + drain the queue inline.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IndexerTask:
    """One queued indexing task. Kept tight: doc_id + text are
    everything PersonalIndex.add needs; source + tags are
    optional metadata the recall path surfaces in results."""
    doc_id: str
    text: str
    source: Optional[str] = None
    tags: Optional[tuple[str, ...]] = None


class MemoryIndexer:
    """Async queue + worker that feeds PersonalIndex from Memory writes.

    Construction is cheap; the worker doesn't start until ``start()``
    is awaited. Caller owns the index instance (typically one per
    daemon process).
    """

    def __init__(self, index: Any, max_retries: int = 3):
        self._index = index
        self._queue: asyncio.Queue[IndexerTask] = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._max_retries = max_retries
        # Counters for /memory/indexer/status (a future endpoint).
        self.enqueued = 0
        self.indexed = 0
        self.failed = 0

    def enqueue(
        self,
        doc_id: str,
        text: str,
        *,
        source: Optional[str] = None,
        tags: Optional[tuple[str, ...]] = None,
    ) -> None:
        """Non-blocking enqueue. Memory hooks call this from sync
        code; the queue is unbounded so this never blocks. A
        production deployment that needs bound queues swaps in
        ``asyncio.Queue(maxsize=N)`` and accepts back-pressure
        through queue full handling (deferred — current scale
        doesn't warrant)."""
        try:
            self._queue.put_nowait(IndexerTask(
                doc_id=doc_id, text=text,
                source=source, tags=tags,
            ))
            self.enqueued += 1
        except Exception:
            # Queue full (only if maxsize set) — drop + log. The
            # entry is still in the chain + SQL; only the index
            # surface misses it.
            logger.exception(
                "indexer enqueue dropped doc_id=%s", doc_id,
            )

    async def start(self) -> None:
        """Spawn the worker coroutine. Idempotent: if already
        started, no-op."""
        if self._worker_task is not None:
            return
        self._stop_event.clear()
        self._worker_task = asyncio.create_task(
            self._run(), name="memory-indexer",
        )

    async def stop(self) -> None:
        """Signal shutdown + wait for the worker to drain the
        in-flight task. Items still in the queue are abandoned
        (the underlying memory_entries rows are intact; recall
        misses them but the next index rebuild picks them up)."""
        if self._worker_task is None:
            return
        self._stop_event.set()
        try:
            await asyncio.wait_for(self._worker_task, timeout=5.0)
        except asyncio.TimeoutError:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except (asyncio.CancelledError, Exception):
                pass
        self._worker_task = None

    async def _run(self) -> None:
        """Worker loop. Pulls one task at a time + indexes it.
        Backs off on persistent failures so a stuck embedder
        doesn't busy-spin."""
        while not self._stop_event.is_set():
            try:
                task = await asyncio.wait_for(
                    self._queue.get(), timeout=0.5,
                )
            except asyncio.TimeoutError:
                continue  # poll the stop_event again

            await self._handle(task)

    async def _handle(self, task: IndexerTask) -> None:
        """Index one task with retry/backoff. PersonalIndex.add
        is synchronous in B292's in-memory implementation; we
        wrap in run_in_executor so the embed call doesn't block
        the event loop (sentence-transformers cold start can be
        ~3-5s on first call). Kwargs are filtered so None source
        and None tags fall back to PersonalIndex.add's defaults
        rather than overriding them with explicit Nones."""
        loop = asyncio.get_running_loop()
        attempt = 0
        backoff = 0.5
        # Build kwargs once; the closure captures the dict so we
        # don't re-construct it per retry.
        kwargs: dict[str, Any] = {"doc_id": task.doc_id, "text": task.text}
        if task.source is not None:
            kwargs["source"] = task.source
        if task.tags:
            kwargs["tags"] = list(task.tags)
        while attempt < self._max_retries:
            try:
                await loop.run_in_executor(
                    None,
                    lambda: self._index.add(**kwargs),
                )
                self.indexed += 1
                return
            except Exception as e:
                attempt += 1
                if attempt >= self._max_retries:
                    self.failed += 1
                    logger.exception(
                        "indexer giving up on doc_id=%s after %d "
                        "attempts: %s",
                        task.doc_id, attempt, e,
                    )
                    return
                logger.warning(
                    "indexer retry %d/%d for doc_id=%s: %s",
                    attempt, self._max_retries, task.doc_id, e,
                )
                await asyncio.sleep(backoff)
                backoff *= 2

    # ---- introspection ----

    def status(self) -> dict[str, Any]:
        """Snapshot for /memory/indexer/status (future endpoint)."""
        return {
            "enqueued": self.enqueued,
            "indexed":  self.indexed,
            "failed":   self.failed,
            "queue_depth": self._queue.qsize(),
            "running":  self._worker_task is not None and not self._worker_task.done(),
        }
