#!/bin/bash
# Burst 260.1 — fix Reality + Security pane "stuck at loading…" bug
# surfaced by the post-B260 live smoke test.
#
# === Diagnosis ===
#
# Smoke test: clicked Reality tab in SoulUX → 4 fetches fire
# (status + ground-truth + recent-events + corrections) → network
# log showed status 200 for some, "pending" forever for others → DOM
# stayed at "loading…" for all 4. Refreshing didn't help. Subsequent
# fetches hung indefinitely, eventually freezing the renderer
# (CDP Runtime.evaluate timeouts on every async/await call).
#
# Pattern: /ground-truth (no audit-chain dep, no registry dep)
# returned 200 OK reliably. /status, /recent-events, /corrections
# (chain or registry deps) hung. Other endpoints elsewhere in the
# daemon (/healthz, /audit/tail, /agents) kept working — those use
# async def + chain.tail().
#
# === Root cause ===
#
# reality_anchor.py + security.py originally used sync `def` handlers
# that called a helper which read the ENTIRE 6 MB audit_chain.jsonl
# into memory via Path.read_text(encoding="utf-8") per call, then
# json.loads'd 9806 lines, then filtered. That's ~100 ms per call.
# When SoulUX fires 4 fetches in parallel via Promise.all, those 4
# sync handlers consume 4 FastAPI threadpool slots simultaneously.
# Combined with the statusbar's healthz/audit/tail polls (every 10s)
# and the always-on chat / preview polls, the threadpool gets close
# enough to saturated that the reality-anchor calls queue
# indefinitely. The browser-side fetch promise never resolves; DOM
# stays at "loading…".
#
# === Fix ===
#
# 1. Convert all reality_anchor.py + security.py handlers from
#    `def` to `async def` — matches the audit.py pattern. async
#    handlers don't tie up threadpool workers.
# 2. Replace the bespoke "read whole file" helpers with calls to
#    `chain.tail(n)`, which uses a deque-bounded streaming reader
#    (the same primitive /audit/tail already uses). Memory and CPU
#    are now O(search_window) instead of O(chain_size).
# 3. Function return shape is preserved — tests assert on
#    body["count"], body["events"][i]["event_type"], etc. The new
#    code returns the same minimal field set plus none of the
#    chain-internal fields (signature, schema_version).
#
# Side effects:
# - All 5 reality-anchor endpoints now async.
# - All 5 security endpoints now async.
# - Helper functions renamed/refactored to take the AuditChain
#   object instead of a Path. Internal-only — no external callers.
# - Pyproject [test] / [dev] extras unchanged.
#
# Verification plan after push:
# 1. Force-restart daemon to pick up new handlers.
# 2. Live-test /reality-anchor/status from chrome address bar →
#    should return 401 (auth-error) instantly. If it hangs or 404s,
#    backend regressed.
# 3. Load SoulUX, click Reality tab → status card should populate
#    with fact count + 24h counts, not stay at "loading…".
# 4. Same for Security tab.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/routers/reality_anchor.py \
        src/forest_soul_forge/daemon/routers/security.py \
        dev-tools/commit-bursts/commit-burst260-1-async-tail-fix.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(daemon): async + chain.tail() for Reality/Security panes (B260.1)

Burst 260.1. Live smoke-test on 2026-05-13 found the SoulUX
Reality and Security panes stuck at 'loading…' even though
network log showed some fetches returning 200. Subsequent
fetches hung in 'pending' indefinitely, freezing the renderer.

Root cause: reality_anchor + security routers used sync 'def'
handlers that called Path.read_text on the 6 MB audit chain
per call, then filtered. Under SoulUX's 4-concurrent-Promise.all
load they consumed 4 threadpool slots simultaneously, and
combined with always-on polls (statusbar, chat, preview) the
threadpool got saturated enough for the reality-anchor calls
to queue forever from the browser's perspective.

Fix mirrors the audit.py pattern that already works:

- All 5 reality-anchor endpoints + 5 security endpoints now
  'async def'.
- Helper '_read_recent_events' (and the security equivalent)
  rewritten to use AuditChain.tail(n) — the same primitive
  /audit/tail uses. Memory and CPU are now O(search_window)
  instead of O(chain_size).
- search_window defaulted to 2000 entries: ample for the v1
  pane since reality-anchor + security-scan events are sparse
  and operator-recent.

Tests assert on body['count'] / body['events'][i]['event_type'] /
body['decision'] — all preserved. Internal-only refactor, no
external API contract changes.

Verification arc on next session: full pytest collection +
live SoulUX smoke (force-restart daemon → click both tabs →
panes populate, no longer stuck at 'loading…')."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 260.1 complete — async + chain.tail() shipped ==="
echo "Run force-restart-daemon.command to pick up the new handlers."
echo "Press any key to close."
read -n 1
