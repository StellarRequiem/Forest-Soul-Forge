#!/bin/bash
# Burst 161 — Concurrency stress test for the Registry's
# _ThreadLocalConn proxy. Closes the lingering Phase 1 polish item
# from the chat-bug-arc session (B142/B143/B144).
#
# Why: B143 fixed a live SQLITE_MISUSE outage (chat tab returned 422
# on /conversations/{id}/turns under concurrent scheduled-task
# dispatches). Root cause was Python sqlite3 module's threadsafety=1:
# connections cannot be shared across threads at the DB-API contract
# level, even with check_same_thread=False (which only disables
# Python's own safety check).
#
# B143 fixed it with _ThreadLocalConn (registry.py:90) — a per-thread
# connection proxy. The fix held; no new SQLITE_MISUSE has surfaced.
# But there's no regression test guarding the fix shape: a future
# refactor that flips back to a shared connection would silently
# reproduce the outage. This commit is the durable guard.
#
# What ships:
#
#   tests/unit/test_registry_concurrency.py — 4 tests:
#
#     test_concurrent_reads
#       16 threads each running 50 read iterations against the agents
#       table. Verifies zero sqlite3.InterfaceError raises and zero
#       all-None corrupted rows (B143's specific symptoms — the
#       Pydantic 422 surfaced because corrupted rows had None on
#       NOT-NULL columns).
#
#     test_concurrent_reads_and_writes
#       1 writer thread + 7 reader threads running for 600ms against
#       a registry under WAL mode. Asserts both writers and readers
#       made progress (no deadlock; no silent freeze) and zero
#       errors. Mirrors the live load shape that exploded in B143:
#       chat-tab GETs concurrent with scheduled-task INSERTs.
#
#     test_per_thread_connections
#       Structural invariant: 8 threads each grab their underlying
#       sqlite3.Connection via proxy._get(); the resulting set of
#       connection ids must be > 1. If a future refactor reverts
#       _ThreadLocalConn to a single shared connection, all 8
#       threads would see the same id — this test catches that
#       directly.
#
#     test_close_on_one_thread_doesnt_affect_others
#       Verifies the _ThreadLocalConn docstring's contract: close()
#       on one thread only closes that thread's connection. Sibling
#       threads' connections survive. Important because daemon
#       teardown can land on an arbitrary thread.
#
# All four tests run in <1s combined. They use Registry.bootstrap
# directly (no FastAPI surface needed), so they're fast unit tests
# that fire on every CI run.
#
# Verification:
#   PYTHONPATH=src python3 -m pytest tests/unit/test_registry_concurrency.py
#   -> 4 passed in ~0.9s
#
#   Combined with the touched-module sweep:
#   pytest tests/unit/test_{posture_gate_step,conversation_helpers,
#                            trait_engine,genre_engine,constitution,
#                            tool_catalog,example_plugins,
#                            registry_concurrency}
#   -> 289 passed, 1 skipped (the soulux-computer-control scaffold
#       allowlist skip, which clears when ADR-0048 T2 lands)
#
# Closes task #23 from the post-B143 punch list. The same test
# infrastructure can be extended with new threads-on-tables tests
# whenever a new accessor surfaces concurrency-sensitive paths.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add tests/unit/test_registry_concurrency.py \
        dev-tools/commit-bursts/commit-burst161-concurrency-stress-test.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "test(registry): concurrency stress test (B161)

Burst 161. Closes the post-B143 Phase 1 polish item: a regression
guard for the _ThreadLocalConn fix that resolved the live
SQLITE_MISUSE outage (chat tab 422 on concurrent reads + scheduled-
task dispatches, 2026-05-05).

The fix held. Without a test, a future refactor that reverts to a
single shared sqlite3.Connection would silently reproduce the
outage — Python's threadsafety=1 means connections cannot be
shared across threads at the DB-API contract level even with
check_same_thread=False.

Ships tests/unit/test_registry_concurrency.py with 4 tests:
- test_concurrent_reads: 16 threads x 50 read iterations. Zero
  sqlite3.InterfaceError + zero all-None corrupted rows (B143's
  exact symptoms).
- test_concurrent_reads_and_writes: 1 writer + 7 readers x 600ms.
  Both make progress; no deadlock or silent freeze.
- test_per_thread_connections: 8 threads' connection ids must be
  > 1 in the resulting set. A revert to a shared connection would
  produce 1 id; this test catches that directly.
- test_close_on_one_thread_doesnt_affect_others: verifies the
  proxy's docstring contract that close() is per-thread.

All 4 tests run in <1s. PYTHONPATH=src pytest
tests/unit/test_registry_concurrency.py -> 4 passed.

Closes task #23 from the post-B143 punch list. The same shape of
test can be extended to other concurrency-sensitive paths as they
surface."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 161 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
