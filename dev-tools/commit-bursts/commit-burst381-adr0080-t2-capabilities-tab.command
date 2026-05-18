#!/bin/bash
# Burst 381 - ADR-0080 T2: Agent Capabilities frontend tab.
#
# Lands the operator-facing UI for the B380 capability-tree
# backend. Tab count goes 15 -> 16.
#
# Files in this commit:
#
#   frontend/index.html (MOD)
#     New tab button "Capabilities" in the navbar (after
#     Provenance) + panel section at end of <main>. Panel
#     contains: agent picker, refresh button, summary line,
#     tree container, detail pane.
#
#   frontend/js/capability-tree.js (NEW, ~230 LoC)
#     Subscribes to state.agents for the picker. On agent
#     selection, fetches /agents/{id}/capability-tree (via
#     api.js) and renders three sections: Tools (constitution-
#     bound) / Skills (operator-toggleable) / MCP Plugins.
#     Three visual states per node: live (✓ green), broken
#     (✗ grey), in_progress (⏳ amber). Two binding glyphs:
#     🔒 hard_wired (rebirth required to remove), ☐ operator-
#     toggleable (T3 will add the toggle action). Click any
#     node to populate the detail pane.
#
#   frontend/js/app.js (MOD)
#     Import + start() the new module in both the
#     trait-tree-failure catch branch (degraded mode) AND the
#     happy path (per the B260/B276/B298 boot-asymmetry rule —
#     every panel must start in BOTH branches). Same pattern
#     the existing 15 panels follow.
#
#   dev-tools/diagnostic/section-13-frontend-integration.command (MOD)
#     Extends TAB_ENDPOINTS with the Capabilities tab using
#     the same per-agent template substitution Pending + Memory
#     use. Required (not INFO) since B380's substrate is
#     supposed to be live.
#
#   dev-tools/diagnostic/section-14-browser-smoke.command (MOD)
#     Extends TABS with capabilities entry. Required tier.
#     Picks up automatically on next harness run; no
#     additional CSS/inline-styles needed (the module's nodes
#     carry inline color styling so a missing theme variable
#     doesn't strand visibility).
#
# Composition contract (from B380's backend):
#   - tools list: hard_wired nodes from constitution.tools
#   - skills list: operator_toggleable nodes from skill_catalog
#     with missing_tools highlighted
#   - mcp_plugins: placeholder ([]) until ADR-0043 per-agent
#     grants land
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm of NOT shipping T2: operator still has to read
#     raw YAML + tool registry dump + skill catalog to answer
#     'what can this agent do RIGHT NOW?' B380 made the answer
#     queryable; T2 makes it visible.
#   Prove non-load-bearing:
#     - Pure additive frontend (new tab, new module, no
#       changes to other tabs' logic).
#     - Tab module follows the same lazy-load + state.subscribe
#       pattern the existing 15 panels use; degrades cleanly
#       when /agents/{id}/capability-tree returns 404 or empty.
#     - Section-13 + section-14 extensions surface regressions
#       automatically.
#   Prove alternative is strictly better:
#     - Embedding capability data in the agents.js tab clutters
#       the agent-detail surface and forces every agent-list
#       fetch to compose the tree (expensive).
#     - Leaving operators on raw YAML is what ADR-0080 set out
#       to fix.
#
# CLAUDE.md sec2 + sec3:
#   No dispatcher subsystem changes. No new builtin tool with
#   _VERSION. Pure frontend + harness extension. Not applicable.
#
# Verification after this commit lands:
#   1. Force-restart daemon if not already (no new server code
#      in this commit, but the frontend reloads from the static
#      server; refresh the browser tab).
#   2. Open frontend at ?api=...; click Capabilities tab.
#   3. Pick TelemetryStreward-D3 (or any active agent); see
#      the tree render with tools live + skills classified.
#   4. Run dev-tools/diagnostic/diagnostic-all.command:
#      - section-13 now lists 16 tabs (was 15); Capabilities
#        probes /agents/<sample_id>/capability-tree.
#      - section-14 (browser smoke) now opens 16 tabs and
#        screenshots each. Capabilities should PASS.
#
# What this UNBLOCKS:
#   ADR-0080 T3 (toggle endpoint + audit event). With the UX
#   shape converged, T3 can land the operator-driven on/off
#   action without UI churn.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add frontend/index.html \
        frontend/js/capability-tree.js \
        frontend/js/app.js \
        dev-tools/diagnostic/section-13-frontend-integration.command \
        dev-tools/diagnostic/section-14-browser-smoke.command \
        dev-tools/commit-bursts/commit-burst381-adr0080-t2-capabilities-tab.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(frontend): ADR-0080 T2 Agent Capabilities tab (B381)

Burst 381. New tab + module backed by B380's capability-tree
endpoint. Tab count 15 -> 16.

frontend/index.html:
  Capabilities tab button + panel (agent picker, summary line,
  tree container, detail pane).

frontend/js/capability-tree.js (NEW):
  Subscribes to state.agents for picker. On selection fetches
  /agents/{id}/capability-tree. Renders Tools / Skills /
  MCP Plugins sections. Three states (live / broken /
  in_progress) with inline color styling so a missing theme
  variable doesn't strand visibility. Two binding glyphs:
  hard_wired (rebirth required) / operator_toggleable
  (T3 toggle endpoint pending). Click any node to populate
  the detail pane.

frontend/js/app.js:
  Module wired in both boot branches per the B260/B276/B298
  boot-asymmetry rule — every panel must start in both
  trait-tree-failure (degraded) AND happy path.

dev-tools/diagnostic/section-13 + section-14:
  Extended TAB_ENDPOINTS / TABS to cover the new tab.
  Section-13 uses per-agent template substitution
  (same pattern as Pending + Memory). Section-14 picks
  it up for browser smoke + screenshot.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: operator still reading raw YAML for per-agent
    capability state.
  Prove non-load-bearing: pure additive frontend; module
    follows the lazy-load + state.subscribe pattern the
    existing 15 panels use.
  Prove alternative is better: embedding in agents.js
    clutters the detail surface + forces every agent-list
    fetch to compose the tree.

After this lands:
  - frontend has 16 tabs.
  - diagnostic-all section-13/14 cover all 16.
  - ADR-0080 T3 (toggle endpoint + audit event) becomes the
    next burst when the UX shape is stable."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 381 complete - Agent Capabilities tab ==="
echo "=========================================================="
echo "Try it:"
echo "  Reload the frontend (browser refresh)."
echo "  Click Capabilities tab."
echo "  Pick TelemetryStreward-D3."
echo ""
echo "Press any key to close."
read -n 1 || true
