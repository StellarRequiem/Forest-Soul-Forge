#!/bin/bash
# Burst 116 — STATE/CHANGELOG refresh covering Bursts 109-115.
#
# Bookkeeping pass closing the documentation gap from Burst 109
# through Burst 115. Bursts 109-115 shipped:
#   - v0.5.0-rc tag
#   - ADR-0043 follow-ups #1, #2 (substrate + operator), #3
#   - ADR-0045 Agent Posture / Trust-Light System (design + 4
#     implementation tranches)
#
# Numbers refreshed against disk:
#   Tests:       2289 → 2386 (+97 across Bursts 109-115)
#   Source LoC:  48,760 → 50,289 (+~1,500)
#   ADRs:        40 → 41 files (+ADR-0045)
#   Schema:      v13 → v15 (+v14 plugin grants, +v15 posture)
#   Audit events: 67 → 70 (+3: agent_plugin_granted/_revoked,
#                          agent_posture_changed)
#   Commits:     264 → 273 (+9)
#   Scripts:     120 → 130 (+10)
#
# Bookkeeping only — no functional changes.

set -euo pipefail

cd "$(dirname "$0")"

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add STATE.md CHANGELOG.md

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs: STATE + CHANGELOG refresh for v0.5 close (Bursts 109-115)

Bookkeeping pass closing the documentation gap from Burst 109
through Burst 115. Three deliverable arcs reflected:

1. ADR-0043 follow-ups #1/#2/#3 shipped end-to-end:
   - Burst 111: per-tool requires_human_approval mirroring
   - Burst 112: frontend Tools-tab plugin awareness
   - Bursts 113a + 113b: plugin grants substrate + operator surface
     (table + dispatcher + HTTP + CLI + 2 audit events)

2. ADR-0045 Agent Posture / Trust-Light System:
   - Burst 113.5 (after 113a): design doc filed
   - Burst 114: schema v15 agents.posture + PostureGateStep
   - Burst 114b: HTTP + CLI + agent_posture_changed audit
   - Burst 115: per-grant trust_tier enforcement + 3×3 precedence

3. v0.5.0-rc tagged at Burst 110 as the implementation-complete
   checkpoint for the initial v0.5 arc (Bursts 95-108).

Numbers refreshed against disk:
  Tests          2,289 → 2,386 (+97)
  Source LoC     48,760 → 50,289 (+~1,500)
  ADRs           40 → 41 files / 38 → 39 unique numbers
  Schema         v13 → v15 (added v14 grants, v15 posture)
  Audit events   67 → 70 (+3)
  Commits        264 → 273 (+9)
  .command       120 → 130 (+10)

CHANGELOG [Unreleased] section grew with the Bursts 109-115
detail; the prior v0.5 initial arc section (Bursts 95-108) is
preserved as the lower portion of [Unreleased] until v0.5.0
final ships.

No functional changes."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 116 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
