#!/bin/bash
# Burst 300 - ADR-0073 T2: audit segment sealing flow.
#
# Runner on top of B291 substrate. seal_segment() takes a
# SegmentIndex + segment_dir, finds the current tail, reads its
# file, computes the Merkle root over per-entry hashes, returns
# a SealOutcome with (new_index, anchor_payload, next_segment_path).
# Pure function - the caller (a future scheduler-driven runner)
# writes the new index to disk and appends the anchor entry to
# the new tail.
#
# What ships:
#
# 1. src/forest_soul_forge/core/audit_chain_segments.py:
#    - New SealError exception.
#    - _read_segment_hashes_and_seqs helper: scans a segment file
#      line by line, extracts (entry_hash, seq) pairs. Refuses to
#      seal on malformed JSON, missing fields, or empty files -
#      sealing is observability substrate, correctness can't be
#      compromised by silent drops.
#    - SealOutcome frozen dataclass with new_index, anchor,
#      next_segment_path.
#    - seal_segment() pure runner: identify tail, scan file,
#      compute Merkle root, build sealed SegmentMeta with
#      seq_end + merkle_root populated, allocate new tail for
#      next_month (defaults to current UTC month), pack the
#      anchor payload. Returns SealOutcome. Splitting side-
#      effects out keeps T2 testable without a live chain.
#
# 2. tests/unit/test_audit_chain_sealing.py - 10 cases:
#    Happy path:
#      - sealed segment has merkle_root + seq_end
#      - new tail allocated for next_month with right seq_start
#      - anchor payload matches sealed segment
#      - next_segment_path points at NEW tail (where caller
#        writes the anchor entry)
#      - prior sealed segments preserved unchanged
#    Error paths:
#      - SealError when no tail (T4 migration helper's job)
#      - SealError when tail file missing
#      - SealError on malformed JSON line
#      - SealError on entry without entry_hash
#      - SealError on empty tail file
#
# What's NOT in T2 (queued):
#   T3: verify_chain extension - mode='tail' verifier consults
#       sealed segments' merkle_root from the index and skips
#       walking those entries. T2 produced the Merkle root; T3
#       wires the verifier to use it.
#   T4: migration helper that bootstraps the index from an
#       existing monolithic audit_chain.jsonl.
#   T5: operator runbook + scheduler task wiring (the runner
#       that calls seal_segment() monthly via ADR-0075 budget-
#       capped scheduled task).

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/audit_chain_segments.py \
        tests/unit/test_audit_chain_sealing.py \
        dev-tools/commit-bursts/commit-burst300-adr0073-t2-sealing.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(audit): ADR-0073 T2 - segment sealing flow (B300)

Burst 300. Runner on top of B291 substrate. seal_segment() takes
a SegmentIndex + segment_dir, finds the current tail, reads its
file, computes the Merkle root over per-entry hashes, returns
SealOutcome(new_index, anchor, next_segment_path). Pure function
- the caller (a future scheduled runner) handles disk side-
effects.

What ships:

  - audit_chain_segments.py: SealError exception,
    _read_segment_hashes_and_seqs helper (refuses on malformed
    JSON / missing fields / empty files - sealing is
    observability substrate, no silent drops), SealOutcome
    frozen dataclass, seal_segment() runner. The runner is
    pure: it doesn't write anything to disk, so T2 stays
    testable without a live chain. Allocates new tail for
    next_month (defaults to current UTC month, caller-
    overridable for tests). Anchor payload matches the locked
    AnchorPayload shape from B291.

Tests: test_audit_chain_sealing.py - 10 cases covering happy-
path output (sealed segment merkle_root + seq_end, new tail
allocation, anchor payload shape, next_segment_path pointing
at NEW tail, multi-segment preservation) and five SealError
paths (no tail, missing file, malformed JSON, missing
entry_hash, empty file).

Sandbox-verified: deterministic Merkle root over three known
entry hashes, anchor + new-tail seq math correct, all error
paths trip cleanly.

Queued T3-T5: verify_chain mode='tail' extension, migration
helper for the monolithic chain, operator runbook + scheduled
runner."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 300 complete - ADR-0073 T2 sealing flow shipped ==="
echo ""
echo "Press any key to close."
read -n 1
