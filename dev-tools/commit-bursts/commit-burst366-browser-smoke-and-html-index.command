#!/bin/bash
# Burst 366 - section-14 browser-driven tab smoke + diagnostic-all
# single browsable index.html.
#
# Bundles B366 + B367 into one commit (they share the same operator
# UX surface: section-14 produces the screenshots that index.html
# embeds).
#
# B366 - what section-13 can't catch:
#   Section-13 hits each tab's API endpoints directly from the
#   harness. That catches "endpoint moved/404'd" but NOT:
#     - frontend module raw fetch() bypassing API_BASE (B361:
#       voice + provenance hit port 5173 instead of 7423; section-
#       13 hits 7423 directly so never saw it)
#     - B260/B276/B298 boot-asymmetry: a panel's start() only runs
#       in the trait-tree-failure catch branch, leaving the tab
#       stuck on "Loading..." on the common path
#     - JS exceptions mid-render that leave a tab half-painted
#       with stray "undefined" / "[object Object]" / "Error:" text
#
#   section-14 drives a real Chromium via Playwright:
#     1. opens FRONTEND/?api=DAEMON (default ports 5173 + 7423)
#     2. injects the API token into localStorage
#     3. clicks each of the 15 tabs (data-tab attributes)
#     4. extracts innerText of the now-visible panel
#     5. asserts NO forbidden strings present:
#          "Loading..." / "Loading…" / "loading..." / "Error:" /
#          "undefined" / "[object Object]"
#     6. asserts panel has SOME content (non-empty)
#     7. saves a full-viewport screenshot per tab to
#        data/test-runs/diagnostic-14-browser-smoke/screenshots/
#
#   DOM-text inspection is faster + more accurate than OCR for
#   detecting boot regressions; screenshots remain as visual
#   evidence for the operator. Graceful degradation: if playwright
#   / chromium aren't installed, the section reports SKIPPED
#   instead of FAILing the umbrella (offline operator boxes).
#
# B367 - operator-friendly umbrella output:
#   diagnostic-all.command now writes index.html alongside
#   summary.md. The HTML carries:
#     - tally banner (PASS/FAIL/MISSING counts, color-coded)
#     - section table with per-section status badge + dur + link
#       to that section's report.md
#     - the same consolidated FAIL punch list summary.md carries
#       (operator can read either)
#     - a screenshot gallery embedding the section-14 tab shots
#       inline (grid layout, captions, click-to-zoom via browser)
#     - link back to summary.md as the machine-readable artifact
#
#   Markdown stays canonical for machine readers (the daily
#   scheduled task continues to parse summary.md). The HTML is
#   the visual sibling for the operator's morning eyeballing
#   pass.
#
# Files in this commit:
#   dev-tools/diagnostic/section-14-browser-smoke.command (NEW)
#     ~150 lines bash + embedded Python. Self-installs playwright +
#     chromium on miss (best-effort), reports SKIPPED if install
#     fails. Per-tab allowlist for tabs whose legitimate content
#     might collide with FORBIDDEN strings.
#   dev-tools/diagnostic/diagnostic-all.command (MOD)
#     Adds 14-browser-smoke to SECTIONS array. Emits index.html
#     after summary.md. ~70 line addition.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: B361-class regressions (raw fetch bypass) +
#     B260-class regressions (boot asymmetry) can both ship to
#     main today without the harness noticing. Both have hit
#     production before; B361 was discovered by manual recon, not
#     by the harness.
#   Prove non-load-bearing: pure additive section. Section-13
#     and the other 12 sections unchanged. index.html is
#     additive output; summary.md unchanged for machine readers.
#   Prove alternative is strictly better: leaving in place means
#     the harness has a known blind spot that recurs across every
#     frontend module added going forward.
#
# Verification after this commit lands:
#   1. Ensure frontend + daemon are both running.
#   2. Run dev-tools/diagnostic/section-14-browser-smoke.command
#      alone first - confirms playwright install + per-tab probing.
#      Should produce 15 PASS / 0 FAIL on a green substrate, plus
#      15 PNGs in screenshots/.
#   3. Run diagnostic-all.command - section 14 joins as
#      section #14; index.html lands in the run dir with the
#      screenshot gallery embedded.
#   4. Open data/test-runs/diagnostic-all-<ts>/index.html in a
#      browser - tally, section table, FAIL list, screenshots
#      all present.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/diagnostic/section-14-browser-smoke.command \
        dev-tools/diagnostic/diagnostic-all.command \
        dev-tools/commit-bursts/commit-burst366-browser-smoke-and-html-index.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(diagnostic): section-14 browser smoke + index.html (B366)

Burst 366. Bundles B366 (browser-driven tab smoke) + B367
(diagnostic-all single browsable index.html).

section-14-browser-smoke.command (NEW):
  Playwright drives Chromium against FRONTEND/?api=DAEMON,
  clicks each of the 15 tabs, asserts forbidden strings
  ('Loading...', 'Error:', 'undefined', '[object Object]')
  are absent + panel has content, saves per-tab screenshots.
  Catches B361-class (raw fetch bypassing API_BASE) and
  B260-class (boot asymmetry leaving tabs stuck on
  'Loading...') regressions section-13 can't see by hitting
  the daemon directly. Graceful degradation: SKIPPED if
  playwright/chromium uninstalled instead of failing.

diagnostic-all.command (MOD):
  Adds 14-browser-smoke to SECTIONS. Emits index.html after
  summary.md - tally banner + section table with status
  badges + consolidated FAIL list + screenshot gallery
  embedding section-14 shots + back-link to summary.md.
  Markdown stays canonical for the daily scheduled task;
  HTML is the operator's morning-eyeballing sibling.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: B361 + B260 classes ship to main today without
    harness catch. B361 was discovered by manual recon.
  Prove non-load-bearing: pure additive section + additive
    output file. Other 12 sections + summary.md unchanged.
  Prove alternative is better: leaving in place leaves the
    harness with a known blind spot.

After this lands + frontend + daemon running:
  - section-14 produces 15/15 PASS on green substrate
  - diagnostic-all writes index.html with tab screenshots
  - daily scheduled task continues to parse summary.md
    (machine-readable artifact unchanged)"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 366 complete - browser smoke + index.html ==="
echo "=========================================================="
echo "Re-test:"
echo "  dev-tools/diagnostic/section-14-browser-smoke.command"
echo "Then:"
echo "  dev-tools/diagnostic/diagnostic-all.command"
echo "Then open: data/test-runs/diagnostic-all-<ts>/index.html"
echo ""
echo "Press any key to close."
read -n 1 || true
