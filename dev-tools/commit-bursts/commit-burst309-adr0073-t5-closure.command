#!/bin/bash
# Burst 309 - ADR-0073 T5: sealing runner + operator runbook.
#
# Wraps B300 seal_segment + B304 migration into a callable runner
# suitable for scheduling. Adds the operator runbook covering
# migration, ongoing sealing, verification, and tamper response.
# Closes ADR-0073 5/5.
#
# What ships:
#
# 1. src/forest_soul_forge/core/audit_chain_segments.py:
#    - SealRunResult frozen dataclass: ok + no_op_reason +
#      sealed_segment_file + next_segment_file +
#      anchor_payload + wall_clock_ms.
#    - seal_audit_segment_runner: async runner. Loads index
#      from disk, identifies tail, refuses if tail's month is
#      the current UTC month (force=True overrides), calls
#      seal_segment (pure), persists the new index BEFORE
#      emitting the anchor entry (so a chain-emit failure
#      leaves recoverable disk state — the anchor can be
#      manually re-emitted), appends audit_chain_anchor to the
#      chain. Soft-fails the chain emit if it raises.
#    - _seal_wall_clock_ms helper for the wall-clock field.
#
# 2. tests/unit/test_audit_chain_sealing.py - 6 new T5 cases:
#    - past-month tail → seals + emits anchor with the locked
#      payload shape (prior_segment_file, prior_seq_end,
#      prior_merkle_root, prior_segment_entry_count)
#    - index persisted after seal (operator can reload + see
#      the sealed segment)
#    - current-month tail → no_op_reason='tail_is_current_month'
#      + zero chain emits (split-mid-month protection)
#    - force=True overrides current-month guard
#    - missing index → no_op_reason='no_tail_segment'
#    - anchor emit failure → ok=False + reason carries the
#      error; index stays persisted (no half-sealed state)
#
# 3. docs/runbooks/audit-chain-segmentation.md - operator runbook:
#    - First-time migration script (calls migrate_monolithic_chain
#      + save_segment_index; rollback by deleting new files)
#    - Manual sealing invocation + every no_op_reason explained
#    - Scheduled-task wiring proposal for config/scheduled_tasks.yaml
#      (uses the existing tool_call runner; the audit_chain_seal.v1
#       tool wrapper is a follow-on burst)
#    - verify_sealed_segments operator workflow + issue kinds
#    - Tamper-response procedure: compare disk's recomputed root
#      against the on-chain anchor's signed root, restore-from-
#      backup vs index-corruption diagnosis, chain_repair event
#      for post-resolution documentation
#    - Performance posture (migration O(N), sealing O(M),
#      verification O(K); Merkle root ~300K hashes/sec on
#      M-series)
#
# Sandbox-verified all 4 end-to-end scenarios pre-commit
# (past-month seal + anchor / current-month no-op /
# force-override / missing-index graceful).
#
# === ADR-0073 CLOSED 5/5 ===
# Audit chain segmentation arc complete. Phase alpha scorecard:
# 5/10 closed (ADR-0050, ADR-0067, ADR-0073, ADR-0074, ADR-0075).
#
# What's NOT in this burst (queued separately):
#   T3b: AuditChain.verify(mode='tail') integration that uses
#       verify_sealed_segments to skip line-by-line walks of
#       sealed segments. T3a (B301) shipped the substrate;
#       T3b is the verify() composition pass.
#   audit_chain_seal.v1 tool wrapper for the scheduled task.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/audit_chain_segments.py \
        tests/unit/test_audit_chain_sealing.py \
        docs/runbooks/audit-chain-segmentation.md \
        dev-tools/commit-bursts/commit-burst309-adr0073-t5-closure.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(audit): ADR-0073 T5 - sealing runner + runbook (B309) — ARC CLOSED 5/5

Burst 309. Wraps B300 seal_segment + B304 migration into a
callable runner suitable for scheduling. Adds the operator
runbook covering migration, ongoing sealing, verification, and
tamper response.

What ships:

  - audit_chain_segments.py: SealRunResult frozen dataclass +
    seal_audit_segment_runner async function. Loads index,
    refuses when tail month == current UTC month (force=True
    overrides), calls pure seal_segment, persists the new
    index BEFORE emitting the anchor entry (so a chain-emit
    failure leaves recoverable disk state), appends
    audit_chain_anchor. Soft-fails the chain emit if it raises.

  - docs/runbooks/audit-chain-segmentation.md: operator runbook
    covering first-time migration script + manual sealing
    invocation + every no_op_reason explained + scheduled-task
    wiring proposal + verify_sealed_segments workflow + tamper-
    response procedure (signed anchor as source of truth for
    pre-tamper state; chain_repair event for post-resolution
    documentation) + performance posture.

Tests: test_audit_chain_sealing.py - 6 new T5 cases covering
past-month seal + anchor payload shape, index persistence after
seal, current-month no-op protection, force=True override,
missing-index graceful no_op, and anchor-emit-failure recovery
(index stays sealed even if chain emit fails - rolling back
would produce a half-sealed segment, worse than a missing
anchor entry).

Sandbox-verified all 4 end-to-end scenarios pre-commit.

=== ADR-0073 CLOSED 5/5 ===
Audit chain segmentation arc complete. Phase alpha scorecard:
5/10 closed (ADR-0050, ADR-0067, ADR-0073, ADR-0074, ADR-0075).

Queued: T3b verify(mode='tail') integration + audit_chain_seal.v1
tool wrapper for the scheduled-task path."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 309 complete - ADR-0073 CLOSED 5/5 ==="
echo "Phase alpha: 5/10 scale ADRs closed."
echo ""
echo "Press any key to close."
read -n 1
