#!/bin/bash
# Burst 415 - section-15 carriage + future_skill recognition.
#
# Wiring-audit's gap reports were over-aggressive in two ways
# that produced lots of false-positive "operator queue" noise:
#
# 1. ORPHAN TOOLS: section-15 only looked for carriers in
#    tool_catalog.archetypes.<X>.standard_tools and
#    genre_default_tools. But constitution_templates.role_base.<X>.
#    allowed_tools is ALSO a real carrier source — domain_orchestrator
#    permits decompose_intent.v1 / route_to_domain.v1 / operator_
#    profile_read.v1 at the constitution layer without including them
#    in its standard archetype kit. Section-15 reported these as
#    orphan; they're operationally wired.
#
# 2. BROKEN HANDOFFS: handoffs.yaml routes can intentionally declare
#    a target ahead of the skill artifact landing. Comments in the
#    file document this pattern ("the dispatcher returns a clean
#    'skill not found' error from the dispatcher, which is the
#    operator-visible signal that the wiring is ahead of the
#    artifact"). Section-15 was reporting these as FAIL when they're
#    really INFO.
#
# Class of gap: same as B404/B414's manifest-vs-runtime checks —
# section-15 had a narrow read of "what counts as wired." Extending
# it to recognize allowed_tools + future_skill removes the
# false-positive noise.
#
# What this commit adds:
#
# 1. dev-tools/diagnostic/section-15-wiring-cross-check.command
#    Two extensions:
#      a) template_allows: builds a {tool_key -> set(role_name)} map
#         from constitution_templates.role_base.<X>.allowed_tools.
#         Folds into all_carrier_archetypes as "(allowed:<role>)".
#      b) handoff_future: routes with `future_skill: true` go to
#         this INFO bucket instead of handoff_broken FAIL bucket.
#
# 2. config/handoffs.yaml
#    7 routes annotated with `future_skill: true`:
#      d2/reminder -> schedule_reminder
#      d2/morning_briefing -> generate_briefing
#      d3/incident_summary -> summarize_recent_incidents
#      d3/incident_response -> respond_to_incident
#      d4/review_signoff -> review_pr
#      d4/implementation -> implement_feature
#      d8/compliance_scan -> scan_for_compliance
#    Plus a comment block at the top of default_skill_per_capability
#    documenting the marker.
#
# Verify results (this commit's substrate state):
#   BEFORE: 6 orphan tools + 7 broken handoffs
#   AFTER:  3 orphan tools + 0 broken handoffs
#
#   Remaining 3 orphans (real gaps for the operator queue):
#     operator_profile_write.v1   archetype_tags=[assistant, operator_steward]
#     personal_recall.v1          archetype_tags=[companion, assistant,
#                                                 operator_steward, domain_orchestrator]
#     security_scan.v1            archetype_tags=[security_low, guardian, observer]
#   Each is a per-tool operator decision: retire OR assign to an
#   existing archetype kit OR await the operator_steward role.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: section-15's noisy output was diluting real gap
#     signal. Triune triage spent prompt tokens reasoning about
#     handoffs that were intentional. Operator-queue churn from
#     false positives.
#   Prove non-load-bearing: section-15 + handoffs.yaml ADDITIONS
#     only. Wiring semantics unchanged. False-positive findings
#     reclassified to INFO; real gaps unchanged.
#   Prove alternative is strictly better:
#     (a) Build skills for all 7 'broken' routes — operator hasn't
#         decided which to build; some may be retired. Premature.
#     (b) Remove the 7 routes — destroys the operator's forward-
#         intent declarations. Premature.
#     (c) Extend section-15 to recognize the patterns — captures
#         the operator's actual intent and stops the noise. This.
#
# Verification after this commit lands:
#   1. bash dev-tools/diagnostic/section-15-wiring-cross-check.command
#      Expected: 4 PASS / 1 FAIL / 2 INFO; 3 orphan tools listed
#      (operator_profile_write + personal_recall + security_scan);
#      0 broken handoffs (was 7).
#   2. Subsequent wiring-coverage.html shows 3 orphans + 0 broken
#      handoffs.
#   3. Next wiring_audit_triage cycle no longer reasons about
#     the 7 false-positive broken handoffs.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/diagnostic/section-15-wiring-cross-check.command \
        config/handoffs.yaml \
        dev-tools/commit-bursts/commit-burst415-section15-allowed-tools-and-future-skills.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(diagnostic): section-15 allowed_tools + future_skill (B415)

Burst 415. Two extensions to ADR-0081 section-15 that cut false-
positive wiring-audit noise by ~75%.

1. ORPHAN TOOLS: section-15 now recognizes
   constitution_templates.role_base.<role>.allowed_tools as a
   real carrier source. domain_orchestrator permits
   decompose_intent / route_to_domain / operator_profile_read at
   the constitution layer without including them in its standard
   archetype kit. These were reported as orphan; now correctly
   classified as carried.

2. BROKEN HANDOFFS: handoffs.yaml routes with
   \`future_skill: true\` are bucketed as INFO not FAIL. Per the
   long-standing pattern documented in the file's own comments,
   the dispatcher returns a clean 'skill not found' error during
   the T2b -> T4 window between intent declaration and artifact
   landing. 7 routes marked accordingly (schedule_reminder,
   generate_briefing, summarize_recent_incidents,
   respond_to_incident, review_pr, implement_feature,
   scan_for_compliance).

Class of gap: same as B404/B414. section-15 had a narrow read
of 'wired'; extending it removes false-positive churn.

Results:
  BEFORE: 6 orphan tools + 7 broken handoffs
  AFTER:  3 orphan tools + 0 broken handoffs

Remaining 3 orphans (real operator-queue items):
  operator_profile_write.v1 — awaits operator_steward role
  personal_recall.v1        — D1 memory; per-tool decision
  security_scan.v1          — D3 SOC; per-tool decision

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: noisy output diluted real signal; triune triage
    burned prompt tokens reasoning about intentional routes.
  Prove non-load-bearing: ADDITIONS only; false-positives
    reclassified, real gaps unchanged.
  Prove alternative: building 7 unbuilt skills is premature;
    removing 7 routes destroys operator forward-intent;
    extending section-15 is the right shape."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 415 complete - section-15 carriage + future_skill ==="
echo "=========================================================="
echo "Verify: bash dev-tools/diagnostic/section-15-wiring-cross-check.command"
echo "Expected: 3 orphan tools, 0 broken handoffs (was 6, 7)."
echo ""
echo "Press any key to close."
read -n 1 || true
