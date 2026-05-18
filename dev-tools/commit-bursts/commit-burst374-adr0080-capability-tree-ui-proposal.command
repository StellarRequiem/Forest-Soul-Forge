#!/bin/bash
# Burst 374 - ADR-0080 per-agent capability tree UI (doc only).
#
# Drafts the ADR for the operator-requested video-game-style
# per-agent capability sub-page (request landed during D-1 of the
# 2026-05-17 wire-readiness sweep). Doc-only commit; implementation
# tranches T1-T5 land as separate bursts after operator green-light.
#
# What ADR-0080 specifies:
#
#   New frontend tab 'Agent Capabilities' alongside the existing 15
#   tabs. Picker selects an agent; tree-view renders that agent's
#   capabilities as a dependency-shaped tree:
#     ─ tools
#       ├─ code_read.v1  ✓ (hard-wired)
#       │  └─ code_edit  ✓ (hard-wired, depends on code_read)
#       ├─ web_fetch    ✗ (broken: provider offline)
#       └─ ...
#     ─ skills
#       └─ archive_evidence  ⏳ (in-progress: staged not installed)
#     ─ mcp_plugins
#       └─ ms-office-suite  ✓ (operator-toggleable)
#
#   Three visual states per node:
#     ✓ live - exercisable now
#     ✗ broken / unavailable - greyed, tooltip with reason
#     ⏳ in-progress - amber, staged or queued
#
#   Toggle state independent of color:
#     🔒 hard-wired - role + genre + constitution invariant forbids
#     ☐  operator-toggleable - click flips per-agent posture
#
#   Composition rules (strict precedence):
#     1. Constitution allowed_tools (immutable, hard-wired)
#     2. Genre risk_profile.max_side_effects ceiling (contract floor)
#     3. Per-agent posture overrides (ADR-0036)
#     4. Runtime availability (tool_runtime + provider liveness +
#        section-04/section-14 harness output)
#
#   Backend: 2 new read-only endpoints
#     GET  /agents/{id}/capability-tree
#     POST /agents/{id}/capability-toggle
#   Composes from existing substrate; no new tables; no schema
#   migration. Toggle mutates posture (NEVER constitution).
#
#   Tranches (5-6 bursts total to close):
#     T1 backend substrate (1 burst)
#     T2 frontend module (1-2 bursts)
#     T3 posture wiring for toggles + audit chain event (1 burst)
#     T4 inferred tool->tool prerequisite edges (1 burst, optional)
#     T5 operator runbook + CLOSE (1 burst)
#
# Why this is its own ADR (not folded into existing tabs):
#   Global Tool Registry + Skills tabs answer 'what does the
#   substrate have?' The per-agent tab answers 'what does THIS
#   agent actually have RIGHT NOW?' Different questions; the
#   operator needs both. Conflating them clutters the global view
#   or hides the global picture behind a picker. Plus: per-agent
#   posture is per-agent, not per-role; two agents in the same
#   role can have different effective reach if the operator
#   toggled differently.
#
# Why doc-only first:
#   Operator green-light on the ADR proposal opens T1. Five-tranche
#   arc has enough surface area (backend + frontend + posture
#   wiring + audit chain event) that the design needs to be agreed
#   before code lands. Same pattern as ADR-0079 (harness arc landed
#   doc B351 first, then T1-T6 sequentially).
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm of NOT writing this ADR: operator's request from the
#     wire-readiness sweep has no paper trail yet; future sessions
#     would re-derive the design. ADR-0080 captures the decision
#     surface so the arc can land deliberately.
#   Prove non-load-bearing: doc only. No substrate or frontend
#     change in this commit.
#   Prove alternative is strictly better: alternatives are leaving
#     the request in chat history (lossy across sessions) or
#     implementing without an ADR (no paper trail for the
#     architectural decisions). The ADR pattern is project
#     convention; mirror it.
#
# Verification after this commit lands:
#   1. Read docs/decisions/ADR-0080-per-agent-capability-tree-ui.md
#      end-to-end.
#   2. Operator green-lights or amends the proposal.
#   3. T1 starts as a separate burst once green-lit.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0080-per-agent-capability-tree-ui.md \
        dev-tools/commit-bursts/commit-burst374-adr0080-capability-tree-ui-proposal.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(adr): ADR-0080 per-agent capability tree UI (B374)

Burst 374. Doc-only proposal for the operator-requested video-
game-style per-agent capability sub-page (request from D-1 of
the 2026-05-17 wire-readiness sweep).

What ADR-0080 specifies:
  New frontend tab 'Agent Capabilities' alongside the existing 15.
  Per-agent dependency tree of tools + skills + MCP plugins with
  three visual states (live / broken / in-progress) and two
  toggle modes (hard-wired by constitution+genre / operator-
  toggleable via posture). Backend adds 2 read-only endpoints
  (capability-tree GET + capability-toggle POST); composes from
  existing substrate; no new tables.

Composition rules (strict precedence):
  1. Constitution allowed_tools (immutable, hard-wired)
  2. Genre risk_profile.max_side_effects ceiling
  3. Per-agent posture overrides (ADR-0036)
  4. Runtime availability (tool_runtime + provider liveness +
     section-04 / section-14 harness output)

Tranches: T1 backend / T2 frontend / T3 toggle wiring / T4
inferred edges (optional) / T5 runbook+CLOSE. 5-6 bursts to
close.

Why per-agent (vs. extending global Tool Registry tab):
  Global tabs answer 'what does the substrate have?'; this tab
  answers 'what does THIS agent have RIGHT NOW?' Different
  questions; operator needs both. Per-agent posture is per-agent
  not per-role - two agents in same role can have different
  effective reach.

Why doc-only first (matches ADR-0079 pattern):
  Five-tranche arc has enough surface (backend + frontend +
  posture wiring + audit chain event) that design needs operator
  green-light before code lands.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: operator request has no paper trail yet; future
    sessions would re-derive.
  Prove non-load-bearing: doc only.
  Prove alternative is better: chat-only would lose detail
    across sessions; ad-hoc impl skips architectural decisions
    the ADR records.

After this lands: operator reviews + green-lights to open T1."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 374 complete - ADR-0080 proposed ==="
echo "=========================================================="
echo "Review: docs/decisions/ADR-0080-per-agent-capability-tree-ui.md"
echo "Green-light opens T1 (backend substrate burst)."
echo ""
echo "Press any key to close."
read -n 1 || true
