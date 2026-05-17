#!/bin/bash
# Burst 346 - ADR-0078 Phase A T4: archive_evidence.v1 skill.
#
# Gives ForensicArchivist-D3 something to execute when the
# orchestrator routes a forensic_archive subintent to it.
# Read-only end-to-end — no code_edit, no shell_exec, no
# filesystem writes outside the agent's own private memory.
#
# What ships:
#
# 1. examples/skills/archive_evidence.v1.yaml: Five-step pipeline:
#      1. prior_context (memory_recall scope=private, query by
#         artifact_id — walk the per-artifact chain)
#      2. verify_artifact_integrity (file_integrity snapshot —
#         sha256 the artifact with symlink-refusing semantics)
#      3. verify_chain_integrity (audit_chain_verify — confirms
#         the chain itself is hash-linked end-to-end; a broken
#         chain invalidates ANY new attestation)
#      4. evaluate_transition (llm_think with 7-rule decision
#         matrix: chain_broken → duplicate_acquire →
#         orphan_transition → missing_handoff_target →
#         tamper_suspected → operator_chain_disagreement →
#         ATTEST). Emits structured VERDICT block.
#      5. write_attestation (memory_write scope=private,
#         layer=episodic; records BOTH ATTEST and HALT verdicts).
#
#    Inputs: artifact_id + artifact_path + transition_type
#    (enum {acquire, handoff, retire}) + attestor_reason
#    required; handoff_to + expected_prior_hash optional.
#
#    Constitutional alignment:
#      - forbid_artifact_mutation: enforced by NOT requiring
#        code_edit/shell_exec; the kit can't violate it
#      - require_chain_of_custody_log: write_attestation runs
#        unconditionally (HALT verdicts get recorded too)
#      - forbid_silent_archive: same — both verdict types log
#      - require_integrity_hash_verification: dedicated
#        verify_chain_integrity step BEFORE attestation
#
# 2. tests/unit/test_archive_evidence_skill.py: 11 assertions:
#      - parse cleanly through production manifest loader
#      - requires-block matches the 5 referenced tools
#      - NO mutation tools in requires (read-only invariant
#        pinned in code; future bursts can't quietly add
#        code_edit without deleting this test)
#      - transition_type enum exactly {acquire, handoff, retire}
#      - step order matches the 5-step pipeline
#      - evaluate_transition is llm_think (pinning the verdict
#        reasoning surface; pattern-match alone would miss HALT
#        cases)
#      - verify_chain_integrity step exists (load-bearing)
#      - write_attestation records BOTH ATTEST + HALT
#      - output surfaces 6 key fields (artifact_id +
#        transition_type + current_hash + chain_status +
#        verdict_block + attestation_entry_id)
#
# Test results: 11/11 archive_evidence + 8/8 propose_tests +
# 19/19 skill_manifest = 38/38 green, no regressions.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add examples/skills/archive_evidence.v1.yaml \
        tests/unit/test_archive_evidence_skill.py \
        dev-tools/commit-bursts/commit-burst346-d3-phase-a-archive-evidence-skill.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(d3): Phase A T4 - archive_evidence.v1 skill (B346)

Burst 346. Gives ForensicArchivist-D3 something to execute when
the orchestrator routes a forensic_archive subintent to it.
Read-only end-to-end - no code_edit, no shell_exec, no filesystem
writes outside the agents own private memory.

examples/skills/archive_evidence.v1.yaml:
  Five-step pipeline:
    1. prior_context (memory_recall scope=private; walk the
       per-artifact custody chain)
    2. verify_artifact_integrity (file_integrity snapshot;
       symlink-refusing sha256)
    3. verify_chain_integrity (audit_chain_verify; a broken
       chain invalidates ANY new attestation)
    4. evaluate_transition (llm_think with 7-rule decision
       matrix; emits structured VERDICT block)
    5. write_attestation (memory_write scope=private,
       layer=episodic; records BOTH ATTEST and HALT verdicts)

  Inputs: artifact_id + artifact_path + transition_type
  (enum {acquire, handoff, retire}) + attestor_reason
  required; handoff_to + expected_prior_hash optional.

  Constitutional alignment:
    - forbid_artifact_mutation: enforced by NOT requiring
      code_edit/shell_exec; the kit cannot violate it
    - require_chain_of_custody_log: write_attestation runs
      unconditionally (HALT verdicts get recorded too)
    - forbid_silent_archive: same - both verdict types log
    - require_integrity_hash_verification: dedicated
      verify_chain_integrity step BEFORE attestation

tests/unit/test_archive_evidence_skill.py:
  11 assertions, with the load-bearing one being
  test_no_mutation_tools_in_requires - pins the read-only
  invariant in code so a future burst cant quietly add
  code_edit/shell_exec without deleting the test.

Test results: 11/11 archive_evidence + 8/8 propose_tests +
19/19 skill_manifest = 38/38 green, no regressions.

Next: B347 umbrella birth script + runbook (closes Phase A)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 346 complete - D3 Phase A T4 shipped ==="
echo "Next: B347 umbrella birth script + runbook (closes Phase A)."
echo ""
echo "Press any key to close."
read -n 1 || true
