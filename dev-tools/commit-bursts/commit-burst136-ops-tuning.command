#!/bin/bash
# Burst 136 — 24/7 ops tuning utilities.
#
# Follow-on to SESSION-HANDOFF §4b. The handoff documented a 3-layer
# 24/7 ops recipe (overhead reduction + process priority + launchd
# plists) as un-executed at the time of the previous session's close.
# This commit lands the operator-facing utilities for Layers 1 and 2.
# Layer 3 (launchd auto-start) is a different shape (per-user
# ~/Library/LaunchAgents/ plists, not repo-root scripts) and is queued
# as Task #4.
#
# What ships:
#
#   quit-bg-apps.command (new at repo root) — one-shot freer of
#     background-app RAM. Gentle AppleScript "quit app" for Spotify,
#     Discord, Docker Desktop. Preserves user state (Spotify queue,
#     Discord login, Docker volumes). Re-runnable any time RAM gets
#     tight. Verified end-to-end on the live stack: total RSS dropped
#     10.68 -> 8.94 GB (1.74 GB freed) when run against the
#     2026-05-05 baseline. The Activity-Monitor per-process column
#     suggested ~6 GB savings, but RSS double-counts shared libraries
#     so the real number is smaller. Memory pressure stays GREEN.
#
#   tune-priority.command (new at repo root) — bumps Ollama and Forest
#     daemon to user-interactive QoS via "sudo taskpolicy -c
#     user-interactive -p PID". Idempotent; safe to re-run after
#     daemon restarts. Includes a verify pass via taskpolicy -G and a
#     reminder that KEEP_ALIVE in .env requires an Ollama restart to
#     take effect (Ollama only reads env on serve startup).
#
#   .env.example — documents two new operator-tunable Ollama vars
#     with rationale comments. OLLAMA_KEEP_ALIVE=-1 pins the loaded
#     model in memory indefinitely; without it the model unloads
#     after 5 min idle, paying 3-10s warmup on every wake. Critical
#     for ADR-0041 set-and-forget specialist agents.
#     OLLAMA_NUM_PARALLEL=1 forces single-stream inference for
#     predictable tokens/sec on 16 GiB unified memory. Both are
#     commented-out in .env.example since the local .env is the
#     authoritative copy (and is gitignored per long-standing
#     convention).
#
# What doesn't ship:
#
#   .env — gitignored; per-operator config. Operator's local .env was
#     edited in-place to add OLLAMA_KEEP_ALIVE=-1 and OLLAMA_NUM_PARALLEL=1.
#     Future operators read .env.example to know the available knobs.
#
#   examples/audit_chain.jsonl — the live audit chain has accumulated
#     test-run drift since the last housekeeping sync. Belongs in a
#     separate housekeeping commit, not this ops-tuning one. Keeps
#     this commit focused and reviewable.
#
# Why these are userspace, not kernel: per the kernel-userspace
# boundary doc, repo-root .command scripts are operator-facing
# day-to-day ops, not part of the kernel ABI. They don't change
# Forest's API surface, schema, or seven ABI surfaces. The .env.example
# update is a documentation surface for the operator's local .env (which
# flows through Ollama and the Forest daemon's existing config
# machinery without code changes).
#
# Verification:
#   - quit-bg-apps.command ran clean on the 2026-05-05 stack: all
#     three target apps confirmed quit, total RSS dropped 1.74 GB,
#     Forest daemon /runtime/provider remained active=local,
#     status=ok throughout, all 5 native Ollama models still pulled
#   - tune-priority.command structure verified at file-author time;
#     live sudo run is operator-side and doesn't gate the commit
#   - .env.example renders cleanly, comments are documentation-grade
#
# Resume point: T4 (Layer 3 launchd plists) is queued. Tasks #2 and
# #3 of the cross-session task list close with this commit (the
# operator-side sudo run of tune-priority and Ollama restart for
# KEEP_ALIVE to take effect are post-commit work).

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add quit-bg-apps.command \
        tune-priority.command \
        .env.example \
        dev-tools/commit-bursts/commit-burst136-ops-tuning.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(ops): 24/7 tuning utilities — KEEP_ALIVE pinning + priority bump (B136)

Burst 136. Lands two operator-facing utilities and an .env.example
update to support the ADR-0044 follow-on 24/7 ops setup that
SESSION-HANDOFF §4b documented as un-executed at handoff time.

Ships:

- quit-bg-apps.command (new at repo root): one-shot freer of
  background-app RAM. Gentle AppleScript quit for Spotify, Discord,
  Docker Desktop. Preserves user state. Re-runnable any time RAM
  gets tight. Verified on live stack: total RSS dropped 10.68 ->
  8.94 GB (1.74 GB freed). Memory pressure stays GREEN.

- tune-priority.command (new at repo root): bumps Ollama and
  Forest daemon to user-interactive QoS via sudo taskpolicy.
  Idempotent. Verifies via taskpolicy -G. Reminds operator that
  KEEP_ALIVE in .env requires an Ollama restart to take effect
  (Ollama reads env only on serve startup).

- .env.example: documents two new operator-tunable Ollama vars,
  OLLAMA_KEEP_ALIVE=-1 and OLLAMA_NUM_PARALLEL=1, with rationale
  comments. KEEP_ALIVE pins loaded model in memory indefinitely
  (eliminates 3-10s warmup on idle dispatches — critical for
  ADR-0041 set-and-forget specialist agents). NUM_PARALLEL=1 forces
  single-stream inference for predictable tokens/sec on 16 GiB
  unified memory.

Why userspace, not kernel: per kernel-userspace-boundary doc,
repo-root *.command scripts are operator-facing day-to-day ops,
not part of the seven kernel ABI surfaces. The .env.example update
is documentation for the operator's local (gitignored) .env; the
values flow through Ollama and Forest's existing config machinery
without code changes.

Closes Tasks #2 and #3 of the cross-session task list. Layer 3
(launchd plists, Task #4) is queued — different shape (per-user
~/Library/LaunchAgents/, not repo-root scripts).

Verification:
- quit-bg-apps.command end-to-end on 2026-05-05 stack: all three
  apps confirmed quit, RSS down 1.74 GB, Forest daemon
  /runtime/provider continues active=local status=ok, 5 native
  Ollama models still pulled
- tune-priority.command structure verified at file-author time;
  live sudo run is operator-side
- .env.example renders cleanly with documentation-grade comments"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 136 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
