#!/bin/bash
# Burst 223 — ADR-0060 T6 frontend grants pane.
#
# Closes the ADR-0060 arc. With T1-T5 already shipped, operators
# could use runtime grants only via curl. T6 adds an operator-
# visible surface on the Agents tab so the workflow is point-and-
# click.
#
# Files:
#   frontend/index.html  — new sibling panel after agents-split:
#     "Tool grants (runtime)" with agent selector, status, refresh
#     button, grants list, and an inline grant form
#     (tool name / version / trust tier / reason / Grant button).
#
#   frontend/js/catalog-grants.js (NEW)
#     - start() fetches /agents and populates the selector
#     - selector change triggers _fetchAndRender against
#       /agents/{id}/tools/grants
#     - per-row Revoke button hits DELETE; toast on success
#     - Grant form posts to /agents/{id}/tools/grant; toasts the
#       resulting grant's tier + audit_seq
#     - 400 from ADR-0060 D5 (unknown tool) surfaces as a clear
#       error toast carrying the daemon's detail message
#     - Auto-refreshes when the operator switches to the Agents
#       tab so newly-forged tools are immediately grantable
#
#   frontend/js/app.js — import + start() wiring next to
#     forgedProposalsPanel (the parallel sibling-panel from B205).
#
# UX shape mirrors the existing "Forged proposals" sibling on the
# Approvals tab — same panel chrome, same toast posture on
# success/failure, same per-row action button pattern.
#
# Verification:
#   - node --check on catalog-grants.js + app.js — both clean
#   - all 9 required HTML element ids present
#   - 52 backend unit tests pass (catalog_grants + matrix +
#     tool_dispatch + plugin_grants regression sweep)
#
# Visual verification (operator-side, after this lands):
#   1. Pull / restart the daemon
#   2. Open the SoulUX, switch to the Agents tab
#   3. Scroll past the agents-split — new "Tool grants (runtime)"
#      panel appears
#   4. Pick an agent from the selector
#   5. List populates with active grants (empty for fresh agents)
#   6. Fill the form, click Grant → toast confirms, list refreshes
#   7. Click Revoke on any row → toast confirms, list updates
#
# ADR-0060 status post-B223: T1-T6 all shipped. Full operator
# pipeline closes: forge a tool via /tools/forge, install via
# /tools/install, grant to any existing agent via the new UI,
# dispatch. constitution_hash stays immutable throughout.
#
# What we deliberately did NOT do:
#   - Per-agent grant pane inside the agent-detail right pane.
#     Sibling pattern is more discoverable and lets one operator
#     manage grants across multiple agents from the same scroll
#     position.
#   - Inline tool autocomplete from /tools/catalog. The current
#     form trusts the operator to type a valid name.vversion;
#     ADR-0060 D5 catches typos at POST time. Autocomplete is a
#     future UX burst, not a substrate concern.
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: ABI unchanged — pure frontend addition wired
#                  to endpoints from B220.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add frontend/index.html \
        frontend/js/catalog-grants.js \
        frontend/js/app.js \
        dev-tools/commit-bursts/commit-burst223-adr-0060-t6-frontend.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(frontend): ADR-0060 T6 runtime grants pane (B223)

Burst 223. Closes the ADR-0060 arc. Operators can now grant
catalog tools to existing agents from the SoulUX Agents tab
without curl.

New sibling panel on the Agents tab (mirrors the 'Forged
proposals' pattern on the Approvals tab from B205):
  - Agent selector pulls from /agents on start
  - Grants list polls /agents/{id}/tools/grants per selection
    change, refresh, or Agents-tab activation
  - Per-row Revoke buttons hit DELETE; toast on success
  - Inline form (tool_name + version + trust_tier + reason)
    posts to /agents/{id}/tools/grant; surfaces 400 errors from
    ADR-0060 D5 unknown-tool validation

Files:
  frontend/index.html        — new <div class='panel'> after
                               agents-split with all hooks
  frontend/js/catalog-grants.js (NEW) — module mirroring
                               forged-proposals.js shape
  frontend/js/app.js         — import + start() wiring

Verification:
  - node --check clean on both touched JS files
  - 9 required HTML ids present
  - 52 backend tests pass (catalog_grants + matrix +
    tool_dispatch + plugin_grants regression sweep)

ADR-0060 status post-B223: T1-T6 SHIPPED. Full natural-language
forge-to-dispatch pipeline closes with operator-friendly UI.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: ABI unchanged; pure frontend addition wired
                 to B220 endpoints."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 223 complete ==="
echo "=== ADR-0060 arc CLOSED. T1-T6 all shipped. Forge -> grant -> dispatch is operator-clickable. ==="
echo "Press any key to close."
read -n 1
