#!/bin/bash
# Burst 291 — ADR-0073 T1: audit chain segmentation substrate.
#
# Scale substrate. Splits the monolithic audit chain into monthly
# files + an index + anchor entries. Sized for the 10K-100K
# events/day the ten-domain platform will produce once telemetry
# streams come online (queued ADR-0064).
#
# Why segmentation:
#   - per-dispatch verify cost drops from O(chain) to O(segment)
#   - encryption rotation (ADR-0050 T8) becomes incremental
#   - sealed segments can be archived off-disk without losing
#     verifiability (merkle_root in the index stays as proof)
#
# What ships:
#
# 1. docs/decisions/ADR-0073-audit-chain-segmentation.md — full
#    record. Four decisions:
#      D1 Monthly segment files (audit_chain_YYYY-MM.jsonl) + index
#      D2 audit_chain_anchor entries bridge segments (prev_hash
#         references prior segment's last entry_hash; carries
#         prior_merkle_root for fast verify)
#      D3 Two verify modes: tail (current segment + anchor sigs;
#         O(segment_size)) vs full (every segment; O(chain_size))
#      D4 Sealed segments lazy-loaded; rotation works segment-by-
#         segment
#    Five tranches T1-T5.
#
# 2. src/forest_soul_forge/core/audit_chain_segments.py:
#    - SegmentMeta frozen dataclass (seq_start / seq_end / file /
#      month / sealed / merkle_root)
#    - SegmentIndex container with current() / for_seq(seq) /
#      sealed_segments() helpers
#    - AnchorPayload frozen dataclass (operator-readable seal
#      provenance)
#    - load_segment_index — missing file → empty index (benign);
#      structural failures hard-raise
#    - save_segment_index — atomic via .tmp + rename
#    - merkle_root(hashes) — standard binary Merkle (odd count
#      duplicates last; empty input → sha256 of empty bytes)
#    - segment_filename_for_month + current_segment_month helpers
#    - append_segment_entry — thin file-append wrapper with
#      line-termination normalization
#
# 3. src/forest_soul_forge/core/audit_chain.py: register
#    audit_chain_anchor event type. Verifier accepts the new type
#    so anchors don't trip the KNOWN_EVENT_TYPES check.
#
# Tests (test_audit_chain_segments.py — 18 cases):
#   load/save round-trip:
#     - missing file → empty index
#     - save + load preserves all fields including merkle_root
#   Loader failures:
#     - malformed JSON / schema mismatch / non-object top-level /
#       missing required fields all raise SegmentIndexError
#   SegmentIndex methods:
#     - current() finds the unsealed segment
#     - current() returns None on empty
#     - for_seq() routes to sealed AND tail
#     - for_seq() returns None when seq below first segment
#     - sealed_segments() filters correctly
#   Merkle root:
#     - empty input → sha256 of empty bytes
#     - single-hash returns the hash itself
#     - two hashes verified against manual sha256 computation
#     - odd count duplicates last (3-hash case verified manually)
#     - deterministic
#   Helpers:
#     - segment_filename_for_month convention
#     - current_segment_month format (YYYY-MM)
#     - append_segment_entry adds newline + doesn't double
#   Audit event registered.
#
# What's NOT in T1 (queued):
#   T2: sealing flow — when a new month starts, freeze the prior
#       segment + emit audit_chain_anchor entry + compute merkle_root
#   T3: audit_chain_verify.v1 extended with mode=tail / mode=full
#   T4: one-shot migration helper (monolithic chain → segmented)
#   T5: operator runbook + scaling characterization at 1M / 10M /
#       100M entries

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0073-audit-chain-segmentation.md \
        src/forest_soul_forge/core/audit_chain_segments.py \
        src/forest_soul_forge/core/audit_chain.py \
        tests/unit/test_audit_chain_segments.py \
        dev-tools/commit-bursts/commit-burst291-adr0073-t1-chain-segments.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(scale): ADR-0073 T1 — audit chain segmentation (B291)

Burst 291. Scale substrate. Splits the monolithic audit chain
into monthly files + an index + anchor entries that bridge
segments. Sized for the 10K-100K events/day projected once the
ten-domain platform + ADR-0064 telemetry pipeline come online.

What ships:

  - ADR-0073 full record. Four decisions: monthly segment files,
    anchor entries with Merkle roots, two verify modes (tail
    O(segment) vs. full O(chain)), lazy-loaded sealed segments
    enabling incremental rotation. Five tranches T1-T5.

  - core/audit_chain_segments.py: SegmentMeta + SegmentIndex +
    AnchorPayload frozen dataclasses. load_segment_index (missing
    file → empty; structural failures hard). save_segment_index
    atomic via .tmp + rename. merkle_root standard binary
    (odd count duplicates last; empty → sha256(b'')).
    segment_filename_for_month + current_segment_month +
    append_segment_entry helpers. SegmentIndex.current() /
    for_seq(seq) / sealed_segments() navigation.

  - core/audit_chain.py: register audit_chain_anchor in
    KNOWN_EVENT_TYPES. Verifier accepts the new type so anchors
    don't trip KNOWN_EVENT_TYPES drift.

Tests: test_audit_chain_segments.py — 18 cases covering loader
happy + failure modes, save round-trip, index navigation
helpers, Merkle root edge cases (empty / single / pair / odd /
deterministic), filename + month-string conventions, append
helper line-termination, audit event registration.

Queued T2-T5: sealing flow, verify-mode extension, one-shot
migration, runbook + scaling characterization."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 291 complete — ADR-0073 T1 segmentation substrate shipped ==="
echo "Next: T2 sealing flow OR pivot to other scale substrate ADRs."
echo ""
echo "Press any key to close."
read -n 1
