#!/bin/bash
# Burst 256 — ADR-0063 T7: SoulUX Reality Anchor pane.
#
# Closes ADR-0063 entirely. 7/7 tranches shipped across
# B251-B256. The Reality Anchor is now operator-facing at
# every surface: dispatcher (T3), agent (T4), conversation
# (T5), correction memory (T6), and now the SoulUX viewer (T7).
#
# Files:
#
# 1. src/forest_soul_forge/daemon/routers/reality_anchor.py (NEW)
#    Five endpoints under /reality-anchor:
#      GET  /status — combined summary card
#      GET  /ground-truth — full catalog
#      GET  /recent-events — last N reality_anchor_* events
#      GET  /corrections — top repeat offenders
#      POST /reload — hot-reload catalog from disk
#    Read-only by design. Per ADR-0063 D3 the operator owns
#    truth by editing config/ground_truth.yaml directly; the
#    UI is a viewer + reload trigger, not an editor.
#
# 2. src/forest_soul_forge/daemon/app.py
#    Imports + mounts the new router.
#
# 3. frontend/index.html
#    New "Reality" tab in the nav. New tab-panel with four
#    sections: status card, ground-truth table, events
#    timeline, repeat offenders table. Reload + Refresh
#    buttons in the header.
#
# 4. frontend/js/reality-anchor.js (NEW)
#    Module wires the panel. Lazy-loads on first tab click
#    (no fetch cost when the tab is unused). Severity chips,
#    event-type colored tags, repeat-count badges.
#
# 5. frontend/js/app.js
#    Imports + starts the new module.
#
# 6. frontend/css/style.css
#    ~180 lines of new CSS: status-grid, ra-table, severity
#    chips, event-row layout, type-specific colors.
#
# 7. tests/unit/test_daemon_reality_anchor.py (NEW)
#    12 endpoint tests covering every surface:
#      - GET /status returns fact_count + 24h counts + ADR list
#      - catalog_errors always a list
#      - GET /ground-truth returns full fact shape
#      - license fact present in bootstrap catalog
#      - GET /recent-events empty chain → empty list
#      - GET /recent-events filters out non-anchor events
#      - GET /corrections empty table → empty
#      - GET /corrections returns only count >= 2 rows
#      - POST /reload returns post-reload state
#
# 8. docs/decisions/ADR-0063-reality-anchor.md
#    Status: CLOSED 2026-05-12. T7 row marked DONE B256.
#    Total: 7/7 shipped in 6 bursts (B251-B256).
#
# Per ADR-0063 D3: operator owns ground truth via the YAML
#   file. The UI is read-only + reload; no in-UI editing
#   in v1 (deliberate — keeps the safety surface tight).
# Per CLAUDE.md §0 Hippocratic gate: closure tranche adds
#   visibility, not new refuse-paths. The substrate gates
#   (T3 + T5) remain the only refuse points.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/routers/reality_anchor.py \
        src/forest_soul_forge/daemon/app.py \
        frontend/index.html \
        frontend/js/reality-anchor.js \
        frontend/js/app.js \
        frontend/css/style.css \
        tests/unit/test_daemon_reality_anchor.py \
        docs/decisions/ADR-0063-reality-anchor.md \
        dev-tools/commit-bursts/commit-burst256-adr0063-t7-soulux-pane.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(reality-anchor): ADR-0063 CLOSED — SoulUX pane T7 (B256)

Burst 256. Closes ADR-0063 entirely. 7/7 tranches shipped
across B251-B256. The Reality Anchor is now operator-facing
at every surface:
  - T3 dispatcher gate (B252)
  - T4 reality_anchor agent role (B253)
  - T5 conversation pre-turn hook (B254)
  - T6 correction memory + recurrence (B255)
  - T7 SoulUX pane + /reality-anchor/* router (B256)

New /reality-anchor router exposes five operator-facing
endpoints: GET /status (combined summary), /ground-truth
(catalog), /recent-events (last N anchor events filtered
from the chain), /corrections (top repeat offenders), and
POST /reload (hot-reload catalog from disk).

New SoulUX 'Reality' tab with four sections: status card
showing fact count + 24h refused/flagged/repeat counts +
top-repeat-count, ground-truth facts table with severity
chips, recent events timeline with event-type colored tags,
repeat offenders table. Lazy-loads on first tab click.

Read-only by design per ADR-0063 D3 — operator edits
config/ground_truth.yaml on disk and clicks Reload. No
in-UI editing in v1 (deliberate; keeps the safety surface
tight). In-UI editor is a v2 candidate.

Tests: 12 endpoint cases covering every surface.

ADR-0063 status: CLOSED 2026-05-12. The Reality Anchor is
now the operator-facing differentiator the ELv2 business
model needs — 'Forest agents run with a Reality Anchor;
your agent can't silently drift past your facts.'"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 256 complete ==="
echo "=== ADR-0063 CLOSED. Reality Anchor live across all surfaces. ==="
echo "Press any key to close."
read -n 1
