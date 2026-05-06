#!/bin/bash
# Burst 143 — fix SQLITE_MISUSE under concurrent reads via per-thread
# connection proxy.
#
# Surfaced live 2026-05-05 by diagnose-chat.command (B142). The chat
# tab's GET /conversations/{id}/turns hit
# `sqlite3.InterfaceError: bad parameter or other API misuse` under
# concurrent access from FastAPI worker threads + scheduled task
# dispatches. After the SQLite connection corrupted, subsequent reads
# returned all-None rows that failed Pydantic validation
# (ConversationOut literal-type checks), surfacing as 422 to clients.
#
# Root cause:
#   Python's sqlite3 module reports threadsafety=1 (per PEP 249,
#   threads may NOT share connections at the DB-API level — only the
#   module). The Forest registry opened ONE connection at bootstrap
#   and shared it across all threads. check_same_thread=False
#   disabled Python's safety check but didn't change the DB-API
#   contract. WAL mode made SQLite-level concurrent reads safe, but
#   the Python connection object's shared state was still corrupted
#   by interleaved use across threads.
#
#   The registry.py docstring already promised the right design
#   ("Multiple reader connections against WAL-mode SQLite are
#   fine"), but the implementation never created multiple connections.
#   The implementation drifted from the design intent.
#
# What ships:
#
#   src/forest_soul_forge/registry/registry.py
#     - New _ThreadLocalConn class (~80 lines): proxies sqlite3.
#       Connection's surface (execute / executemany / executescript
#       / cursor / commit / rollback / close) to a per-thread
#       sqlite3.Connection opened lazily on first use. WAL mode
#       (already in CONNECTION_PRAGMAS) lets the per-thread
#       connections coexist safely.
#     - Registry.bootstrap rewritten to construct _ThreadLocalConn
#       instead of a shared sqlite3.connect(). Schema install/verify
#       runs on the lifespan thread (which gets the first per-thread
#       connection); FastAPI workers each get their own connection
#       on first execute().
#     - import threading added.
#
#   verify-b143.command (new at repo root): focused end-to-end
#     verification. Restarts daemon (loads B143), creates a test
#     conversation, adds a status_reporter as participant, POSTs a
#     turn with auto_respond=true. Asserts HTTP 201 (was 422 pre-B143).
#     Cleans up by archiving the test conversation. Dumps tail of
#     err log to _diagnostic_b143_err.txt for inspection.
#
# Verified live 2026-05-05:
#   - Pre-B143 verify run: HTTP 422 + Pydantic ConversationOut
#     all-None validation errors in err log
#   - Post-B143 verify run: HTTP 201, agent_turn body "All systems
#     operational; awaiting further instruction." (real LLM
#     response via llm_think → qwen2.5-coder:7b), 101 tokens used
#   - Err log post-B143: NO new SQLITE_MISUSE entries, NO new
#     ConversationOut validation errors
#   - Chat round-trip works end-to-end through the dispatcher,
#     ToolDispatcher, llm_think, audit chain
#
# Side-effect: Task #22 (post-B142 scheduled task dispatch failures)
# very likely also fixed — same SQLITE_MISUSE root cause. Needs
# verification by triggering dashboard_watcher_healthz_5m post-fix
# and confirming success outcome.
#
# Tradeoffs accepted:
#   - Each thread that touches the registry opens its own sqlite3
#     connection. Memory footprint per connection is small (~50KB);
#     FastAPI's threadpool defaults to ~40 workers, so worst-case
#     ~2MB extra. Acceptable.
#   - Connections leak per-thread until process exit (the daemon's
#     one-process-per-host model makes this fine).
#   - Cross-thread transactions are not supported (transactions stay
#     bounded to the calling thread). Forest's design never used
#     cross-thread transactions; tables/_helpers.transaction() is
#     always called within one thread.
#
# Followups (NOT in this commit):
#   - Concurrency stress test to prevent regression (e.g., 10
#     concurrent reads + 1 write asserting no SQLITE_MISUSE) —
#     queued for B144 or operator-side
#   - Audit other dispatch-path objects for similar API drift like
#     B142's DispatchFailed.reason (DispatchSucceeded /
#     DispatchPendingApproval / DispatchRefused fields) — queued
#   - Update registry.py header docstring (currently warns "this
#     module does not add its own locking — would be a false
#     reassurance"; with B143 the per-thread proxy IS the
#     coordination layer, so the docstring is now slightly stale)

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/registry/registry.py \
        verify-b143.command \
        dev-tools/commit-bursts/commit-burst143-thread-local-conn.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(registry): per-thread sqlite3 connection proxy (B143)

Burst 143. Surfaced live 2026-05-05 by diagnose-chat.command.
Chat tab's GET /conversations/{id}/turns hit sqlite3.InterfaceError:
bad parameter or other API misuse under concurrent access from
FastAPI worker threads + scheduled task dispatches. After SQLite
connection corrupted, reads returned all-None rows that failed
Pydantic ConversationOut validation, surfacing as 422 to clients.

Root cause: Python's sqlite3 module reports threadsafety=1 — per
PEP 249, threads may NOT share connections at DB-API level even
with check_same_thread=False (latter only disables Python's safety
check, not the DB-API contract). Forest opened one connection at
bootstrap and shared it across all threads. WAL mode made
SQLite-level concurrent reads safe but Python connection's shared
state still corrupted by interleaved cross-thread use.

The registry.py docstring already promised the right design
('Multiple reader connections against WAL-mode SQLite are fine')
but implementation never created multiple connections. Drift
between intent and code.

Ships:
- src/forest_soul_forge/registry/registry.py: new
  _ThreadLocalConn class proxies sqlite3.Connection surface
  (execute/executemany/executescript/cursor/commit/rollback/close)
  to per-thread connections opened lazily on first use. Bootstrap
  rewritten to construct proxy instead of shared connect.
- verify-b143.command: focused end-to-end verification. Restarts
  daemon, creates test conversation, sends turn with
  auto_respond=true. Asserts HTTP 201 (was 422 pre-B143).

Verified live 2026-05-05:
- Pre-B143: HTTP 422 + Pydantic ConversationOut all-None errors
- Post-B143: HTTP 201, real agent response 'All systems
  operational; awaiting further instruction.' from
  qwen2.5-coder:7b, 101 tokens used
- Err log post-B143: NO new SQLITE_MISUSE, NO new validation errors

Likely also fixes Task #22 (post-B142 scheduled task dispatch
failures) — same SQLITE_MISUSE root cause. Needs verification.

Tradeoffs accepted:
- Each thread opens own sqlite3 connection (~50KB each, ~2MB
  worst-case at FastAPI's 40-worker default). Acceptable.
- Connections leak per-thread until process exit. Acceptable for
  one-process-per-host daemon model.
- Cross-thread transactions unsupported (Forest never used them).

Followups queued (NOT in this commit):
- Concurrency stress test to prevent regression
- Audit other dispatch dataclass field references for B142-style
  API drift
- Update registry.py header docstring to describe new model"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 143 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
