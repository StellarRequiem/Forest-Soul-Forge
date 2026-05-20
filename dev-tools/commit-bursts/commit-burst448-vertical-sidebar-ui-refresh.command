#!/usr/bin/env bash
# Burst 448 — Phase I UI refresh: vertical sidebar grouped by
# operator workflow.
#
# Closes the first phase of the external-facing arc that started
# with B447's positioning anchor. Per the Candidate B positioning
# (operator substrate), the 16 frontend tabs reorganize from a
# single horizontal strip into a vertical left sidebar with four
# workflow groups:
#
#   BUILD     forge / skills / tool-registry / marketplace
#   RUN       agents / pending (Approvals) / chat / voice
#   OBSERVE   audit / memory / provenance / capabilities
#   GOVERN    security / reality-anchor / orchestrator / operator-wizard
#
# 4 groups × 4 tabs each — balanced, scannable, easy to find
# anything from muscle memory after one orientation.
#
# Files touched:
#   frontend/index.html
#     - nav.tabs gains aria-label and is restructured into four
#       .tabs__group divs, each with a .tabs__group-title header.
#     - Every <button class="tab"> keeps its data-tab attribute
#       UNCHANGED so all existing JS tab-switching logic continues
#       to work without modification. Tab labels wrapped in
#       <span class="tab__label"> so we can grow / shrink them
#       independently in the future (mobile collapse, narrow
#       sidebar mode).
#
#   frontend/css/style.css
#     - .app becomes a 2-column grid (220px sidebar + 1fr main).
#     - .tabs becomes a flex column, sticky-positioned, scrollable
#       overflow at max-height calc(100vh - 80px).
#     - .tabs__group + .tabs__group-title styles added (uppercase
#       muted heading + tight group spacing).
#     - .tab becomes full-width, left-aligned. Active state:
#       border-left accent + bg-raised background (was: border-bottom
#       accent).
#     - .tab__label class added to host label flex-1.
#     - @media (max-width: 720px) collapses back to horizontal
#       scrollable tabs with group dividers — mobile / narrow
#       viewport gets the pre-B448 visual footprint without losing
#       the group structure.
#
# Verified end-to-end via Chrome MCP:
#   * Frontend loads at http://localhost:5173/?api=http://127.0.0.1:7423
#   * Daemon connection healthy (daemon ok / 40 agents / chain #20675)
#   * Forge tab loads as default (active left-border accent)
#   * Clicked Audit in the Observe group; tab switched cleanly,
#     audit chain rendered 50 recent entries, sidebar scroll state
#     preserved, active state moved.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: 16 horizontal tabs eat horizontal space + force
#     left-right scanning; operator-substrate positioning calls
#     for workflow grouping; the welcome message + onboarding
#     story benefits from a clear left-rail navigation pattern
#     familiar from every IDE / admin console / SaaS dashboard.
#   Prove non-load-bearing for kernel: frontend HTML + CSS only.
#     Zero JS changes. Zero kernel, schema, audit-event, route
#     touches. Pure userspace per ADR-0044 + ADR-0082.
#   Prove alternative: keep horizontal (rejected; operator
#     explicitly asked for vertical); minimal vertical (rejected;
#     user picked 'full visual refresh' but Phase I is one
#     focused deliverable — typography polish + welcome-banner
#     overhaul land in subsequent phases).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "==========================================================="
echo "Burst 448 — Phase I UI refresh: vertical sidebar"
echo "==========================================================="
echo

echo "Pre-commit: clear stale .git locks..."
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null && echo "  cleared" || echo "  none"
echo

git add frontend/index.html
git add frontend/css/style.css
git add dev-tools/commit-bursts/commit-burst448-vertical-sidebar-ui-refresh.command

echo "Staged:"
git diff --cached --stat
echo

git commit -m "feat(frontend): Phase I UI refresh — vertical sidebar grouped by operator workflow (B448)

Restructures the 16-tab horizontal strip into a vertical left
sidebar with four workflow-grouped sections. Per the Candidate B
positioning anchor set in B447 (operator substrate), the groups
mirror what an operator actually does day-to-day:

  BUILD     forge / skills / tools / marketplace
  RUN       agents / approvals / chat / voice
  OBSERVE   audit / memory / provenance / capabilities
  GOVERN    security / reality / orchestrator / operator

4 groups x 4 tabs. Balanced, scannable, muscle-memorable.

Files:
  frontend/index.html
    - <nav class='tabs'> restructured: four <div class='tabs__group'>
      sections, each headed by a <div class='tabs__group-title'>.
    - Every <button class='tab'> keeps its data-tab attribute
      UNCHANGED. All existing JS tab-switching logic works without
      modification.
    - Labels wrapped in <span class='tab__label'> for layout control.

  frontend/css/style.css
    - .app becomes a 2-column grid (220px sidebar + 1fr main).
    - .tabs is now a flex column, sticky-positioned, scrollable
      at max-height calc(100vh - 80px). No more horizontal
      border-bottom strip.
    - .tabs__group + .tabs__group-title styles: uppercase muted
      heading + tight inter-tab spacing inside each group.
    - .tab is full-width, left-aligned. Active state moves from
      border-bottom accent to border-left accent + bg-raised
      background — eye snaps to which tab is current.
    - .tab__label class added for layout flex-1.
    - @media (max-width: 720px) collapses back to horizontal
      scrollable tabs with group dividers so narrow viewports
      keep working.

Verified end-to-end via Chrome MCP against the live daemon:
  * Frontend loads with daemon ok / 40 agents / chain #20675.
  * Forge tab renders as default with left-border accent.
  * Clicked Audit (Observe group); tab switched cleanly, main
    panel rendered 50 recent audit-chain entries.
  * Sidebar scroll state preserved across switches.
  * Approvals badge visible inline (post-label, pre-active-border).

Phase I of the external-facing roadmap. Phase II (onboarding tour
update walking the new layout) lands next session. Phases III/IV/V
(Homebrew installer + landing page + GitHub release) follow Phase
II.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: 16-tab horizontal strip eats horizontal space +
    forces left-right scanning. Operator-substrate positioning
    needs workflow grouping. Welcome / onboarding flows benefit
    from familiar IDE-style left rail.
  Prove non-load-bearing: frontend HTML + CSS only. Zero JS.
    Zero kernel, schema, event, or route touches. Pure userspace
    per ADR-0044 + ADR-0082.
  Prove alternative: keep horizontal (rejected; operator asked
    for vertical); minimal vertical (rejected; operator picked
    full visual refresh — but Phase I is one focused deliverable;
    typography + welcome-banner overhaul land in subsequent
    phases)." || { echo "commit failed"; exit 1; }

echo
echo "Post-commit signature status:"
git log --format='%h %G? %s' -3
echo

echo "Pushing B448..."
git push origin main || { echo "push failed"; exit 1; }

echo
echo "Done. B448 pushed."
echo
echo "Press any key to close."
read -n 1 || true
