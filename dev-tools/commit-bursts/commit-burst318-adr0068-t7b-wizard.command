#!/bin/bash
# Burst 318 - ADR-0068 T7b: first-boot wizard pane.
#
# Closes ADR-0068 T7 (T7a substrate + T7b UX). Frontend tab
# "Operator" walks the operator through per-domain connector
# consent. Reads /orchestrator/domains for declared connectors +
# /operator/profile/connectors for current state; POSTs decisions
# through /operator/connectors/{domain}/{connector}.
#
# What ships:
#
# 1. frontend/index.html:
#    - "Operator" tab button (between Orchestrator and Chat).
#    - <section data-panel="operator-wizard"> with status hero
#      + connectors table panel.
#    - DOM ids: op-status, op-connectors, op-refresh-btn.
#
# 2. frontend/js/operator-wizard.js (~220 LoC):
#    - refreshStatus reads /operator/profile/connectors, renders
#      5-cell grid (operator_id + decisions + granted + denied
#      + pending). Same shape as reality-anchor / orchestrator
#      status hero.
#    - refreshConnectors parallel-fetches /orchestrator/domains
#      + /operator/profile/connectors, builds a Map keyed on
#      domain_id:connector_name, then for each domain emits a
#      bordered block listing every declared connector with its
#      current consent chip + Grant / Deny / Pending buttons.
#    - _renderConnectorRow builds one row: name + status chip +
#      decided_at + three action buttons.
#    - _decide POSTs the choice via writeCall (the canonical
#      writes path), toasts the old->new transition, refreshes
#      both sections.
#    - statusChip: granted=green, denied=red, pending=amber.
#    - Lazy-load on first tab activation — same pattern as
#      reality-anchor / security / orchestrator.
#
# 3. frontend/js/app.js:
#    - import operatorWizardPanel.
#    - start() called in BOTH the trait-tree-success branch (with
#      lineage comment for B260/B298 readers) AND the failure
#      catch branch (symmetric fallback).
#
# Verification: node --check passes on both .js files. All 5
# expected ids/attrs present in HTML. Visual smoke deferred to
# host (sandbox lacks a daemon to hit /operator/* against).
#
# === ADR-0068 progresses to 7.75/8 ===
# T7 closed (T7a substrate + T7b UX). Only T8 migration substrate
# remains.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add frontend/index.html \
        frontend/js/operator-wizard.js \
        frontend/js/app.js \
        dev-tools/commit-bursts/commit-burst318-adr0068-t7b-wizard.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(frontend): ADR-0068 T7b - operator wizard pane (B318)

Burst 318. Closes ADR-0068 T7 (T7a substrate B317 + T7b UX this
burst). Frontend Operator tab walks the operator through per-
domain connector consent. Reads /orchestrator/domains for
declared connectors + /operator/profile/connectors for current
state; POSTs decisions through /operator/connectors/{domain}/
{connector}.

What ships:

  - frontend/index.html: Operator tab button (between
    Orchestrator and Chat) + section with status hero +
    connectors table panel. DOM ids op-status, op-connectors,
    op-refresh-btn.

  - frontend/js/operator-wizard.js: refreshStatus 5-cell grid
    (operator_id + decisions + granted + denied + pending).
    refreshConnectors parallel-fetches domains + consent state,
    builds per-domain bordered blocks with connector rows.
    _renderConnectorRow: name + chip + decided_at + three action
    buttons (Grant / Deny / Pending). _decide POSTs choice via
    writeCall, toasts old->new transition, refreshes. Lazy-load
    on first tab activation matching reality-anchor / security /
    orchestrator pattern.

  - frontend/js/app.js: imports operatorWizardPanel, calls
    start() in both success and failure catch branches per the
    B260/B298 lineage pattern.

Verified: node --check passes on both JS files. All 5 expected
ids/attrs present in HTML.

ADR-0068 progress: 7.75/8 (T1-T7 closed, only T8 migration
substrate remaining)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 318 complete - ADR-0068 T7 closed ==="
echo ""
echo "Press any key to close."
read -n 1
