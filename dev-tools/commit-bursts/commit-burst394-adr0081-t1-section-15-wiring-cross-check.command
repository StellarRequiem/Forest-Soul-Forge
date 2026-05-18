#!/bin/bash
# Burst 394 - ADR-0081 T1: section-15 substrate wiring cross-check.
#
# First implementation tranche of the ADR-0081 arc (proposed B393).
# Adds the harness section that asks the cross-cutting questions
# the 14 isolated-layer sections miss — the gap class that let
# B363/B392 ship undetected for an entire substrate-rollout window.
#
# What this commit adds:
#
# 1. dev-tools/diagnostic/section-15-wiring-cross-check.command
#    Daemon-independent. Reads disk only (tool_catalog.yaml,
#    handoffs.yaml, examples/skills/, soul_generated/*.yaml,
#    config/domains/). Computes four cross-cutting checks:
#
#      (a) Tool wiring coverage: every cataloged tool has at
#          least one carrier (archetype kit OR genre default OR
#          alive agent). Orphan tools = FAIL.
#      (b) Skill requires resolve: every installed skill's
#          requires list resolves to cataloged tools. Unresolvable
#          requires = FAIL.
#      (c) Skill has carrier archetype: every installed skill has
#          at least one archetype kit that carries all its
#          required tools. No-carrier = FAIL.
#      (d) Handoff routes end-to-end: every handoff
#          (domain, capability) -> skill route points at an
#          existing skill manifest AND at least one entry_agent
#          role for that domain carries the required tools.
#          Broken = FAIL.
#
#    Plus INFO checks:
#      - Tools in archetype kits but no alive agent yet
#        (normal during rollouts).
#      - Broken constitution parse count (section-05's lane).
#
#    Outputs:
#      data/test-runs/diagnostic-15-wiring-cross-check/report.md
#        Operator-readable markdown with check table + per-failure
#        evidence.
#      data/test-runs/diagnostic-15-wiring-cross-check/coverage.json
#        Structured findings the T4 wiring_audit.v1 skill will
#        consume + the T2 wiring-coverage.html generator will
#        render. Includes per-tool carrier matrix for drilldown.
#
# 2. dev-tools/diagnostic/diagnostic-all.command
#    SECTIONS array gains "15-wiring-cross-check" with a comment
#    explaining the gap class it catches.
#
# Initial dry-run (this commit's substrate state) — section-15
# finds:
#   - 6 orphan tools (cataloged, zero kits/agents carry them):
#       decompose_intent.v1, operator_profile_read.v1,
#       operator_profile_write.v1, personal_recall.v1,
#       route_to_domain.v1, security_scan.v1
#   - 7 broken handoff routes (skill referenced in handoffs.yaml
#     but no manifest in examples/skills/):
#       d2/reminder -> schedule_reminder.v1
#       d2/morning_briefing -> generate_briefing.v1
#       d3/incident_summary -> summarize_recent_incidents.v1
#       d3/incident_response -> respond_to_incident.v1
#       d4/review_signoff -> review_pr.v1
#       (+2 more in the report)
#
# These findings are EXACTLY the gap class ADR-0081 was written
# to surface. Per ADR-0081 D5 (sentinel finds, operator fixes),
# section-15 reports the gaps; the operator queues per-gap
# remediation as separate bursts. Not auto-fixing here is the
# constitution-immutability invariant in action.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: B363 + 9 dead skill manifests + 9-dead-skills
#     B361/B370 audit gaps all surfaced months/weeks after they
#     landed because the existing 14 sections check layers in
#     isolation. Section-15 makes the gap class fail loud within
#     seconds of substrate change.
#   Prove non-load-bearing: ADDITION only. New section, daemon-
#     independent, no behavior changes to existing 14 sections.
#     Umbrella's SECTIONS array gains one entry. Reports surface
#     pre-existing gaps; landing this commit does not introduce
#     any gap.
#   Prove alternative is strictly better: alternatives are
#     (a) extend section-04/05/09 to do this work — would conflate
#         per-layer probes with cross-cutting checks and obscure
#         the failure source; (b) operator-driven manual check
#         when motivated — that's what missed B363 for months.
#     A dedicated section that runs every harness cycle is the
#     auditable, repeatable answer.
#
# Verification after this commit lands:
#   1. force-restart-daemon
#   2. bash dev-tools/diagnostic/section-15-wiring-cross-check.command
#      Expected: 2/6 pass (2 fail + 2 info) with the 6 orphans +
#      7 broken handoffs above. Exit code 1 (loud).
#   3. bash dev-tools/diagnostic/diagnostic-all.command
#      Expected: all 15 sections run; section-15 lands its
#      report.md + coverage.json into the run dir; umbrella
#      summary shows section-15 in the table.
#   4. cat data/test-runs/diagnostic-15-wiring-cross-check/coverage.json
#      Expected: structured findings with summary block +
#      orphan_tools + skills_unresolvable + skills_no_carrier
#      + handoffs_broken + tool_carriers matrix.
#
# What this UNBLOCKS / queues next:
#   T2: wiring-coverage.html generator (consumes coverage.json).
#   T3: wiring_sentinel role (guardian-genre singleton).
#   T4: wiring_audit.v1 skill (runs section-15, reads coverage.json,
#       escalates medium+ gaps via delegate-to-operator-queue).
#   T5: scheduled task + runbook (4-hour cadence).
#   T6: CLOSE — live verify + north-star + status: Accepted.
#
# Operator decisions surfaced by this commit's initial dry-run:
#   - 6 orphan tools: retire OR assign to archetype kits? Each
#     one's wiring is a per-tool operator call.
#   - 7 broken handoff routes: each skill is either (a) build it,
#     (b) re-route the handoff to an existing skill, or
#     (c) remove the handoff entry. Domain owners decide.
#   Section-15 surfaces; operator queues remediation bursts as
#   separate work.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/diagnostic/section-15-wiring-cross-check.command \
        dev-tools/diagnostic/diagnostic-all.command \
        dev-tools/commit-bursts/commit-burst394-adr0081-t1-section-15-wiring-cross-check.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(diagnostic): section-15 substrate wiring cross-check (ADR-0081 T1, B394)

Burst 394. First implementation tranche of the ADR-0081 wiring
coverage arc (proposed B393). Adds the harness section that asks
the cross-cutting questions the 14 isolated-layer sections miss
- the gap class that let B363/B392 ship undetected.

section-15-wiring-cross-check.command (NEW):
  Daemon-independent; reads disk only (tool_catalog.yaml,
  handoffs.yaml, examples/skills/, soul_generated, config/domains).
  Four cross-cutting checks:
    (a) Tool wiring coverage: every cataloged tool has at least
        one carrier (archetype kit / genre default / alive agent).
    (b) Skill requires resolve: every installed skill's requires
        resolve to cataloged tools.
    (c) Skill carrier archetype: every installed skill has at
        least one archetype kit carrying all required tools.
    (d) Handoff routes end-to-end: every handoff
        (domain, capability) -> skill route points at an existing
        skill manifest + at least one entry_agent role for that
        domain carries the required tools.
  Outputs report.md + coverage.json (structured, drilldown-ready).

diagnostic-all.command:
  SECTIONS array gains '15-wiring-cross-check' with comment.

Initial dry-run findings (this commit's substrate state):
  6 orphan tools (cataloged, zero kits/agents carry):
    decompose_intent, operator_profile_read/write, personal_recall,
    route_to_domain, security_scan.
  7 broken handoff routes (skill in handoffs.yaml, no manifest):
    d2/reminder, d2/morning_briefing, d3/incident_summary,
    d3/incident_response, d4/review_signoff (+2 more).

These are EXACTLY the gap class ADR-0081 surfaces. Per D5
(sentinel finds, operator fixes), section-15 reports the gaps;
operator queues per-gap remediation as separate bursts. Not
auto-fixing here is the constitution-immutability invariant in
action.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: B363, 9 dead skills, audit-sig gaps all surfaced
    months after they landed. Cross-cutting check makes gap class
    fail loud within seconds of substrate change.
  Prove non-load-bearing: ADDITION only. New section, no behavior
    changes elsewhere. Reports surface pre-existing gaps.
  Prove alternative is better: extending sections 04/05/09 would
    conflate per-layer probes with cross-cutting checks; operator-
    driven manual check is what missed B363 for months.

T2-T6 queued: wiring-coverage.html -> wiring_sentinel role ->
wiring_audit.v1 skill -> scheduled task + runbook -> CLOSE."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 394 complete - ADR-0081 T1 shipped ==="
echo "=========================================================="
echo "Verify:"
echo "  bash dev-tools/diagnostic/section-15-wiring-cross-check.command"
echo "  Expected: 2/6 pass (2 FAIL + 2 INFO), exit 1."
echo "  Findings: 6 orphan tools + 7 broken handoff routes."
echo ""
echo "Next: T2 (wiring-coverage.html generator)."
echo ""
echo "Press any key to close."
read -n 1 || true
