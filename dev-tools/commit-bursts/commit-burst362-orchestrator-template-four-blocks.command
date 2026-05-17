#!/bin/bash
# Burst 362 - domain_orchestrator constitution template: add the
# four common-template blocks the harness's section-01 static-config
# check requires.
#
# Bug shape (surfaced by diagnostic-all on 2026-05-17 against
# HEAD 45786f4):
#   section-01-static-config FAIL:
#     "every template has required blocks - domain_orchestrator:
#      missing ['risk_thresholds', 'out_of_scope', 'operator_duties',
#      'drift_monitoring']"
#   The domain_orchestrator entry was added in ADR-0067 with policies,
#   allowed_tools, confidence_floor, and reality_anchor blocks - but
#   it pre-dated the schema convention every other template uses
#   (the four blocks were standardized as part of the ADR-0044
#   role-expansion arc, Burst 124). The orchestrator wasn't
#   re-conformed when the schema convention solidified.
#
# Fix (additive, conservative):
#   Append the four blocks to the domain_orchestrator entry with
#   values that match its actual posture:
#     risk_thresholds: high min_confidence (matches confidence_floor
#       so ambiguous decompositions surface, not auto-route)
#     out_of_scope: the capability list the existing
#       forbid_direct_action and forbid_self_delegate policies
#       already enforce - listing them in out_of_scope gives the
#       operator a single place to see what the orchestrator MUST
#       NOT do
#     operator_duties: three concrete duties drawn from ADR-0067's
#       D2/D5/D7 operator-facing checkpoints
#     drift_monitoring: per_turn check with zero deviation tolerance
#       and halt-on-drift - the orchestrator is a singleton bound
#       to its constitution hash, any drift IS a corruption signal
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: section-01 reports FAIL today. The harness's
#     baseline-drift detector flags this on every daily run until
#     fixed.
#   Prove non-load-bearing: additive only; existing policies +
#     allowed_tools + confidence_floor + reality_anchor are
#     unchanged. The four new blocks just make the orchestrator's
#     posture self-describing.
#   Prove the alternative is strictly better: leaving in place
#     means section-01 stays red, hiding any FUTURE template-block
#     regressions behind a known-issue line item.
#
# Verification after this commit lands:
#   1. Restart daemon (force-restart-daemon.command) - constitution
#      template is loaded at boot.
#   2. Run section-01-static-config.command - the orchestrator
#      template check flips from FAIL to PASS.
#   3. Re-run diagnostic-all.command - section 01 PASS count goes
#      from 0 to 1 of its 2 checks; the d10_research_lab FAIL
#      remains (B365 documents this as expected until D10 rollout).

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add config/constitution_templates.yaml \
        dev-tools/commit-bursts/commit-burst362-orchestrator-template-four-blocks.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(config): orchestrator template four blocks (B362)

Burst 362. Fix open bug #3 from the harness baseline punch list.

domain_orchestrator constitution template pre-dated the four-
block schema convention standardized in ADR-0044 Burst 124.
Section-01-static-config reports FAIL: 'missing [risk_thresholds,
out_of_scope, operator_duties, drift_monitoring]'.

Fix: append the four blocks with values matching the orchestrator's
actual posture (router-only, singleton-bound, conservative).
  risk_thresholds: min_confidence=0.6 matches confidence_floor so
    ambiguous decompositions surface to operator, not auto-route.
  out_of_scope: the capability list forbid_direct_action and
    forbid_self_delegate already enforce - now self-describing.
  operator_duties: review ambiguous decompositions weekly; audit
    one-step cascade discipline; verify constitution_hash per
    restart.
  drift_monitoring: per_turn, zero deviation, halt-on-drift -
    matches singleton identity binding.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: section-01 FAIL today, surfaces daily.
  Prove non-load-bearing: additive; existing blocks unchanged.
  Prove alternative is better: leaving red hides future
    template-block regressions behind a known-issue.

After this lands:
  - section-01 orchestrator check flips PASS.
  - diagnostic-all section 01 remaining FAIL is the d10
    unlanded-roles entry (B365 documents as expected pre-D10)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 362 complete - orchestrator template ==="
echo "=========================================================="
echo "Re-test: dev-tools/diagnostic/section-01-static-config.command"
echo "Expected: orchestrator template check PASSes."
echo ""
echo "Press any key to close."
read -n 1 || true
