#!/bin/bash
# Burst 192 — ADR-0056 E6 — operator safety runbook +
# posture-swap UI in the Cycles pane. CLOSES the ADR-0056 arc.
#
# Two deliverables:
#
#   1. docs/runbooks/experimenter-cycle-runbook.md (NEW)
#      ~280 lines covering identity check, posture brake-pedal
#      semantics, when-to-flip-RED checklist, cycle review
#      checklist, abandon-a-cycle paths, self-augmentation
#      flow with operator gates, frontier cost guardrails,
#      success markers, escalation triggers (the 'should NOT
#      happen' list), and a quick-reference table of paths.
#      Cross-links to ADR-0056, ADR-0045, ADR-0001, ADR-0019.
#
#   2. Posture-swap widget at the top of the Cycles pane.
#      Three buttons (green / yellow / red) wired to the
#      existing /agents/{id}/posture endpoints from ADR-0045.
#      Click confirms destructive flips (red, green); yellow
#      flips without confirm. Highlights the active posture.
#      Includes a one-line current-state label ('current:
#      yellow') and a runbook link.
#
# What ships:
#
#   docs/runbooks/experimenter-cycle-runbook.md (NEW):
#     Full operator-facing prose runbook. Pairs with ADR-0056
#     for design rationale; this doc covers day-to-day
#     operations.
#
#   frontend/index.html:
#     - chat-pane-cycles body gains a chat-cycles-posture bar
#       at the top: three posture buttons + current-state
#       label + runbook link (target=_blank to the markdown
#       file).
#
#   frontend/js/chat.js:
#     - wireCyclesRefresh() now also wires the posture
#       buttons + kicks an initial refreshSmithPosture call.
#     - NEW refreshSmithPosture(): GETs /posture, updates
#       label + active-button highlight.
#     - NEW _onPostureChange(): confirms red/green flips,
#       POSTs /posture with reason='operator-driven via
#       Cycles pane (ADR-0056 E6)', refreshes label.
#
#   frontend/css/style.css:
#     - NEW .chat-cycles-posture (bar layout) +
#       .chat-cycles-posture-btn (default + hover + active +
#       disabled states) styles. Active state color-codes
#       per posture (green, yellow, red). Matches the
#       existing posture toggle aesthetic from the Agents
#       tab.
#
# Per ADR-0044 D3: zero kernel ABI changes. Frontend +
# documentation only. The posture endpoint already existed
# (ADR-0045 T1, B114). This burst just exposes it from the
# Cycles pane.
#
# Per ADR-0001 D2: posture is per-instance state, not
# identity. Smith's constitution_hash + DNA stay constant
# across posture changes; the audit chain captures every
# posture_changed event.
#
# Verification:
#   - Live: dashboard at http://127.0.0.1:5173 → Chat → Cycles
#     pane shows the posture toggle at the top, three buttons,
#     'current: yellow' label (matches Smith's birth posture
#     from B187). Clicking 'red' confirms then flips; the
#     audit chain records the change.
#
# Status — ADR-0056 implementation arc COMPLETE:
#   E1 — Smith born (B187 + B187 followup)
#   E2 — ModeKitClampStep + task_caps.mode (B188)
#   E3 — Explore-mode scheduled tasks (B189)
#   E4 — Display-mode cycles pane (B190)
#   E5 — Cycle decision endpoint + buttons (B191)
#   E6 — Runbook + posture-swap UI (B192) ← THIS
#
# Smith now has the full design surface: branch-isolated
# work cycles, three-mode discipline, posture-clamped
# autonomy, operator review with approve/deny/counter,
# explore-mode timer, and a documented operator workflow.
#
# Next major direction — the operator picks. Suggested:
#   - Run a first work-mode cycle on a small target (e.g.
#     BACKLOG #14 ADR-0036 contradiction scan T1)
#   - Resume ADR-0055 marketplace tranches (M3-M7)
#   - Resume ADR-0054 T5b chat thumbs UI / T6 lifespan
#     wiring
#   - ADR-0053 per-tool plugin grant substrate (T1-T6)

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/runbooks/experimenter-cycle-runbook.md \
        frontend/index.html \
        frontend/js/chat.js \
        frontend/css/style.css \
        dev-tools/start-frontend.command \
        dev-tools/commit-bursts/commit-burst192-adr0056-e6-runbook-posture-ui.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(experimenter): ADR-0056 E6 — runbook + posture UI (B192) — closes arc

Burst 192. Closes the ADR-0056 implementation arc with the
two final deliverables:

1. docs/runbooks/experimenter-cycle-runbook.md (NEW) —
   ~280 lines. Identity check, posture brake-pedal semantics,
   when-to-flip-RED checklist, cycle review checklist,
   abandon-a-cycle paths, self-augmentation flow with
   operator gates, frontier cost guardrails, success
   markers, escalation triggers, quick-reference table.

2. Posture-swap widget at the top of the Cycles pane. Three
   buttons (green/yellow/red) wired to existing
   /agents/{id}/posture endpoints (ADR-0045). Click confirms
   destructive flips. Highlights active posture. Includes
   current-state label + runbook link.

Plus dev-tools/start-frontend.command (NEW) — operator
launcher for frontend/serve.py. Surfaced when Vite/dev
server stopped responding mid-session and the dashboard
failed to load. Cd's into frontend/ then runs serve.py
directly (not as a module — module path fails inside
frontend/ because there's no nested frontend/ subdir).

Per ADR-0044 D3: zero kernel ABI changes. Frontend +
documentation only.

Per ADR-0001 D2: posture is per-instance state, not
identity. constitution_hash + DNA constant across posture
changes; audit chain records every posture_changed event.

ADR-0056 implementation arc COMPLETE:
- E1 Smith born (B187 + followup)
- E2 ModeKitClampStep + task_caps.mode (B188)
- E3 Explore-mode scheduled tasks (B189)
- E4 Display-mode cycles pane (B190)
- E5 Cycle decision endpoint + buttons (B191)
- E6 Runbook + posture UI (B192)

Smith has the full design surface — branch-isolated work
cycles, three-mode discipline, posture-clamped autonomy,
operator review with approve/deny/counter, explore-mode
timer, and documented workflow."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 192 commit + push complete — ADR-0056 arc CLOSED ==="
echo "Press any key to close this window."
read -n 1
