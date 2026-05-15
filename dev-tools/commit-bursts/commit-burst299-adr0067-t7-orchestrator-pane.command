#!/bin/bash
# Burst 299 - ADR-0067 T7: frontend Orchestrator pane.
#
# Closes ADR-0067 (8/8). Phase alpha cross-domain orchestrator
# arc complete. Operator gets a one-tab window into the routing
# substrate: status hero, domain manifest table, last-100
# domain_routed timeline, hot-reload button.
#
# What ships:
#
# 1. frontend/index.html:
#    - "Orchestrator" tab button (between Security and Chat).
#    - <section data-panel="orchestrator"> with three panels:
#        * Status hero (orch-status)
#        * Domains manifest table (orch-domains)
#        * Recent routes timeline (orch-routes)
#      Plus refresh + reload buttons (orch-refresh-btn /
#      orch-reload-btn).
#
# 2. frontend/js/orchestrator.js (~280 LoC):
#    - refreshStatus() -> /orchestrator/status -> renders 6-cell
#      grid (domains / dispatchable / planned / skill mappings /
#      cascade rules / routes_24h) + top-domains line + config
#      errors box. Same shape as reality-anchor's status grid.
#    - refreshDomains() -> /orchestrator/domains -> renders
#      manifest table with status chip per row.
#    - refreshRoutes() -> /orchestrator/recent-routes?limit=100
#      -> renders mono-font timeline (timestamp / target_domain /
#      capability).
#    - reloadConfig() -> POST /orchestrator/reload -> shows toast
#      + re-runs status + domains refresh.
#    - start() wires refresh button, reload button, and a
#      lazy-load handler on the orchestrator tab so the pane
#      stays cheap until first activation.
#
# 3. frontend/js/app.js:
#    - import orchestratorPanel.
#    - call orchestratorPanel.start() in the trait-tree-success
#      branch (with the same comment block as the B260.1/B260.2
#      lineage), AND in the failure catch branch for symmetry.
#
# Verification: node --check passes on both .js files. All 7
# expected ids/attributes present in the HTML. Visual check on
# host: click Orchestrator tab, confirm status grid + domains
# table + routes list render against a running daemon.
#
# No JS test file — frontend doesn't have a JS test harness.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add frontend/index.html \
        frontend/js/orchestrator.js \
        frontend/js/app.js \
        dev-tools/commit-bursts/commit-burst299-adr0067-t7-orchestrator-pane.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(frontend): ADR-0067 T7 - Orchestrator pane (B299)

Burst 299. Closes ADR-0067 (8/8). Phase alpha cross-domain
orchestrator arc complete. Operator gets a one-tab window into
the routing substrate: status hero card, domain manifest table,
last-100 domain_routed timeline, hot-reload button.

What ships:

  - frontend/index.html: Orchestrator tab button (between
    Security and Chat) + <section data-panel='orchestrator'>
    with three panels (status / domains / routes) plus refresh
    and reload buttons.

  - frontend/js/orchestrator.js (~280 LoC): refreshStatus reads
    /orchestrator/status and renders a 6-cell grid (domains /
    dispatchable / planned / skill mappings / cascade rules /
    routes_24h) plus a top-targets line and a config-errors box.
    refreshDomains reads /orchestrator/domains and renders the
    manifest table with status chips. refreshRoutes reads
    /orchestrator/recent-routes?limit=100 and renders a mono-
    font timeline. reloadConfig POSTs /orchestrator/reload and
    toasts. start() wires the refresh + reload buttons and a
    lazy-load handler on first tab activation - same shape as
    reality-anchor.js and security.js.

  - frontend/js/app.js: imports orchestratorPanel; calls
    orchestratorPanel.start() in BOTH the trait-tree-success
    branch (with B260.1/B260.2 lineage comment) AND the
    failure catch branch (symmetric fallback).

Verification: node --check passes on both .js files. All 7
expected ids/attributes present in the HTML.

Phase alpha status: ADR-0050 closed, ADR-0067 closed, ADR-0075
closed. ADR-0068 / 0070 / 0071 / 0072 / 0073 / 0074 / 0076 all
at T1 substrate. The arc shifts from substrate-only to
multi-tranche runner work."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 299 complete - ADR-0067 closed 8/8 ==="
echo ""
echo "Press any key to close."
read -n 1
