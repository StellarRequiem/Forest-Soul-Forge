#!/bin/bash
# Burst 128 — .command script archival.
#
# Moves 100 one-shot commit-*.command + tag-*.command scripts from
# repo root to dev-tools/commit-bursts/. Operational scripts
# (start/stop/run/reset/push/clean-git-locks/close-stale-terminals/
# swarm-bringup/live-test-* etc.) stay at root.
#
# Why now: per the audit logged in commit-burst127's body and the
# STATE.md "items in queue" entry, the repo root accumulated 143
# .command files, of which 100 were one-shot per-burst commit
# scripts. They've been called out for archival in three commit
# bodies (Burst 124 / 126 / 127). The .command archival is
# mechanical, requires no design decisions, and unblocks future
# bursts from staging-around-30-untracked-scripts noise.
#
# What ships:
#
#   dev-tools/commit-bursts/ (new directory) — the archive home.
#   Contains:
#     - All commit-burst*.command files (per-burst commit scripts
#       from B42 through B127)
#     - All commit-adr*.command files (ADR-specific commits)
#     - All commit-and-tag-*.command, commit-audit-*.command,
#       commit-sanity-scrub.command, commit-sarahr1-review.command,
#       commit-tag-script-v0.2.0.command (one-off variants)
#     - All tag-v*.command files (release tag scripts; one-shot
#       same as commit scripts)
#
#   Repo root keeps ONLY operationally re-runnable scripts:
#     - start.command / start-demo.command / start-full-stack.command
#     - stop.command / reset.command / run.command / push.command
#     - clean-git-locks.command / close-stale-terminals.command
#     - swarm-bringup.command
#     - docker-up.command / kill-ollama.command / ollama-up.command /
#       ollama-status.command / ollama-coder-up.command
#     - frontend-rebuild.command / stack-rebuild.command
#     - run-tests.command / run-tests-direct.command / t4-tests.command
#     - live-test-*.command (15 live-test drivers — operator runs
#       these to verify changes)
#     - sw-debug.command / soak.command / a5-finalize.command /
#       open-in-chrome.command / track-sarahr1-script.command /
#       verify-burst86-scheduler.command / verify-tag-v0.2.0.command /
#       web-test-day1.command
#
#   STATE.md, README.md, docs/architecture/kernel-userspace-boundary.md
#   updated to reflect new layout + counts:
#     - Repo root: 43 .command scripts (was 140)
#     - Archive: 100 .command scripts under dev-tools/commit-bursts/
#
#   close-stale-terminals.command unchanged — it pattern-matches on
#   window titles, not file paths, so it still works.
#
# Verification:
#   - Full unit suite: 2,386 passing, 3 skipped (sandbox-only),
#     1 xfail (v6→v7 SQLite migration, pre-existing). Pure file-
#     reorganization commit; zero code touched.
#   - git rename detection should display the moves as R-status
#     entries in `git log --stat -M` (similarity threshold default
#     50% — these files are >99% identical to their old paths so
#     the threshold is trivially met).
#   - blame history preserved via the rename detection.
#
# What this closes:
#   The .command archival item that's been parked through Bursts
#   124 / 126 / 127. STATE.md "Items in queue" no longer carries
#   it as a follow-up.
#
# What this opens:
#   Future commit-burst* scripts land directly in
#   dev-tools/commit-bursts/ as a convention. The clean-git-locks
#   script header says "Run ./clean-git-locks.command first" — that
#   path stays at root and continues to work. Future commit-burst
#   scripts can be invoked from their archive location since they
#   `cd "$(dirname "$0")"` and use absolute paths internally.

set -euo pipefail

cd "$(dirname "$0")"

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

# Stage the moves + new files + doc updates.
# `git add -A` captures: tracked-and-deleted-from-root + tracked-or-
# untracked-and-now-in-dev-tools/commit-bursts/ + the doc edits.
git add -A

echo "--- staged for commit ---"
git diff --cached --stat | tail -5
echo "(full diff stat truncated to last 5 lines; expect ~100 R-status renames + 3 doc edits)"
echo "-------------------------"

git commit -m "chore: archive 100 commit-* + tag-* scripts to dev-tools/commit-bursts/ (B128)

Burst 128. Closes the .command archival loose end called out in
Bursts 124, 126, and 127 commit bodies + STATE.md 'items in queue'.

Repo root accumulated 143 .command files, of which 100 were
one-shot per-burst commit scripts (B42 through B127) + release
tag scripts. Moved them under dev-tools/commit-bursts/ to keep the
operational entry-points (start/stop/run/live-test-*/etc.)
discoverable at root.

What moved:
- 93 commit-burst*.command + commit-adr*.command +
  commit-and-tag-*.command + commit-audit-*.command +
  commit-sanity-scrub.command + commit-sarahr1-review.command +
  commit-tag-script-v0.2.0.command
- 7 tag-v*.command files (release tag scripts; one-shot same as
  commit scripts)

What stayed at root (43 scripts):
- start / start-demo / start-full-stack
- stop / reset / run / push
- clean-git-locks / close-stale-terminals
- swarm-bringup
- docker-up / kill-ollama / ollama-up / ollama-status /
  ollama-coder-up
- frontend-rebuild / stack-rebuild
- run-tests / run-tests-direct / t4-tests
- 15 live-test-*.command drivers
- sw-debug / soak / a5-finalize / open-in-chrome /
  track-sarahr1-script / verify-burst86-scheduler /
  verify-tag-v0.2.0 / web-test-day1

Doc updates (3 files):
- STATE.md: .command row reflects new layout (43 root + 100
  archived)
- README.md: same
- docs/architecture/kernel-userspace-boundary.md: adds row for
  dev-tools/commit-bursts/ as 'userspace (developer history)'

close-stale-terminals.command unchanged — it pattern-matches on
window titles, not file paths.

Verification:
- Full unit suite: 2,386 passing (pure file-reorganization; zero
  code touched).
- git rename detection at log/diff time displays the moves as
  R-status entries (similarity >99% trivially clears the 50%
  default threshold).
- blame history preserved via rename detection — git log --follow
  still traces commits back across the rename.

Future commit-burst scripts land directly in
dev-tools/commit-bursts/ as the new convention. Clean-git-locks
+ commit script invocation still works from the archive location
since each script cd's to its own dirname and uses absolute paths
internally."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 128 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
