#!/bin/bash
# Bursts 338 + 339 - ADR-0077 T4b + T4c: safe_migration.v1 +
# release_check.v1 skills.
#
# Two parallel skill drops bundled into one commit since they
# share the same template shape (canonical YAML in
# examples/skills/, tests under tests/unit/test_*_skill.py,
# manifest-parser-driven sandbox verification). Operator
# install path remains the same: copy from examples/skills/
# into data/forge/skills/installed/ when ready.
#
# What ships:
#
# 1. examples/skills/safe_migration.v1.yaml (NEW):
#    migration_pilot's analysis + dry-run skill. Six steps:
#      prior_context (memory_recall) → analyze (llm_think
#      produces FK-cascade plan + rollback plan with
#      ---ROLLBACK--- delimiter the operator parses) →
#      clone_registry (cp production → /tmp/registry.dryrun.<id>
#      .sqlite — production untouched) → dry_run (sqlite3 against
#      scratch) → integrity_check (PRAGMA integrity_check) →
#      recommend (llm_think emits GO / NO-GO / NEEDS-REVIEW
#      verdict on the LAST line for tooling to parse).
#    Apply step is intentionally NOT in this skill — operator
#    runs `fsf migrate apply` separately after reviewing the
#    recommendation.
#
# 2. examples/skills/release_check.v1.yaml (NEW):
#    release_gatekeeper's pre-release gating skill. Five steps:
#      prior_context → conformance (pytest tests/conformance/) →
#      drift_sentinel (./dev-tools/check-drift.sh + changelog
#      grep for the release_tag) → chain_verify
#      (audit_chain_verify.v1) → decide (llm_think synthesizes
#      with PASS / FAIL / INSUFFICIENT-EVIDENCE verdict line).
#    Notably no git, twine, curl, or code_edit in requires —
#    forbid_release_action enforced both at constitutional
#    layer + at kit layer.
#
# 3. tests/unit/test_safe_migration_skill.py (NEW): 7 cases
#    covering manifest parse, kit subset, scratch-only DB
#    invariant (defense-in-depth on the production DB write
#    pattern), clone step shape, recommend step terminal
#    position, no-apply-step invariant, output completeness.
#
# 4. tests/unit/test_release_check_skill.py (NEW): 7 cases
#    covering manifest parse, kit (no git/twine/curl), pipeline
#    decision-step-last, verdict-literal enumeration in prompt,
#    required inputs, output completeness.
#
# Sandbox-verified 22/22 across the three skill test files
# (propose_tests + safe_migration + release_check).
#
# === ADR-0077 progress: T1+T2+T2b+T3+T4 ===
# T4 closed across all three D4 advanced skills. Remaining:
# T5 (SBOM ownership decision + dev-tools/birth-d4-advanced
# umbrella script) and T6 (operator runbook). Estimated 2 more
# bursts to ARC CLOSED 6/6.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add examples/skills/safe_migration.v1.yaml \
        examples/skills/release_check.v1.yaml \
        tests/unit/test_safe_migration_skill.py \
        tests/unit/test_release_check_skill.py \
        dev-tools/commit-bursts/commit-burst338-339-adr0077-t4bc-skills.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(d4): ADR-0077 T4b+T4c - safe_migration + release_check skills (B338+B339)

Bursts 338 + 339. Two parallel skill drops bundled. Same
template shape as B337's propose_tests.v1 (canonical YAML in
examples/skills/, manifest-parser-validated tests).

What ships:

  - examples/skills/safe_migration.v1.yaml (NEW):
    migration_pilot's analysis + dry-run skill. Six steps —
    prior_context → analyze (FK-cascade plan + rollback plan
    with ---ROLLBACK--- delimiter) → clone_registry (cp
    production → /tmp/registry.dryrun.<id>.sqlite, production
    untouched) → dry_run (sqlite3 against scratch) →
    integrity_check (PRAGMA integrity_check) → recommend
    (GO/NO-GO/NEEDS-REVIEW verdict last line). Apply step
    intentionally NOT in this skill — operator runs
    \`fsf migrate apply\` separately.

  - examples/skills/release_check.v1.yaml (NEW):
    release_gatekeeper's pre-release gate. Five steps —
    prior_context → conformance (pytest tests/conformance/) →
    drift_sentinel (./dev-tools/check-drift.sh + changelog
    grep) → chain_verify (audit_chain_verify.v1) → decide
    (PASS/FAIL/INSUFFICIENT-EVIDENCE verdict last line).
    Notably no git/twine/curl/code_edit in requires —
    forbid_release_action enforced at both constitutional +
    kit layers.

  - tests/unit/test_safe_migration_skill.py: 7 cases including
    a defense-in-depth invariant that no step writes to the
    production DB (only clone_registry references it as cp
    source).

  - tests/unit/test_release_check_skill.py: 7 cases including
    enumeration of the three verdict literals + the
    no-release-acting-tools-in-requires invariant.

Sandbox-verified 22/22 across propose_tests + safe_migration +
release_check skill tests.

ADR-0077 progress: T1+T2+T2b+T3+T4 shipped. Remaining: T5
(birth-d4-advanced umbrella script + SBOM ownership decision)
+ T6 (operator runbook). 2 more bursts to ARC CLOSED 6/6."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Bursts 338+339 complete - T4 D4 skills shipped ==="
echo "ADR-0077 progress: 5/6 tranches partial. T5+T6 close it."
echo ""
echo "Press any key to close."
read -n 1
