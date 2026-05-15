#!/bin/bash
# Burst 320 - ADR-0076 T2: background memory indexer hook.
#
# Wires Memory.append (scope='personal' only) to an asyncio
# queue + worker that lands content into PersonalIndex (B292).
# Search via personal_recall.v1 (T4, next burst) sees the new
# content within ms-to-seconds of the write.
#
# What ships:
#
# 1. src/forest_soul_forge/core/memory_indexer.py (NEW):
#    - IndexerTask frozen dataclass (doc_id, text, source, tags).
#    - MemoryIndexer class — asyncio.Queue + worker coroutine.
#      enqueue() is non-blocking + sync (Memory.append calls it
#      from sync code under write_lock); start()/stop() drive
#      the worker; _handle() retries with exponential backoff
#      (0.5s, 1s, 2s) via run_in_executor so the embedder's
#      cold-load doesn't block the event loop.
#    - Counters: enqueued / indexed / failed for a future
#      /memory/indexer/status endpoint.
#    - Kwargs filtering: None source / empty tags drop to the
#      PersonalIndex.add defaults rather than passing explicit
#      None overrides.
#
# 2. src/forest_soul_forge/core/memory/__init__.py:
#    - Memory dataclass gains optional indexer: Any = None.
#    - Memory.append() — after successful INSERT, if scope ==
#      'personal' AND indexer is not None, call
#      indexer.enqueue(doc_id=entry_id, text=content,
#      source=f'memory:{layer}:{scope}', tags=...). Failures
#      are swallowed so an indexer hiccup never fails a memory
#      write. The chain + SQL is the source of truth; the
#      vector index is a derivative.
#    - Scope-filter rationale: only operator-context (personal)
#      entries leak into PersonalIndex. Agent-private journal
#      entries stay out, preserving privacy isolation across
#      instances.
#
# 3. src/forest_soul_forge/daemon/deps.py:
#    - Pulls memory_indexer from app.state + passes through to
#      Memory(...). None when the operator hasn't opted in.
#
# 4. src/forest_soul_forge/daemon/app.py:
#    - Lifespan: when FSF_PERSONAL_INDEX_ENABLED=true, construct
#      a PersonalIndex + MemoryIndexer + await start(). Both
#      stashed on app.state (memory_indexer + personal_index).
#    - Lifespan finally: stop() drains in-flight; queued tasks
#      at shutdown are abandoned (rebuilt by `fsf index rebuild`
#      in T5).
#    - Startup diagnostic surfaces enabled / disabled / failed.
#
# Tests (test_memory_indexer.py - 9 cases):
#   - enqueue is non-blocking + bumps the counter
#   - worker drains queue into the index with exact kwargs
#     (None source / empty tags filtered out)
#   - flaky index succeeds on retry (2 fails, then success)
#   - persistent failure records failed=1 and worker moves on
#   - start() is idempotent
#   - stop() is idempotent + safe before start()
#   - stop() drains in-flight tasks
#   - status() returns the documented snapshot shape
#   - IndexerTask is frozen (caller can't mutate enqueued tasks)
#
# Sandbox-verified: 9/9 tests pass + Memory.append integration
# smoke confirms scope-filter gate works (personal enqueues,
# private does NOT, None-indexer path is a clean no-op).
#
# === ADR-0076 progress: 2/6 tranches closed (T1+T2) ===
# Next: T3 hybrid BM25+cosine RRF, T4 personal_recall.v1 tool,
# T5 fsf index rebuild CLI, T6 runbook.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/memory_indexer.py \
        src/forest_soul_forge/core/memory/__init__.py \
        src/forest_soul_forge/daemon/app.py \
        src/forest_soul_forge/daemon/deps.py \
        tests/unit/test_memory_indexer.py \
        dev-tools/commit-bursts/commit-burst320-adr0076-t2-indexer.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(memory): ADR-0076 T2 - background indexer hook (B320)

Burst 320. Wires Memory.append (scope='personal' only) to an
asyncio queue + worker that lands content into PersonalIndex
(B292). Search via personal_recall.v1 (T4, next burst) will
see the new content within ms-to-seconds of the write.

What ships:

  - core/memory_indexer.py (NEW): MemoryIndexer with asyncio
    queue + worker coroutine. IndexerTask frozen dataclass.
    enqueue() is non-blocking + sync (Memory.append calls it
    from sync code under write_lock). start()/stop() drive
    the worker. _handle() retries with exponential backoff
    (0.5s/1s/2s) via run_in_executor so the embedder cold-load
    doesn't block the event loop. Counters enqueued/indexed/
    failed for the future /memory/indexer/status endpoint.
    Kwargs filtering: None source / empty tags fall back to
    PersonalIndex.add defaults rather than overriding.

  - core/memory/__init__.py: Memory dataclass gains optional
    indexer field. After successful INSERT, if scope=='personal'
    AND indexer is set, enqueue(doc_id, text, source, tags).
    Scope-filter rationale: only operator-context entries leak
    into PersonalIndex; agent-private journal entries stay out,
    preserving privacy isolation across instances. Failures
    are swallowed so an indexer hiccup never fails a memory
    write — chain + SQL is the truth, the vector index is a
    derivative.

  - daemon/deps.py: passes app.state.memory_indexer through to
    Memory(...). None when the operator hasn't opted in.

  - daemon/app.py: lifespan constructs PersonalIndex +
    MemoryIndexer when FSF_PERSONAL_INDEX_ENABLED=true; both
    stashed on app.state. Lifespan finally drains the worker
    cleanly. Startup diagnostic surfaces enabled/disabled/
    failed.

Tests: test_memory_indexer.py — 9 cases covering non-blocking
enqueue + counter, worker drain with exact-kwargs assertion,
retry/backoff (2 failures then success), terminal failure with
worker still alive, idempotent start/stop, stop-drain in-flight,
status() shape, frozen IndexerTask.

Sandbox-verified 9/9 tests pass + Memory.append integration
smoke confirms scope-filter gate (personal enqueues, private
does NOT, None-indexer path is clean no-op).

ADR-0076 progress: 2/6 tranches closed (T1 substrate + T2
indexer). Next bursts: T3 hybrid BM25+cosine RRF, T4
personal_recall.v1 tool, T5 fsf index rebuild CLI, T6 runbook."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 320 complete - ADR-0076 T2 indexer hook shipped ==="
echo "ADR-0076: 2/6 tranches closed."
echo ""
echo "Press any key to close."
read -n 1
