#!/bin/bash
# Burst 304 - ADR-0073 T4: chain migration helper.
#
# One-shot operator action: split an existing monolithic
# audit_chain.jsonl into the per-month segment files + bootstrap
# the segment index. The pre-migration chain stays byte-
# identical so operators can roll back.
#
# What ships:
#
# 1. src/forest_soul_forge/core/audit_chain_segments.py:
#    - MigrationError exception.
#    - MigrationOutcome frozen dataclass (segments_created,
#      entries_written per month, files tuple, new_index).
#    - migrate_monolithic_chain(*, source_path, segment_dir,
#      overwrite=False):
#        * Reads source line by line, parses each entry, groups
#          by YYYY-MM from the timestamp field.
#        * Pre-flight overwrite check: refuses if any target
#          segment file already exists (unless overwrite=True).
#          Protects an already-migrated operator from re-running.
#        * Writes one segment file per month (canonical
#          audit_chain_YYYY-MM.jsonl naming).
#        * Builds a SegmentIndex with every month-segment
#          sealed=True (with merkle_root computed) EXCEPT the
#          most recent month, which is the new tail (sealed=False,
#          merkle_root=None).
#        * Pure: doesn't modify source. Operator's rollback path
#          is intact.
#    - _month_from_iso helper for the timestamp -> 'YYYY-MM'
#      extraction.
#
# 2. tests/unit/test_audit_chain_sealing.py - 9 new T4 cases:
#    Happy path:
#      - splits 3-month chain into 3 segment files
#      - last segment marked as tail (sealed=False)
#      - source file stays byte-identical (rollback intact)
#      - migration output passes verify_sealed_segments (the
#        round-trip integration check)
#    Overwrite handling:
#      - default refuses second run
#      - overwrite=True allows re-migration
#    Error paths:
#      - missing source raises
#      - malformed JSON raises
#      - missing required field (seq/timestamp/entry_hash) raises
#
# Sandbox-verified all 9 scenarios end-to-end, including the
# migration -> verify_sealed_segments round-trip.
#
# What's NOT in T4 (queued):
#   T3b: AuditChain.verify(mode='tail') integration that uses
#       the migrated index to skip sealed-segment line walks.
#   T5: operator runbook documenting the migration command +
#       rollback procedure + scheduled-runner wiring (the
#       monthly seal_segment() pass).

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/audit_chain_segments.py \
        tests/unit/test_audit_chain_sealing.py \
        dev-tools/commit-bursts/commit-burst304-adr0073-t4-migration.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(audit): ADR-0073 T4 - chain migration helper (B304)

Burst 304. One-shot operator action: split an existing
monolithic audit_chain.jsonl into per-month segment files +
bootstrap the segment index. Pre-migration chain stays byte-
identical for rollback.

What ships:

  - audit_chain_segments.py: MigrationError exception,
    MigrationOutcome frozen dataclass (segments_created,
    entries_written per month, files tuple, new_index).

    migrate_monolithic_chain(*, source_path, segment_dir,
    overwrite=False): reads source line by line, parses each
    entry, groups by YYYY-MM from timestamp. Pre-flight
    overwrite check refuses if any target segment file already
    exists (unless overwrite=True). Writes one segment file per
    month, builds SegmentIndex with every month-segment
    sealed=True (with merkle_root) EXCEPT the most recent which
    is the new tail (sealed=False, merkle_root=None). Pure
    function over the source — doesnt modify it, operator's
    rollback path stays intact.

Tests: test_audit_chain_sealing.py - 9 new cases covering
3-month split, tail allocation, source byte-identity preservation,
migration→verify_sealed_segments round-trip clean, overwrite
refusal by default + overwrite=True force, missing source +
malformed JSON + missing-field errors.

Sandbox-verified all 9 scenarios end-to-end.

Queued T3b + T5: AuditChain.verify(mode='tail') integration +
operator runbook + scheduled monthly sealing runner.

ADR-0073 progress: T1 + T2 + T3a + T4 shipped; T3b + T5
queued. Closes the migration path so existing operators can
upgrade to the segment substrate without touching the source
chain."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 304 complete - ADR-0073 T4 migration helper shipped ==="
echo ""
echo "Press any key to close."
read -n 1
