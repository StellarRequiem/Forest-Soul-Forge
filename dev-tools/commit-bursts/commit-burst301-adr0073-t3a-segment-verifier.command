#!/bin/bash
# Burst 301 - ADR-0073 T3a: sealed-segment Merkle verifier.
#
# Contained slice of T3. The full mode='tail' integration into
# AuditChain.verify() comes in T3b; T3a ships the building block.
# verify_sealed_segments(index, segment_dir) walks the index,
# recomputes Merkle root over entry_hashes for each sealed
# segment, compares to the stored merkle_root. Returns a
# SegmentVerifyResult with issues by value so the operator sees
# all problems in one pass.
#
# What ships:
#
# 1. src/forest_soul_forge/core/audit_chain_segments.py:
#    - SegmentVerifyIssue frozen dataclass (kind, segment_file,
#      details). kind is a stable string for programmatic dispatch:
#      'merkle_mismatch' | 'file_missing' | 'no_root' | 'scan_error'.
#    - SegmentVerifyResult frozen dataclass (ok, segments_verified,
#      issues). ok is True iff every sealed segment hashes clean.
#    - verify_sealed_segments() function. Walks index.sealed_segments(),
#      re-reads each file, recomputes Merkle root, compares. Issues
#      accumulate rather than raise - callers that want refuse-on-
#      first-issue check result.ok. Errors get classified into
#      'file_missing' vs 'scan_error' so dashboards can split
#      presentation. A segment marked sealed but missing merkle_root
#      surfaces as 'no_root' (index schema violation, not tamper).
#
# 2. tests/unit/test_audit_chain_sealing.py - 7 new T3a cases:
#    - clean sealed segment verifies
#    - merkle mismatch surfaces (the tamper signal)
#    - missing file surfaces
#    - no_root surfaces (sealed-but-no-root schema violation)
#    - unsealed segment skipped (out of scope for this verifier)
#    - multi-segment mixed outcome reports all in one pass
#    - SegmentVerifyIssue frozen-ness
#
# Why split T3 into T3a/T3b:
#   T3 spec (ADR-0073 D3): mode='tail' verifier consults sealed
#   segments' merkle_root from the index and skips walking those
#   entries. That's two things: (a) the segment-level Merkle
#   verifier substrate, and (b) the AuditChain.verify() extension
#   that integrates the substrate as a mode. T3a here is (a) -
#   the substrate ships clean against minimal surface area. T3b
#   queued for the integration pass which touches the more complex
#   AuditChain.verify() walk.
#
# What's NOT in T3a (queued):
#   T3b: AuditChain.verify(mode='tail') - skips line-by-line walk
#       of sealed segments, defers to verify_sealed_segments() for
#       their Merkle root check, walks only the tail.
#   T4: migration helper.
#   T5: operator runbook + scheduled runner.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/audit_chain_segments.py \
        tests/unit/test_audit_chain_sealing.py \
        dev-tools/commit-bursts/commit-burst301-adr0073-t3a-segment-verifier.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(audit): ADR-0073 T3a - sealed-segment Merkle verifier (B301)

Burst 301. Contained slice of T3 - the segment-level Merkle
verifier substrate. The full AuditChain.verify(mode='tail')
integration is queued as T3b; T3a here ships the building
block in isolation so the diff stays focused.

What ships:

  - audit_chain_segments.py: SegmentVerifyIssue + SegmentVerifyResult
    frozen dataclasses. verify_sealed_segments(index, segment_dir)
    walks index.sealed_segments(), re-reads each file, recomputes
    Merkle root, compares to stored root. Issues accumulate by
    value so the operator sees every problem in one pass; callers
    that want refuse-on-first-issue check result.ok.

    Issue kinds (stable strings for downstream dispatch):
      merkle_mismatch — tamper signal, file edited after seal.
      file_missing   — operator can restore from backup.
      no_root        — sealed-but-no-root schema violation,
                       chase index corruption separately from
                       actual tamper.
      scan_error     — malformed JSON / missing entry_hash;
                       file is broken in a different way than
                       missing.

Tests: test_audit_chain_sealing.py - 7 T3a cases covering clean
verification, tamper detection (merkle_mismatch), missing file,
no_root schema violation, unsealed-skipped, multi-segment mixed
outcome (verified count + per-issue reporting), Issue frozen-ness.

Sandbox-verified: 6 hand-built scenarios trip the right kinds
with the right segments_verified counts.

Queued T3b-T5: AuditChain.verify(mode='tail') integration,
migration helper for monolithic chain, operator runbook +
scheduled sealing runner."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 301 complete - ADR-0073 T3a segment verifier shipped ==="
echo ""
echo "Press any key to close."
read -n 1
