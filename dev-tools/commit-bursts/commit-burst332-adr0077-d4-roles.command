#!/bin/bash
# Burst 332 - ADR-0077 D4 T2: trait_tree + genres + constitution
# templates for the three new D4 advanced rollout roles.
#
# Operator reviewed the draft at
# docs/audits/2026-05-16-d4-advanced-rollout-drafts.md and
# approved all five judgment calls + the forbid_self_test_
# deletion policy follow-up.
#
# What ships:
#
# 1. config/trait_tree.yaml:
#    Three new role entries with domain_weights:
#      test_author: cognitive 2.3, audit 2.0, communication 1.4,
#        security 1.4, embodiment 1.2, emotional 0.4
#      migration_pilot: audit 2.4, security 2.2, cognitive 2.0,
#        communication 1.4, embodiment 0.9, emotional 0.4
#      release_gatekeeper: audit 2.6 (max in system), security
#        2.2, communication 1.8, cognitive 1.7, embodiment 0.5,
#        emotional 0.4
#    Inserted directly above domain_orchestrator with the ADR-0077
#    section header.
#
# 2. config/genres.yaml:
#    test_author → researcher.roles
#    migration_pilot + release_gatekeeper → guardian.roles
#    Each tagged with `# ADR-0077 (B331-B333) — D4 advanced rollout`.
#
# 3. config/constitution_templates.yaml:
#    Three new role_base entries, each with:
#      - policies (3-4 per role): forbid_production_code_edit +
#        require_assertion_in_test + approval_for_test_dependency_add
#        + forbid_self_test_deletion (test_author);
#        require_dry_run_before_apply + require_human_approval_for_apply
#        + forbid_silent_drop + require_rollback_plan
#        (migration_pilot); forbid_release_action +
#        require_conformance_evidence + require_fail_explanation
#        + forbid_check_skip (release_gatekeeper).
#      - risk_thresholds: min_confidence_to_act 0.55 / 0.70 / 0.80.
#      - out_of_scope + operator_duties + drift_monitoring blocks.
#    Inserted directly above verifier_loop with the ADR-0077 section
#    header.
#
# Tests (test_d4_advanced_rollout.py - 27 cases):
#   trait_tree (12, parametrized 4× per role):
#     role exists, has 6 domain_weight keys, weights in [0,3],
#     release_gatekeeper audit is max in system
#   genres (10, parametrized 3× + 1× + 6×):
#     test_author in researcher, migration_pilot + release_
#     gatekeeper in guardian, each claimed exactly once,
#     trait_engine invariant still holds
#   constitution_templates (5):
#     each role has template + required blocks, critical
#     policies present per role, min_confidence calibration
#     matches draft
#
# Sandbox-verified 27/27 pass.
#
# === ADR-0077 progress: T2 substrate landed; T2b birth scripts next ===
# Roles defined but not yet birthed. B333 ships the
# birth-test-author.command, birth-migration-pilot.command,
# birth-release-gatekeeper.command operator-driven scripts.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add config/trait_tree.yaml \
        config/genres.yaml \
        config/constitution_templates.yaml \
        docs/audits/2026-05-16-d4-advanced-rollout-drafts.md \
        tests/unit/test_d4_advanced_rollout.py \
        dev-tools/commit-bursts/commit-burst332-adr0077-d4-roles.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(d4): ADR-0077 T2 - three new role definitions (B332)

Burst 332. Operator-approved draft at
docs/audits/2026-05-16-d4-advanced-rollout-drafts.md lands as
production config. Three new D4 roles defined: test_author
(researcher), migration_pilot (guardian), release_gatekeeper
(guardian). All five operator-flagged judgment calls accepted
in the draft + the agreed forbid_self_test_deletion policy
follow-up included.

What ships:

  - config/trait_tree.yaml: test_author cognitive 2.3 audit 2.0,
    migration_pilot audit 2.4 security 2.2, release_gatekeeper
    audit 2.6 (max in system). All entries above domain_orchestrator.

  - config/genres.yaml: test_author → researcher.roles;
    migration_pilot + release_gatekeeper → guardian.roles.

  - config/constitution_templates.yaml: three role_base entries
    with policies (3-4 each), risk_thresholds (min_confidence_to
    _act 0.55 / 0.70 / 0.80), out_of_scope, operator_duties,
    drift_monitoring. test_author's forbid_self_test_deletion
    policy ensures tests-this-agent-wrote can only be retired by
    operator or software_engineer, preserving the
    test-first-discipline audit trail.

  - docs/audits/2026-05-16-d4-advanced-rollout-drafts.md: the
    review surface the operator scrutinized before approving.
    Preserved as audit context for future ADR-0077 tranches.

Tests: test_d4_advanced_rollout.py — 27 cases covering 12
trait_tree assertions (role exists, weight keys, weight ranges,
release_gatekeeper.audit is max), 10 genres assertions
(genre assignment, single-claim invariant, trait_engine
invariant still holds), 5 constitution_template assertions
(template exists, required blocks, critical policies present,
min_confidence calibration). Sandbox-verified 27/27 pass.

ADR-0077 progress: T1 doc (B331) + T2 substrate (B332). Next:
T2b birth-script triplet (B333), then T3 handoffs.yaml wiring,
T4 skill implementations, T5 SBOM + birth orchestration, T6
runbook."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 332 complete - ADR-0077 T2 roles defined ==="
echo "Three D4 agents ready for birth via B333's scripts."
echo ""
echo "Press any key to close."
read -n 1
