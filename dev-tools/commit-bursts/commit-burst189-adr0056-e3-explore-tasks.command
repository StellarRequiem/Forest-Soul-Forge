#!/bin/bash
# Burst 189 — ADR-0056 E3 — explore-mode scheduled tasks for
# Smith.
#
# Adds two scheduled-task entries to config/scheduled_tasks.yaml
# that fire Smith with task_caps.mode=explore on a configurable
# cadence. Both DISABLED by default — operator opts in
# deliberately because frontier costs apply (Anthropic API spend
# per fire).
#
# Why two tasks (and what each does):
#
#   smith_explore_codebase_4h (every 4h, ≤6 fires/day)
#     Smith scans the codebase for ONE improvement candidate
#     worth taking to a work-mode cycle. Output is a JSON
#     summary (title, rationale, complexity, target_files,
#     related_adrs, blast_radius_concerns). Memory_recall
#     dedups against prior findings.
#
#   smith_explore_audit_chain_6h (every 6h, ≤4 fires/day)
#     Smith scans the past 6h of audit-chain activity for
#     coordination patterns or coordination gaps. Output is a
#     JSON summary with severity tags + a recommendation
#     (no_action / investigate / operator_attention). Surfaces
#     in the display-mode pane (E4) when that lands.
#
# Combined ceiling: ≤10 frontier dispatches per day at full
# enablement. Operator scales independently.
#
# Per ADR-0056 D2: explore-mode dispatches go through
# ModeKitClampStep (B188) which refuses any tool with
# side_effects != read_only. Smith CANNOT mutate code or
# state in this mode regardless of posture. llm_think is
# read_only so the dispatch passes the clamp; if Smith tried
# to call code_edit or shell_exec the dispatcher would refuse
# with reason='mode_kit_clamp'.
#
# Per ADR-0045 / posture: explore-mode dispatches are
# read_only, so PostureGateStep passes regardless of YELLOW /
# GREEN / RED. RED posture does NOT silence explore mode —
# operator must `enabled: false` the task or stop the daemon
# to silence Smith fully overnight.
#
# What ships:
#
#   config/scheduled_tasks.yaml:
#     - 2 NEW entries scoped to Smith's instance_id
#       (experimenter_1de20e0840a2, established 2026-05-07
#       03:22Z). Both enabled=false. Operator flips after
#       the first work-mode cycle establishes baseline trust.
#     - Long inline prompt blocks specify expected output
#       shape (JSON). Keeping prompts in the YAML rather than
#       a separate file means the cadence + prompt evolve
#       together; ADR-0041 D5 calls this 'co-located
#       configuration.'
#
# No code changes — the scheduler's tool_call task type
# (B89) already supports task_caps in its config block since
# its initial ship. Verified by reading src/forest_soul_forge/
# daemon/scheduler/task_types/tool_call.py:82.
#
# Per ADR-0044 D3: additive YAML entries. Pre-E3 daemons
# reading post-E3 scheduled_tasks.yaml just see two more
# tasks they ignore (because enabled=false). Post-E3 daemons
# reading pre-E3 scheduled_tasks.yaml work unchanged.
#
# Per ADR-0001 D2: scheduled fires modify Smith's per-instance
# memory state (procedural shortcuts grow, semantic findings
# accrue). Identity stays constant.
#
# Verification:
#   yaml.safe_load() parses cleanly; both Smith entries have
#   the right shape (task_caps.mode=explore, enabled=false,
#   schedule strings valid per ADR-0041 schedule grammar).
#
# Operator-facing follow-up (NOT in this commit):
#   - Run a work-mode cycle first to validate Smith on a small
#     target. Recommended: BACKLOG #14 (ADR-0036 cross-agent
#     contradiction scan T1 — small, well-scoped). Cycle
#     report + diff review in display-mode pane (E4 — but E4
#     ships in B190 so for B189 the operator does the review
#     manually via git diff + the audit chain).
#   - After 2-3 clean work-mode cycles, flip
#     smith_explore_codebase_4h to enabled=true and watch the
#     audit chain. Each fire writes a tool_call_succeeded
#     event with Smith's findings; Smith's memory accrues the
#     candidates.
#   - audit-chain task can stay disabled until E4 lands — the
#     output is meant for the display-mode pane.
#
# Next burst: B190 — E4 display-mode chat-tab pane.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add config/scheduled_tasks.yaml.example \
        dev-tools/commit-bursts/commit-burst189-adr0056-e3-explore-tasks.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(experimenter): ADR-0056 E3 — explore-mode scheduled tasks (B189)

Burst 189. Adds two scheduled-task entries that fire Smith with
task_caps.mode=explore on configurable cadence. Both DISABLED
by default — operator opts in after the first work-mode cycle
establishes baseline trust.

smith_explore_codebase_4h: every 4h, scans codebase for one
improvement candidate worth taking to a work-mode cycle.
Output: JSON with title, rationale, complexity, target files,
related ADRs, blast-radius concerns.

smith_explore_audit_chain_6h: every 6h, scans the last 6h
of audit-chain activity for coordination patterns / gaps.
Output: JSON with severity-tagged findings + recommendation
(no_action / investigate / operator_attention).

Combined ceiling: ≤10 frontier dispatches/day at full
enablement.

Per ADR-0056 D2: explore-mode dispatches go through
ModeKitClampStep (B188) which refuses any tool with
side_effects != read_only. Smith cannot mutate state in this
mode regardless of posture. llm_think is read_only so the
dispatch passes the clamp.

No code changes — the scheduler's tool_call task type already
supports task_caps in its config block (B89, verified). YAML
edits land cleanly through yaml.safe_load.

Per ADR-0044 D3: additive YAML entries; pre-E3 daemons just
see two more disabled tasks they ignore.

Per ADR-0001 D2: scheduled fires modify Smith's per-instance
memory state (procedural + semantic memory grow). Identity
stays constant.

Operator follow-up (NOT in this commit): run a work-mode
cycle first to validate Smith on a small target (suggested
BACKLOG #14, ADR-0036 contradiction scan T1). After 2-3
clean cycles, flip smith_explore_codebase_4h to enabled=true.

Next burst: B190 — E4 display-mode chat-tab pane."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 189 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
