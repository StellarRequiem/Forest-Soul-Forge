#!/bin/bash
# Burst 229 — ADR-0055 M5: post-install grant-to-agent flow.
#
# Closes Phase A end-to-end. After this lands, an operator can:
#   1. Browse the Marketplace tab
#   2. Click Install on an entry
#   3. Pick an agent + trust tier from the inline picker that
#      replaces the Install button
#   4. Click "Grant all" — every tool the plugin contributes is
#      granted to the chosen agent via the existing ADR-0060
#      grant endpoint (B220)
#   5. The agent can now dispatch the new tools immediately
#
# What ships (frontend only — no backend changes):
#
# 1. Trust-tier auto-derivation per ADR-0055 D7:
#      read_only  → green
#      network    → green
#      filesystem → yellow
#      external   → yellow
#    Operator can override in the picker before clicking Grant.
#
# 2. On successful install, the Install button's actions column
#    is replaced with a grant picker containing:
#      - Agent selector (populated from cached /agents fetch)
#      - Trust tier selector (defaulted per D7)
#      - "Grant all" button → iterates contributes.tools[],
#        calling POST /agents/{id}/tools/grant per tool
#      - "Skip" button → collapses to "Installed ✓" with no
#        grants issued
#
# 3. Grant loop captures per-tool failures (400 unknown tool,
#    409 already-granted) and surfaces a summary toast:
#      - All success: "Granted N tools to <agent> tier=<tier>"
#      - Partial:      "Granted N / M; K failed" warning toast
#                      with per-failure reasons
#      - All failed:   error toast with the full failure list
#
# 4. Agent list cached after first fetch so the picker doesn't
#    re-poll /agents for every install (low session-cost
#    optimization; refresh-button-driven re-fetch is the escape
#    hatch if a new agent gets birthed mid-marketplace-session).
#
# Verification:
#   - node --check clean on marketplace.js
#   - 80 backend regression tests pass (marketplace_index +
#     catalog_grants + posture_matrix + audit_chain)
#
# Visual verification path (operator after this lands):
#   1. Reload SoulUX page (frontend cache bust)
#   2. Browse Marketplace tab, pick an entry, click Install
#   3. After daemon downloads + installs, the row's right side
#      becomes the grant picker
#   4. Pick operator_companion → default trust_tier shown (yellow
#      for external-tier plugins)
#   5. Click Grant all → toast confirms N tools granted
#   6. Switch to Agents tab → the new grants appear in the Tool
#      grants pane (B223) for operator_companion
#   7. Agent can dispatch the new tools — same constitution gate
#      that grants the tool sees the grant flow through (ADR-0060
#      T2, B220)
#
# Phase A status after this burst:
#   M1 ✓ index endpoint (B184, pre-session)
#   M3 ✓ install endpoint (B227)
#   M4 ✓ Browse pane (B228)
#   M5 ✓ post-install grant (THIS BURST)
#   M2 — sibling repo scaffold (Open Decision 1)
#   M6 — signing pipeline (Open Decision 2)
#
# What remains: the two blocked tranches (M2 + M6) need external
# decisions resolved. Phase B/C/D can begin in parallel anytime
# (reviews, telemetry, agent templates).
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: pure frontend addition wired to existing
#                  endpoints (M3 from B227, ADR-0060 grant from
#                  B220).

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add frontend/js/marketplace.js \
        dev-tools/commit-bursts/commit-burst229-marketplace-grant-flow.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(frontend): post-install grant-to-agent flow (B229)

Burst 229 / ADR-0055 M5. Closes Phase A end-to-end. After install,
the marketplace entry's Install button is replaced with an inline
agent + trust-tier picker and a 'Grant all' button. Clicking it
iterates the plugin's contributes.tools[], hitting the existing
ADR-0060 grant endpoint per tool.

Trust tier auto-derived per ADR-0055 D7:
  read_only/network -> green
  filesystem/external -> yellow

Per-grant failures captured into a summary toast (full success /
partial / all-failed). Agent list cached after first fetch so the
picker doesn't poll /agents per install.

Verification: node --check clean, 80 backend regression tests pass.

Phase A status post-B229:
  M1 (B184) M3 (B227) M4 (B228) M5 (B229) all live.
  M2 sibling repo + M6 signing still queued on external decisions.

Operator workflow now end-to-end click-only:
  Marketplace tab -> Install -> pick agent -> Grant all -> dispatch.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: pure frontend addition over existing endpoints
                 (M3 from B227, ADR-0060 grant from B220)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 229 complete ==="
echo "=== Phase A end-to-end. Marketplace browse -> install -> grant -> dispatch is click-only. ==="
echo "Press any key to close."
read -n 1
