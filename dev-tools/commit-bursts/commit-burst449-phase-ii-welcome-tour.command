#!/usr/bin/env bash
# Burst 449 — Phase II of the external-facing arc: onboarding tour
# update for the vertical sidebar layout.
#
# Adds a new "welcome" tour to frontend/js/tour.js that orients a
# first-visit operator to the four sidebar groups introduced in
# B448 (Build / Run / Observe / Govern), the live status bar, and
# the path to the first concrete task (birth an agent in Forge).
#
# Steps in the welcome tour (7 total):
#   1. Intro — centered, no anchor. "Forest is a local-first agent
#      governance kernel..." Pinpoints what the operator is looking
#      at without diving into specifics.
#   2. BUILD group title — anchor [data-group=build] .tabs__group-title.
#      Explains forge / skills / tools / marketplace as the
#      'forge new things' surface.
#   3. RUN group title — anchor [data-group=run] .tabs__group-title.
#      Explains agents / approvals / chat / voice as the 'interact
#      with live agents' surface.
#   4. OBSERVE group title — anchor [data-group=observe] .tabs__group-title.
#      Audit / memory / provenance / capabilities = 'understand what
#      is happening.'
#   5. GOVERN group title — anchor [data-group=govern] .tabs__group-title.
#      Security / reality / orchestrator / operator = 'safety surfaces.'
#   6. Status bar — anchor #statusbar. Daemon health, agent count,
#      chain head, last-activity. Real-time bottom strip.
#   7. CTA — anchor .tab[data-tab='forge']. 'Click Forge to make your
#      first agent — or use the ? tour button for the Forge-specific
#      walkthrough next.'
#
# Auto-launch on first visit changed from "forge" to "welcome".
# Forge / agents / audit tours stay registered + re-launchable via
# the ? tour button in the top bar (which picks the tour matching
# the currently-active tab).
#
# Verified end-to-end via Chrome MCP against the live daemon:
#   * Cleared fsf:toursSeen + welcomeBanner state via JS.
#   * Reloaded; welcome tour fired automatically after 1.5s delay.
#   * Step 1/7 centered tooltip; clicked next.
#   * Step 2/7 spotlight cut around the BUILD group title in the
#     left sidebar; tooltip rendered the BUILD-group body copy.
#   * skip / back / next buttons visible; engine intact.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: B448's vertical sidebar grouping is invisible to a
#     first-visit operator until they discover it. The pre-B448
#     auto-launch went straight into Forge, skipping the
#     orientation step. New operators bounce or get confused.
#   Prove non-load-bearing for kernel: pure frontend/js/tour.js
#     edit. No schema, no events, no routes, no kernel touch.
#     Pure userspace per ADR-0044 + ADR-0082.
#   Prove alternative: skip the orientation tour (rejected; we just
#     restructured the sidebar in B448 and the welcome tour is the
#     payoff — explaining the structure pays for the restructure);
#     replace forge tour outright (rejected; forge tour still useful
#     for return visits when operator is on the Forge tab).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "==========================================================="
echo "Burst 449 — Phase II welcome tour"
echo "==========================================================="
echo

echo "Pre-commit: clear stale .git locks..."
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null && echo "  cleared" || echo "  none"
echo

git add frontend/js/tour.js
git add dev-tools/commit-bursts/commit-burst449-phase-ii-welcome-tour.command

echo "Staged:"
git diff --cached --stat
echo

git commit -m "feat(frontend): Phase II — welcome tour orients to vertical sidebar (B449)

Adds a new 'welcome' tour to frontend/js/tour.js that walks a
first-visit operator through the four sidebar groups introduced
in B448 (Build / Run / Observe / Govern), the live status bar,
and the path to the first concrete task (birth an agent in Forge).

7 steps:
  1. Centered intro — 'Forest is a local-first agent governance
     kernel; the left sidebar is your home base.'
  2. BUILD group — forge / skills / tools / marketplace.
  3. RUN group — agents / approvals / chat / voice.
  4. OBSERVE group — audit / memory / provenance / capabilities.
  5. GOVERN group — security / reality / orchestrator / operator.
  6. #statusbar — daemon health + agent count + chain head + last-
     activity strip.
  7. .tab[data-tab=forge] — CTA to click Forge or take the
     Forge-specific tour next.

Anchors point at [data-group=...] .tabs__group-title selectors
introduced in B448 so the spotlight cutouts land on the actual
sidebar geography.

Auto-launch behavior changed: was launching the 'forge' tour on
first visit; now launches 'welcome' on first visit. Operator gets
sidebar orientation BEFORE diving into Forge. Pre-B449 visitors
who already hasSeen('forge') will see the welcome tour fire once
(welcome != forge in the seen-set). Forge / agents / audit tours
stay registered + relaunchable via the ? tour button.

Verified end-to-end via Chrome MCP against the live daemon:
  * Cleared fsf:toursSeen via JS; reloaded.
  * Welcome tour fired automatically after 1.5s.
  * Step 1/7 centered tooltip rendered cleanly.
  * Step 2/7 spotlight cut around BUILD group title; tooltip
    rendered the BUILD-group body.
  * skip / back / next buttons all functional.

Phase II of the external-facing arc closed. Phase III (Homebrew
installer formula) is the next deliverable; Phase III.b (signed
.pkg) waits on Apple Developer ID arriving Friday.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: B448's vertical sidebar grouping is invisible to a
    first-visit operator until discovered; pre-B448 auto-launch
    went straight to Forge, skipping orientation.
  Prove non-load-bearing for kernel: pure frontend/js/tour.js
    edit. No schema, no events, no routes.
  Prove alternative: skip orientation tour (rejected; the welcome
    tour is the payoff of restructuring the sidebar); replace
    forge tour outright (rejected; forge tour still useful for
    return visits)." || { echo "commit failed"; exit 1; }

echo
echo "Post-commit signature status:"
git log --format='%h %G? %s' -3
echo

echo "Pushing B449..."
git push origin main || { echo "push failed"; exit 1; }

echo
echo "Done. B449 pushed."
echo
echo "Press any key to close."
read -n 1 || true
