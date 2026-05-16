#!/bin/bash
# Burst 337 - ADR-0077 T4a: propose_tests.v1 skill.
#
# First of three D4 advanced skill implementations. Closes the loop
# for test_author: the role now has a concrete skill to run when
# software_engineer hands off a 'test_proposal' subintent.
#
# What ships:
#
# 1. examples/skills/propose_tests.v1.yaml (NEW):
#    Four-step pipeline:
#      prior_context  — memory_recall.v1 lineage-mode lookup
#      draft          — llm_think.v1 with a strict prompt that
#                       produces a self-contained pytest module
#                       expected to fail for the right reason
#      write_test     — code_edit.v1 writes the draft to disk under
#                       tests/ (allowed_paths constraint enforces)
#      collect_and_run — pytest --collect-only to catch
#                       import / syntax errors fast
#      confirm_failure — pytest -x -q to confirm tests fail for
#                       the right reason (the contract: non-zero
#                       returncode is expected; the caller reads
#                       pytest_stdout to learn what assertions
#                       to satisfy)
#    Inputs: spec_summary, target_test_path, production_module_path,
#    + optional context_query. Outputs the draft + pytest stdout +
#    return code so software_engineer reads the contract.
#
# 2. tests/unit/test_propose_tests_skill.py (NEW):
#    8 cases covering manifest parse, metadata, required tools,
#    inputs.required completeness, step ordering, write_test arg
#    plumbing, confirm_failure pytest invocation, output shape.
#
# Sandbox-verified 8/8 pass through the production manifest parser.
#
# Operator next step: copy the canonical YAML to
# data/forge/skills/installed/propose_tests.v1.yaml (or use the
# Skill Forge UI install path). The daemon's lifespan loads from
# data/forge/skills/installed/ so the operator-install dance is
# the deliberate gating point per ADR-0031.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add examples/skills/propose_tests.v1.yaml \
        tests/unit/test_propose_tests_skill.py \
        dev-tools/commit-bursts/commit-burst337-adr0077-t4a-propose-tests-skill.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(d4): ADR-0077 T4a - propose_tests.v1 skill (B337)

Burst 337. First of three D4 advanced skill implementations.
Closes the loop for test_author: the role now has a concrete
skill to run when software_engineer hands off a 'test_proposal'
subintent.

What ships:

  - examples/skills/propose_tests.v1.yaml (NEW): four-step
    pipeline. prior_context (memory_recall lineage-mode) →
    draft (llm_think with a strict prompt that produces a
    self-contained pytest module expected to fail for the
    right reason) → write_test (code_edit writes under tests/)
    → collect_and_run (pytest --collect-only fast syntax
    check) → confirm_failure (pytest -x -q to confirm tests
    fail for the right reason — contract is non-zero return
    code; caller reads stdout for what to satisfy).

  - tests/unit/test_propose_tests_skill.py (NEW): 8 cases
    covering manifest parse, metadata, required tools, inputs.
    required completeness, step ordering, write_test arg
    plumbing, confirm_failure pytest invocation, output shape.

Sandbox-verified 8/8 pass through the production manifest
parser.

Next: T4b safe_migration.v1 (B338), T4c release_check.v1
(B339). Both follow the same skill-template shape; T4b adds
the dry-run + apply-gate dance, T4c orchestrates the
conformance suite + drift sentinel + signed-artifact check."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 337 complete - propose_tests.v1 skill shipped ==="
echo "ADR-0077 progress: T1+T2+T2b+T3+T4a (3/6 tranches partial)."
echo ""
echo "Press any key to close."
read -n 1
