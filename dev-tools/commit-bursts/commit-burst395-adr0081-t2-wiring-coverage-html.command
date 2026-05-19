#!/bin/bash
# Burst 395 - ADR-0081 T2: wiring-coverage.html generator.
#
# Second implementation tranche. Section-15 (T1, B394) emits the
# structured coverage.json; T2 renders it into a rich, self-
# contained HTML page the operator can open in any browser to
# answer "is everything wired?" at a glance with drilldown into
# the gaps.
#
# What this commit adds:
#
# 1. dev-tools/diagnostic/render-wiring-coverage.py (NEW)
#    Standalone Python script. Reads coverage.json (from section-
#    15), writes wiring-coverage.html with:
#      - Top-level verdict chip (ALL WIRED / GAPS DETECTED).
#      - Status chips: cataloged tools, orphans, kit-only, skills,
#        unresolvable, no-carrier, handoffs total + broken.
#      - Jump-nav to each section.
#      - Orphan tools table (cataloged, zero carriers).
#      - Kit-only tools table (in archetype kits but no alive
#        agent yet — normal during rollouts).
#      - Skill wiring issues (unresolvable requires + no carrier).
#      - Handoff routes (broken end-to-end with reason).
#      - Per-tool carrier matrix with drilldown (every cataloged
#        tool with archetype + alive-agent carrier counts; red
#        rows = orphan, orange = kit-only, white = healthy).
#    Inline CSS, no JS dependencies, no external fetches. Operator
#    opens in any browser without a server.
#
# 2. dev-tools/diagnostic/diagnostic-all.command
#    After all 15 sections finish, the umbrella now:
#      (a) Invokes render-wiring-coverage.py against section-15's
#          coverage.json if present.
#      (b) Writes wiring-coverage.html into the umbrella run dir
#          alongside summary.md + index.html.
#      (c) Links to it from index.html's "Source artifacts" list
#          when the render succeeded.
#    Best-effort: if section-15 crashed or render fails, the
#    umbrella logs a warn and continues (no hard fail).
#
# Initial verify (with current section-15 output):
#   Generated wiring-coverage.html (18KB) shows:
#     - GAPS DETECTED verdict
#     - 67 cataloged tools, 6 orphans (red), 6 kit-only (orange)
#     - 43 skills all wired
#     - 14 handoffs, 7 broken (3 d2 + 2 d3 + 2 d4 + 1 d8)
#     - Per-tool matrix highlights orphans + kit-only rows in
#       distinct colors.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: section-15 (T1) emits structured JSON + a markdown
#     report. JSON is machine-readable but not operator-friendly
#     for the "click around the wiring" UX Alex asked for. Markdown
#     report is human-readable but flat — no drilldowns, no chips,
#     no jump nav. The HTML closes that gap.
#   Prove non-load-bearing: ADDITION only. New Python script, new
#     umbrella post-process step (best-effort; missing coverage
#     logs warn not fail). No behavior changes to existing sections
#     or to umbrella's failure semantics.
#   Prove alternative is strictly better:
#     (a) "operator reads markdown" - what we have today; doesn't
#         scale past 5-10 gaps and obscures the cross-tool drilldown.
#     (b) "ship a SPA" - over-engineered for a static report;
#         introduces JS framework dep + dev-loop overhead.
#     (c) "ship as React component in main frontend" - couples
#         operator-facing diagnostic UX to daemon liveness; the
#         section-15 + wiring-coverage.html pair is intentionally
#         daemon-independent. Future T3-T6 wires sentinel into the
#         daemon; the HTML stays static.
#
# Verification after this commit lands:
#   1. bash dev-tools/diagnostic/section-15-wiring-cross-check.command
#      (regenerates coverage.json).
#   2. python3 dev-tools/diagnostic/render-wiring-coverage.py \
#        data/test-runs/diagnostic-15-wiring-cross-check/coverage.json \
#        /tmp/test-coverage.html
#      Expected: "wrote wiring-coverage.html: /tmp/test-coverage.html"
#   3. bash dev-tools/diagnostic/diagnostic-all.command
#      Expected: umbrella runs all 15 sections + generates
#      wiring-coverage.html in the run dir + links to it from
#      index.html.
#   4. Open the generated wiring-coverage.html in a browser.
#      Expected: verdict chip, status chips, jump nav, orphan
#      table, kit-only table, skill block, handoff block,
#      per-tool matrix with color-coded rows.
#
# What this UNBLOCKS / queues next:
#   T3: wiring_sentinel role (guardian-genre singleton).
#   T4: wiring_audit.v1 skill (consumes coverage.json + delegates
#       medium+ gaps to operator queue).
#   T5: scheduled task forest-soul-forge-wiring-audit + runbook.
#   T6: CLOSE - live verify + north-star update + Accepted.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/diagnostic/render-wiring-coverage.py \
        dev-tools/diagnostic/diagnostic-all.command \
        dev-tools/commit-bursts/commit-burst395-adr0081-t2-wiring-coverage-html.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(diagnostic): wiring-coverage.html generator (ADR-0081 T2, B395)

Burst 395. Second implementation tranche of ADR-0081. Section-15
(T1, B394) emits coverage.json; T2 renders it into a rich self-
contained HTML page operators open in any browser to answer 'is
everything wired?' at a glance with per-tool drilldown.

render-wiring-coverage.py (NEW):
  Standalone Python script, no external deps. Reads coverage.json,
  emits wiring-coverage.html with:
    - Top verdict chip (ALL WIRED / GAPS DETECTED).
    - Status chips: tools, orphans, kit-only, skills,
      unresolvable, no-carrier, handoffs total + broken.
    - Jump-nav to each section.
    - Orphan tools table.
    - Kit-only tools table (in kit, no alive agent yet).
    - Skill wiring issues.
    - Handoff routes broken end-to-end.
    - Per-tool carrier matrix with color-coded rows.
  Inline CSS, no JS, no external fetches. Operator opens in any
  browser without a server.

diagnostic-all.command:
  After all 15 sections finish, the umbrella now invokes
  render-wiring-coverage.py against section-15's coverage.json if
  present, writes wiring-coverage.html into the run dir, and
  links to it from index.html's Source artifacts. Best-effort;
  missing coverage logs warn not fail.

Initial verify (current substrate):
  GAPS DETECTED verdict.
  67 tools (6 orphan red + 6 kit-only orange + 55 healthy).
  43 skills all wired.
  14 handoffs (7 broken across d2/d3/d4/d8).

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: section-15's JSON + markdown report don't scale
    past 5-10 gaps and lack drilldown UX Alex asked for.
  Prove non-load-bearing: ADDITION only. New script + post-
    process step. Missing coverage logs warn not fail; no
    behavior change to existing sections.
  Prove alternative is better: SPA over-engineered for static
    report; main-frontend coupling would tie operator UX to
    daemon liveness; section-15 + HTML pair is daemon-independent.

T3-T6 queued: wiring_sentinel role -> wiring_audit.v1 skill ->
scheduled task + runbook -> CLOSE."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 395 complete - ADR-0081 T2 shipped ==="
echo "=========================================================="
echo "Verify:"
echo "  bash dev-tools/diagnostic/diagnostic-all.command"
echo "  open data/test-runs/diagnostic-all-<ts>/wiring-coverage.html"
echo ""
echo "Next: T3 (wiring_sentinel role)."
echo ""
echo "Press any key to close."
read -n 1 || true
