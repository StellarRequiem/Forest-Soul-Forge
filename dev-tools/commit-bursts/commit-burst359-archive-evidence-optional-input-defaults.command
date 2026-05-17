#!/bin/bash
# Burst 359 - archive_evidence.v1: optional-input defaults so the
# prompt resolver doesn't raise on the acquire path.
#
# Bug shape (surfaced by D3 Phase A live verification, captured in
# session memory + the post-B357 north-star "5 known bugs" list):
#   When ForensicArchivist-D3 invokes archive_evidence.v1 with
#   transition_type=acquire (the very first attestation in a chain),
#   the operator does NOT pass handoff_to. The skill engine's
#   compile_arg resolver hits `${inputs.handoff_to}` in the
#   evaluate_transition step's prompt body and raises:
#       arg resolution failed: key 'handoff_to' missing on dict
#   Same shape applies to expected_prior_hash on any path where the
#   operator omits it. Both are documented as optional in the
#   skill's `inputs` schema (not in `required:`) so the failure is
#   schema-vs-resolver contract drift, not operator error.
#
# Fix (minimal, no semantic change):
#   Add `default: ""` to both optional input properties. The
#   resolver substitutes "" when the operator omits the key; the
#   evaluate_transition prompt's rule-6 already treats empty
#   handoff_to as a HALT condition (missing_handoff_target) when
#   transition_type=handoff, so the empty-string default doesn't
#   weaken the governance check - it just keeps the resolver from
#   pre-empting the rule with a hard exception.
#
# Hippocratic gate (CLAUDE.md §0):
#   Prove harm: live-test-d3-phase-a.command acquire transition
#     raises ToolValidationError on the first archive_evidence call.
#     Concrete user-visible failure.
#   Prove non-load-bearing: removing the input would break the
#     handoff path (intentional). The fix is additive (a default),
#     not a removal. Schema reading agents see the same required
#     vs. optional contract.
#   Prove the alternative is strictly better than leaving in place:
#     leaving in place = skill unusable on acquire = D3 Phase A
#     dead in the water for first-acquire of any artifact.
#   All three pass -> apply the additive default.
#
# Verification after this commit lands:
#   1. Restart daemon (force-restart-daemon.command).
#   2. Re-run live-test-d3-phase-a.command - acquire transition
#      should now produce an ATTEST verdict block, not raise.
#   3. diagnostic-all.command section 07 (skill-smoke) stays PASS;
#      section 02 (skill-manifests) unaffected by this schema add.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add examples/skills/archive_evidence.v1.yaml \
        dev-tools/commit-bursts/commit-burst359-archive-evidence-optional-input-defaults.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(skills): archive_evidence optional-input defaults (B359)

Burst 359. Fix open bug #1 from the north-star 5-known-bugs list.

archive_evidence.v1 raises 'arg resolution failed: key
handoff_to missing on dict' on the acquire path because the
evaluate_transition prompt references \${inputs.handoff_to}
unconditionally. handoff_to is optional in the inputs schema
(not in required:), so its absence on the acquire path is by
design. The resolver pre-empted the rule-6 HALT that already
handles the missing-target case at the governance layer.

Fix: add default: \"\" to handoff_to and expected_prior_hash.
Resolver substitutes \"\" when absent; rule-6 still HALTs on
transition_type=handoff with empty handoff_to.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: live-test-d3-phase-a.command acquire path raises.
  Prove non-load-bearing: additive default, no removal.
  Prove alternative is better: leaving in place blocks D3 Phase A
    first-acquire entirely.

After this lands:
  - Re-run live-test-d3-phase-a.command -> acquire ATTEST verdict.
  - Section 07 stays PASS; section 02 unaffected."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 359 complete - archive_evidence acquire path ==="
echo "=========================================================="
echo "Re-test: dev-tools/live-test-d3-phase-a.command"
echo "Expected: acquire transition produces ATTEST verdict."
echo ""
echo "Press any key to close."
read -n 1 || true
