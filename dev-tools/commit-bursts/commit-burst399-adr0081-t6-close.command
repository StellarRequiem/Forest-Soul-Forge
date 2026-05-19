#!/bin/bash
# Burst 399 - ADR-0081 T6: CLOSE.
#
# Final tranche. Marks ADR-0081 Accepted and records the 6-burst
# arc B393-B399 as the structural answer to the B363/B392 gap
# class.
#
# What this commit does:
#
# 1. docs/decisions/ADR-0081-substrate-wiring-coverage.md —
#    Status field updated from "Proposed" to "Accepted" with
#    the date showing T1-T5 ship dates + B399 close.
#
# What this commit DOES NOT do:
#   - It does NOT auto-install the launchd plist. Operator does
#     that explicitly per docs/runbooks/wiring-audit.md (one cp
#     + one launchctl bootstrap). The plist sits in
#     dev-tools/launchd/ as a template; opting in is a
#     deliberate operator action.
#   - It does NOT auto-birth the WiringSentinel. Operator runs
#     dev-tools/birth-wiring-sentinel.command after force-restart-
#     daemon picks up the new archetype + template.
#   - It does NOT fix the 6 orphan tools or 7 broken handoffs
#     that section-15's initial dry-run surfaced. Those are
#     operator-queue items per ADR-0081 D5 (sentinel finds;
#     operator owns substrate mutation).
#
# The 6-burst arc, summarized:
#   B393 (Proposed): ADR-0081 doc-only proposal.
#   B394 T1: section-15-wiring-cross-check harness + diagnostic-
#            all SECTIONS array entry. Initial dry-run: 2/6 pass
#            (2 FAIL + 2 INFO) surfacing 6 orphan tools + 7
#            broken handoffs.
#   B395 T2: render-wiring-coverage.py generator + umbrella
#            integration. Self-contained HTML with verdict chip,
#            status chips, jump nav, orphan/skill/handoff tables,
#            per-tool carrier matrix.
#   B396 T3: wiring_sentinel role substrate (archetype kit +
#            trait profile + constitution template + singleton-
#            roles set + birth driver).
#   B397 T4: wiring_audit.v1 signature skill (4-step:
#            verify_chain -> recall_prior -> summarize -> record).
#   B398 T5: scheduled cadence (run-wiring-audit.command wrapper
#            + launchd plist template) + operator runbook.
#   B399 T6 (this): ADR-0081 status -> Accepted.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: leaving ADR-0081 at Proposed when T1-T5 are all
#     shipped + the runbook is in place creates documentation
#     drift. Future operators reading docs/decisions/ would see
#     "Proposed" and not realize the substrate has been built
#     out. Accepted is the truthful state.
#   Prove non-load-bearing: status text edit only.
#   Prove alternative is strictly better: leaving it Proposed is
#     wrong; jumping straight to Accepted at B393 would have
#     violated the doc-only-first cadence ADR-0079/0080 set.
#     B399 closes the arc cleanly.
#
# Verification after this commit lands:
#   1. grep "^**Status:**" docs/decisions/ADR-0081-substrate-wiring-coverage.md
#      Expected: "**Status:** Accepted"
#   2. End-to-end live verify (operator-driven):
#      a. force-restart-daemon
#      b. bash dev-tools/birth-wiring-sentinel.command
#         Expected: WiringSentinel born, constitution parses,
#         posture green.
#      c. bash dev-tools/run-wiring-audit.command
#         Expected: section-15 runs, sentinel ID resolved, skill
#         dispatched with ok=true, lineage memory gains one
#         wiring_audit_outcome entry.
#      d. (Optional) Install the launchd plist per the runbook.
#      e. bash dev-tools/diagnostic/diagnostic-all.command
#         Expected: section-15 in the table + wiring-coverage.html
#         in the run dir + linked from index.html.
#
# North-star update (out-of-band — Alex updates the auto-memory
# project_north_star_current_state.md):
#   - ADR-0081 6/6 closed (B393-B399 arc).
#   - WiringSentinel added to the singleton-per-forest list
#     (joins reality_anchor + domain_orchestrator).
#   - Open gap (operator queue): 6 orphan tools + 7 broken
#     handoff routes surfaced by section-15's initial dry-run.
#     Per-gap remediation is separate operator-authored bursts.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0081-substrate-wiring-coverage.md \
        dev-tools/commit-bursts/commit-burst399-adr0081-t6-close.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(adr): ADR-0081 CLOSE — substrate wiring coverage Accepted (T6, B399)

Burst 399. Final tranche. ADR-0081 Status flips Proposed ->
Accepted. T1-T5 shipped B394-B398; this closes the 6-burst arc
B393-B399.

The arc, summarized:
  B393 Proposed: doc-only ADR.
  B394 T1: section-15-wiring-cross-check + diagnostic-all wire.
    Initial dry-run: 6 orphan tools + 7 broken handoffs surfaced.
  B395 T2: render-wiring-coverage.py + umbrella integration.
  B396 T3: wiring_sentinel role substrate (archetype + trait +
    template + singleton-roles + birth driver).
  B397 T4: wiring_audit.v1 4-step signature skill.
  B398 T5: launchd cadence (4h) + run-wiring-audit.command
    wrapper + operator runbook.
  B399 T6 (this): ADR-0081 -> Accepted.

This commit does NOT:
  - auto-install the launchd plist (operator step per runbook)
  - auto-birth WiringSentinel (operator step after restart)
  - fix the 6 orphan tools / 7 broken handoffs surfaced by
    section-15 (per D5: sentinel finds; operator mutates)

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: T1-T5 shipped but ADR still reads Proposed creates
    documentation drift. Future operators wouldn't see the arc
    is done.
  Prove non-load-bearing: status text edit only.
  Prove alternative: Accepted is the truthful state once T1-T5
    are in.

Operator next steps (out-of-band):
  1. force-restart-daemon (picks up archetype + template).
  2. dev-tools/birth-wiring-sentinel.command.
  3. dev-tools/run-wiring-audit.command (manual first run).
  4. Install the launchd plist per docs/runbooks/wiring-audit.md.
  5. Queue per-gap remediation for the 6 orphan tools + 7 broken
     handoffs surfaced by section-15's initial dry-run."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 399 complete - ADR-0081 6/6 CLOSED ==="
echo "=========================================================="
echo ""
echo "The 6-burst arc B393-B399 is in. Operator next steps:"
echo "  1. force-restart-daemon"
echo "  2. dev-tools/birth-wiring-sentinel.command"
echo "  3. dev-tools/run-wiring-audit.command (manual first run)"
echo "  4. Install the launchd plist per docs/runbooks/wiring-audit.md"
echo "  5. Queue per-gap remediation for the orphans + broken handoffs"
echo ""
echo "Press any key to close."
read -n 1 || true
