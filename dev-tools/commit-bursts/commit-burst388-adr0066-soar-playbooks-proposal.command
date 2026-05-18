#!/bin/bash
# Burst 388 - ADR-0066 D3 Phase D proposal (doc-only).
#
# Drafts the SOAR layer that closes the D3 SOC roadmap. With
# ADR-0065 detecting + this ADR responding + testing, D3 becomes
# a full Detect -> Respond -> Test loop. Doc-only; T1-T6 follow
# after operator green-light.
#
# What ADR-0066 specifies:
#
#   Playbook DSL (YAML in config/playbooks/*.yml):
#     trigger {detection_rule_ids, min_severity, cooldown_seconds}
#     approval {default: required_human, steps_auto_approved: [...]}
#     steps[]: id + action (tool/skill key) + args + per-step
#       requires_human_approval override
#     postconditions {audit_event_type}
#   Simple by design — no branches or loops; composition over
#   nesting. Operator-authored, version-controlled, reviewable.
#
#   playbook_pilot role (actuator genre — state-changing surface):
#     Subscribes to detection_fired chain events. Resolves against
#     playbook trigger table within cooldowns. Executes steps in
#     order: auto-approved steps run immediately; gated steps
#     queue to pending_calls. Emits playbook_executed audit events
#     with full step history.
#     Constitution policies enforce:
#       forbid_unscheduled_action - only act on detection-triggered
#         playbooks
#       require_playbook_signature_match - step's tool must match
#         playbook declaration verbatim (defense against
#         playbook+detection injection)
#       forbid_playbook_authorship - pilot consumes, operator writes
#       require_cooldown_respect - repeated detections within cooldown
#         don't re-fire
#
#   purple_pete role (researcher genre - adversary simulation):
#     Runs synthetic scenarios from config/purple_pete_scenarios/.
#     Writes synthetic events to data/telemetry_simulation.sqlite
#     (SEPARATE from production telemetry store). Measures
#     time-to-detect + time-to-respond. Emits
#     purple_team_run_completed events with metrics + coverage notes.
#     Signature skill purple_team_brief.v1 (parallel to
#     telemetry_steward_brief).
#     Constitution policies enforce:
#       forbid_production_telemetry_emit - simulation store only
#       forbid_real_response_dispatch - cannot invoke playbook_pilot
#       require_scenario_provenance - every synthetic event carries
#         purple_team_run_id + scenario name
#
#   Decisions:
#     D1 Playbooks are operator-authored YAML, NOT LLM-generated.
#        propose_playbook.v1 may land later as assistance; never
#        autonomous authorship.
#     D2 Every state-changing step requires human approval by
#        DEFAULT. steps_auto_approved is opt-IN, enumerated.
#        Inverts the call-time approval pattern to be safer
#        out-of-the-box.
#     D3 purple_pete writes to separate simulation store. The
#        chain entries carry simulation=true so production audit
#        reviewers can filter them.
#     D4 Cooldown per (playbook_id, detection_rule_id,
#        target_entity). Same target re-fires within window are
#        blocked; different target_entity is fresh.
#     D5 playbook_executed events are first-class chain entries
#        with rule_version=sha256(playbook body) so history pins
#        exact playbook ran.
#     D6 Scenarios in config/purple_pete_scenarios/. Operator
#        authored + reloadable via POST /purple_pete/reload.
#        Starter library: T1059.004 shell + T1003 cred-dump +
#        T1071 C2.
#     D7 Phase D closure requires both roles + DSL + end-to-end
#        smoke test (T6 ships the detection_fired -> playbook_
#        executed -> purple_team_run_completed live verify).
#
# Tranche plan (~6 bursts):
#   T1 ADR doc + Playbook DSL parser + PlaybookDef + tests
#   T2 PlaybookEngine + subscription + approval gating + audit
#   T3 playbook_pilot role (full wiring)
#   T4 purple_pete role + simulation store + scenario DSL + brief skill
#   T5 Operator runbook + starter playbook + scenario libraries
#   T6 End-to-end smoke + status: Accepted -- CLOSES Phase D + D3
#
# Why doc-only first (matches ADR-0079 + ADR-0080 + ADR-0065 pattern):
#   Six-tranche arc spans parser + engine + actuator role +
#   simulation surface + DSL design. Operator green-light before
#   code lands so the action-surface decisions get reviewed first.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm of NOT writing this ADR: Phase D has no paper trail;
#     future sessions would re-derive the playbook-DSL-vs-code
#     choice + the approval-default-direction + the simulation-
#     isolation discipline. Plus: the action surface is consequential;
#     undocumented design = unaccountable design.
#   Prove non-load-bearing: doc only. No code change.
#   Prove alternative is better: skipping the doc means T1+
#     ships without architectural decisions recorded; recovery
#     after a regression becomes harder.
#
# Verification after this commit lands:
#   1. Read docs/decisions/ADR-0066-soar-playbooks.md.
#   2. Operator green-lights or amends.
#   3. T1 starts as a separate burst once green-lit.
#
# What this CLOSES:
#   D3 SOC roadmap is now fully designed end-to-end:
#     Phase A (forensic_archivist) - shipped 2026-05-17
#     Phase B (telemetry_steward + threat_intel_curator) - shipped
#       2026-05-18 (B385+B386)
#     Phase C (detection_engineer) - proposed B387
#     Phase D (playbook_pilot + purple_pete) - proposed B388 (this)
#   Implementation tranches for C + D queue against operator
#   green-light.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0066-soar-playbooks.md \
        dev-tools/commit-bursts/commit-burst388-adr0066-soar-playbooks-proposal.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(adr): ADR-0066 SOAR playbooks + Phase D proposal (B388)

Burst 388. D3 Phase D ADR. Closes the D3 SOC design surface.
Doc-only; T1-T6 implementation tranches follow after operator
green-light.

What ADR-0066 specifies:
  Playbook DSL (config/playbooks/*.yml): trigger + approval +
    steps[] + postconditions. Simple by design — no branches or
    loops; composition over nesting. Operator-authored.
  playbook_pilot role (actuator): subscribes to detection_fired,
    executes matching playbooks within cooldowns, gates state-
    changing steps through approval BY DEFAULT (steps_auto_approved
    is opt-in enumerated). Emits playbook_executed audit events
    with playbook_version=sha256(playbook body).
  purple_pete role (researcher): runs synthetic ATT&CK scenarios
    against a SEPARATE simulation telemetry store; measures
    time-to-detect + time-to-respond; emits
    purple_team_run_completed events with metrics. NEVER pollutes
    production state.
  Constitution policies enforce the action-discipline (pilot only
    acts on detection triggers; signature match required;
    cooldown respect; pete cannot invoke pilot's action surface;
    provenance mandatory on all simulated events).

Tranche plan (~6 bursts):
  T1 parser + dataclass + tests
  T2 PlaybookEngine + subscription + approval gating + audit
  T3 playbook_pilot role (full wiring)
  T4 purple_pete role + simulation store + scenario DSL + brief
  T5 operator runbook + starter playbook + scenario libraries
  T6 end-to-end smoke + status: Accepted -- CLOSES Phase D + D3

Why doc-only first:
  Action surface is consequential. ADR pattern (B374/B387)
  surfaces architectural decisions before code lands so reviewer
  has artifact to anchor on.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: Phase D has no paper trail; undocumented design
    of the consequential action surface = unaccountable design.
  Prove non-load-bearing: doc only.
  Prove alternative is better: ad-hoc impl skips architectural
    decisions that ought to be recorded.

After this lands:
  D3 SOC roadmap fully designed end-to-end (A shipped, B shipped,
  C proposed, D proposed). Operator green-lights C T1 or D T1 to
  start implementation."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 388 complete - ADR-0066 proposed ==="
echo "=========================================================="
echo "Review: docs/decisions/ADR-0066-soar-playbooks.md"
echo "Green-light opens C T1 (Sigma parser) or D T1 (Playbook DSL)."
echo ""
echo "Press any key to close."
read -n 1 || true
