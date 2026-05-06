#!/bin/bash
# Burst 135 — Session handoff document.
#
# 2026-05-05 session shipped Bursts 124-134 (11 commits closing the
# v0.6 kernel arc burst-deliverable phases). Context window is at
# its limit; this commit lands a SESSION-HANDOFF.md the next Cowork
# session can read first to pick up with high fidelity.
#
# What ships:
#
#   SESSION-HANDOFF.md (new at repo root) — comprehensive bridge:
#     §1 Repository state at handoff (HEAD, working tree, hardware)
#     §2 What this session shipped (B124-134 commit table)
#     §3 v0.6 kernel arc phase status (ADR-0044)
#     §4 Mid-flight items NOT yet executed:
#         §4a LocalLLaMA Discord outreach drafts (3 variants A/B/C
#             pending choice + channel name + Send permission)
#         §4b 24/7 ops 3-layer recipe (overhead reduction + process
#             priority + launchd plists) — fully drafted, not
#             touched on Alex's system
#         §4c Model installation (Qwen 2.5 7B recommended; ollama
#             list pending)
#         §4d Specialist agent stable design (dashboard_watcher,
#             signal_listener, incident_correlator,
#             paper_summarizer, vendor_research, status_reporter)
#     §5 STATE.md backlog status (mostly ✅; ADR-0042 T5, ADR-0043
#         #4, ADR-0036, ADR-0038 T4-T6, mfa_check still gated)
#     §6 Operating conventions discovered this session
#     §7 Pending operator decisions
#     §8 Where-to-look-first table by topic
#     §9 Verification commands the next session can run
#     §10 Last conversational state (verbatim resume point)
#
#   Plus auto-memory updates (not in this commit; persisted to
#   spaces/.../memory/ outside the repo per Cowork conventions):
#     - project_v0_6_kernel_arc_status.md
#     - feedback_finder_typejump_workflow.md
#     - user_hardware_and_24_7_ops.md
#     - project_audit_chain_canonical_form.md
#     - MEMORY.md index updated with 5 new pointers
#
# Why commit this to the repo: the handoff file lives in version
# control so Alex can share the URL with a new session, the file
# is discoverable via git log, and the next session has a clean
# checkpoint marker (whatever updates the file or deletes it post-
# resume is its own commit).
#
# Verification:
#   - SESSION-HANDOFF.md renders cleanly as markdown
#   - All cross-references resolve (links to spec, runbook, ADR-0044,
#     pitch, quickstart, KERNEL.md, integrator-pitch.md, etc.)
#   - File is at repo root for easy discoverability
#   - Auto-memory entries (4 new + 1 index update) persisted to
#     /Users/llm01/Library/Application Support/Claude/.../memory/
#     so the next session sees them on first MEMORY.md read.
#
# Resume point for next session: §4b (24/7 ops L1/L2/L3 setup) —
# ask whether to drive computer-use through L1+L2 visually or hand
# Alex the launchd plists with paths filled in.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add SESSION-HANDOFF.md \
        dev-tools/commit-bursts/commit-burst135-session-handoff.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs: SESSION-HANDOFF.md for cross-session continuity (B135)

Burst 135. The 2026-05-05 session is at context limit after shipping
Bursts 124-134 (11 commits closing the v0.6 kernel arc burst-
deliverable phases). This commit lands a comprehensive handoff
document the next Cowork session can read first to resume with
high fidelity.

Ships:

- SESSION-HANDOFF.md (new at repo root): conversation-state bridge.
  Sections cover repository state at handoff, the 11 commits
  shipped this session, v0.6 phase status, mid-flight items NOT
  yet executed (Discord outreach drafts; 24/7 ops 3-layer recipe;
  model install; specialist agent stable design), backlog status,
  operating conventions discovered this session (Finder type-jump
  + cmd+O for commit scripts; clean-git-locks lock dance; audit
  chain canonical form), pending operator decisions, where-to-look
  table, verification commands, and last conversational state.

- Auto-memory updates persisted to ~/Library/.../memory/ (outside
  this commit per Cowork convention):
  - project_v0_6_kernel_arc_status.md
  - feedback_finder_typejump_workflow.md
  - user_hardware_and_24_7_ops.md
  - project_audit_chain_canonical_form.md
  - MEMORY.md index updated with 5 new pointers

Resume point for next session: §4b (24/7 ops L1/L2/L3) — ask
whether to drive computer-use through Layer 1+2 visually or hand
Alex the launchd plists with paths filled in.

Verification:
- File renders cleanly as markdown
- Cross-references resolve to existing files
- Auto-memory entries land in the persistent memory directory

This is the natural pause point for the v0.6 kernel arc. Every
burst-deliverable phase is shipped; what remains is external
integrator validation (months not bursts) and operator decisions
(Apple Dev account, plugin secrets storage, etc.)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 135 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
