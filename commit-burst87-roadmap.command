#!/usr/bin/env bash
# Burst 87: full state-and-roadmap audit doc
#
# After Burst 86.3 closed the chat-tab UX bugs, operator asked for
# a full reading of where the project stands: what's shipped, what's
# in flight, what got cut off as a side-quest, and what's left to
# polish/double-check.
#
# This commit lands docs/roadmap/2026-05-03-state-and-roadmap.md.
# Honest status doc — no padding, calls out the wonky stuff
# (sandbox locks, chat copy-paste, scheduler T3-T6 outstanding,
# v0.4 architectural decisions still in front of orchestrator).
#
# Sections:
#   1. Tag state (v0.3.0 + commits since)
#   2. What shipped this session (8 bursts)
#   3. ADR-0041 in-flight (T1-T2 done, T3-T7 outstanding)
#   4. Audit-remediation backlog (P0 done, P1/P2 open)
#   5. Frontend polish queue (5 fixed, 4 open)
#   6. v0.4 architectural decisions awaiting orchestrator
#   7. Quality gaps (test coverage, observability, docs)
#   8. Side-quest detours (Run 001, app-roadmap, multi-user)
#   9. Recommended next 5 bursts (in priority order)
#  10. Wonky-stuff list (the honest dark-corner list)
#
# This is the canonical "where are we" doc for the v0.3 → v0.4
# transition. Future sessions should read this before deciding
# what to work on next.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 87 — full state-and-roadmap audit ==="
echo
clean_locks
git add docs/roadmap/2026-05-03-state-and-roadmap.md
git add commit-burst87-roadmap.command
clean_locks
git status --short
echo
clean_locks
git commit -m "docs(roadmap): full state-and-roadmap audit (2026-05-03)

Operator-requested honest status doc covering everything in
flight, deferred, or cut off as side-quest detours. The
canonical 'where are we' doc for the v0.3 → v0.4 transition.

Contents:

1. Tag state — v0.3.0 + every commit since, with one-line
   summaries so the dispatch story is followable.

2. What shipped this session — 8 named bursts:
   - Burst 71: ADR-0040 design (trust-surface decomposition rule)
   - Bursts 72-76: memory.py → memory/ package decomp (T2)
   - Bursts 77-80: writes.py → writes/ package decomp (T3)
   - Burst 81: Run 001 FizzBuzz autonomous coding test
   - Burst 82: full audit (P0/P1/P2 finds, drift sentinel)
   - Burst 83: audit remediation (STATE/README/CHANGELOG)
   - Burst 84: v0.3.0 release tag
   - Burst 85: ADR-0041 design (set-and-forget orchestrator)
   - Burst 86: scheduler runtime (T1-T2)
   - Bursts 86.1-86.3: chat-tab UX hotfixes

3. ADR-0041 in-flight — T1-T2 landed (Scheduler runtime +
   GET status routes). T3 (tool_call task type), T4 (scenario
   task type), T5 (config-driven seeding), T6 (SQLite v13
   persistence), T7 (write routes — scoped, gated) all still
   outstanding.

4. Audit-remediation backlog — P0 cleared in Burst 83.
   P1 still open: ADR forward-references for scheduler,
   FizzBuzz scenario port to YAML config. P2 still open:
   doc-style normalization, test fixture sweep.

5. Frontend polish queue — 9 items found via Chrome MCP
   debug session. 5 fixed (chat.js import, [hidden] CSS,
   new-room cache miss, bridge cache miss, add-participant
   dialog, sticky-bar latency hint). 4 still open: chat
   stream live updates (real async-dispatch), chat tab
   pagination, agents-tab archive flow, governance-tab
   constitution diff viewer.

6. v0.4 architectural decisions — 5 still in front of the
   orchestrator. Multi-user direction (cloud relay vs
   Tauri installer; recommendation: B). Customer/vertical
   thesis (regulated vs SMB). Mobile platform (Tauri vs
   React Native vs PWA-first). Free-tier policy (local-only
   forever?). Repo branding (harness-app + harness-bridge
   are placeholders).

7. Quality gaps — test coverage holes (scheduler runtime
   has 30 unit tests, zero integration; chat tab has zero
   tests; frontend has zero tests period). Observability
   (no structured logging, no latency p50/p95/p99 per
   tool/agent/model). Docs (architecture/layout.md is two
   weeks stale post-decomp).

8. Side-quest detours — Run 001 (planned 30min, took 2hr
   due to 5 driver bugs). App-roadmap reaction (third-party
   AI proposal review). Multi-user discussion (cloud vs
   installer thesis). All three valuable but consumed
   bursts that didn't ship code.

9. Recommended next 5 bursts in priority order:
   - Burst 87 (this commit): roadmap doc
   - Burst 88: frontend audit pass (open every tab, test
     every button, fix latent bugs — same Chrome MCP method
     that found the chat-tab bugs)
   - Burst 89: ADR-0041 T3 (tool_call task type) — closes
     the loop with ADR-0036 verifier
   - Burst 90: ADR-0041 T6 (SQLite v13 persistence) —
     scheduled_task_state table so cooldowns survive restart
   - Burst 91: ADR-0041 T4 (scenario task) + FizzBuzz YAML
     port — closes Burst 81 P1 item
   - Burst 92: diagnostics dashboard (latency percentiles
     per tool/agent/model) — observability gap

10. Wonky stuff — honest list of dark corners:
    - Sandbox can't always rm .git/index.lock
    - examples/audit_chain.jsonl vs data/audit_chain.jsonl
      confusion (now documented)
    - chat copy-paste broken (fixed in 86.3 but reveals
      window.prompt UX is a footgun)
    - 8k LoC drift between STATE.md and disk (fixed in 83
      but caught nothing earlier)
    - registry.sqlite at repo root (gitignored, fine)
    - sandbox Python 3.10 vs project 3.11+ (xfailed
      with reason; tracked but unfixable in sandbox)

This doc is the durable artifact of the session. Future
session pickup: read this first, then read the latest
audit doc, then pick the highest-priority Burst 88 item."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 87 landed. Roadmap doc is the canonical pickup point."
echo "Read docs/roadmap/2026-05-03-state-and-roadmap.md to see the full picture."
echo ""
read -rp "Press Enter to close..."
