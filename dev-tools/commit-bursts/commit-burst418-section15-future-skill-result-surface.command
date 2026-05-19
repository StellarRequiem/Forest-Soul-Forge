#!/bin/bash
# Burst 418 - section-15 surfaces handoff_future in result list + coverage.json.
#
# B415 added the future_skill bucket but didn't add a results.append
# line for it, so the 7 future-skill routes were silently absorbed
# (counted in the FAIL bucket as zero, not visible anywhere in the
# report or coverage.json). Operator couldn't see "7 forward-intent
# declarations still need their skills built."
#
# What this commit adds:
#
# 1. dev-tools/diagnostic/section-15-wiring-cross-check.command
#    - results.append for handoff_future as INFO with first 5
#      shown + "+N more" suffix.
#    - coverage["summary"]["handoffs_future"] count.
#    - coverage["handoffs_future"] list of {domain, capability,
#      reason} for HTML / sentinel drilldown.
#
# Now section-15 report shows:
#   [PASS] handoff routes resolve end-to-end ... all 14 mappings
#          backed by at least one runnable role
#   [INFO] handoff routes declared ahead of skill (7 future_skill)
#          — d2/reminder: schedule_reminder.v1 intentionally ahead
#          of artifact; ... +2 more
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: operator-actionable forward-intent declarations
#     were silently absent from reports.
#   Prove non-load-bearing: ADDITIONS only. Same bucket data, now
#     surfaced.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/diagnostic/section-15-wiring-cross-check.command \
        dev-tools/commit-bursts/commit-burst418-section15-future-skill-result-surface.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(diagnostic): surface section-15 future_skill bucket (B418)

Burst 418. B415 added the handoff_future bucket but didn't add
a results.append for it — the 7 future-skill routes were
silently absorbed (not visible in report.md or coverage.json).

Fix: results.append INFO line + coverage.summary.handoffs_future
count + coverage.handoffs_future list.

Verified report.md output:
  [INFO] handoff routes declared ahead of skill (7 future_skill)
         d2/reminder: schedule_reminder.v1 intentionally ahead of
         artifact; ... +2 more"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 418 complete - future_skill surfaced ==="
echo "=========================================================="
echo "Press any key to close."
read -n 1 || true
