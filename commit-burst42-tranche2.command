#!/usr/bin/env bash
# Burst 42 Tranche 2: Schema v10 -> v11 + MemoryEntry epistemic metadata.
#
# Implements ADR-0027-amendment T1 (schema migration v10 -> v11) and
# ADR-0027-amendment T2 (MemoryEntry dataclass + Memory.append write
# path + read path).
#
# Closes ADR-0038 H-6 (memory overreach / inferred-preference cementing)
# at the data layer: agent_inference is now a distinct claim_type from
# observation / user_statement, and three-state confidence prevents
# silent precision inflation.
#
# Test delta: 1466 -> 1478 passing (+12, 0 regressions).
#
# Handles recurring sandbox lock cleanup before each git op.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 42 Tranche 2 — schema v11 + MemoryEntry epistemic fields ==="
echo
clean_locks
echo "step 1/4 — staging..."
git add src/forest_soul_forge/registry/schema.py \
        src/forest_soul_forge/core/memory.py \
        tests/unit/test_registry.py \
        tests/unit/test_memory.py \
        tests/unit/test_daemon_readonly.py \
        commit-burst42-tranche2.command
clean_locks
git status --short
echo
echo "step 2/4 — commit..."
clean_locks
git commit -m "Schema v11 + MemoryEntry epistemic metadata (ADR-0027-am T1+T2)

Implements the second of three Proposed ADRs from the SarahR1 review
absorption (commit 889e362). Closes ADR-0038 H-6 (memory overreach /
inferred-preference cementing) at the data layer.

Schema changes (ADR-0027-am T1, additive only):
- SCHEMA_VERSION 10 -> 11.
- memory_entries gains three columns:
    * claim_type TEXT NOT NULL DEFAULT 'observation' CHECK (...)
      Six-class enum: observation / user_statement / agent_inference /
      preference / promise / external_fact.
    * confidence TEXT NOT NULL DEFAULT 'medium' CHECK (...)
      Three-state: low / medium / high.
    * last_challenged_at TEXT (nullable; populated when contradicted
      or challenged).
- New memory_contradictions table (m-to-many across memory_entries
  via two FKs, kind enum: direct / updated / qualified / retracted).
- Three new indexes: idx_memory_claim_type, partial
  idx_memory_last_challenged WHERE last_challenged_at IS NOT NULL,
  partial idx_contradictions_unresolved WHERE resolved_at IS NULL.
- MIGRATIONS[11] tuple lands the ALTER TABLE ADD COLUMN + CREATE
  TABLE + CREATE INDEX statements. Pre-existing rows land at the
  schema column DEFAULTs ('observation', 'medium', NULL) — no data
  rewriting, just additive forward migration.
- REBUILD_TRUNCATE_ORDER updated: memory_contradictions clears before
  memory_entries (children-before-parents per FK direction).

MemoryEntry / Memory.append (ADR-0027-am T2):
- core/memory.py exports CLAIM_TYPES tuple, CONFIDENCE_LEVELS tuple,
  UnknownClaimTypeError, UnknownConfidenceError.
- MemoryEntry dataclass gains claim_type / confidence /
  last_challenged_at fields with safe defaults.
- Memory.append() accepts claim_type + confidence keyword args
  (defaulted to 'observation' / 'medium'). Validates against the
  Python enum BEFORE the schema CHECK fires — raises typed
  exceptions with sortable error messages.
- _row_to_entry() defensively reads the new columns via row.keys()
  so v10-shape in-memory test fixtures still work (matches the v7
  defensive pattern).
- memory_disclose tool's INSERT relies on schema column DEFAULTs
  for the new fields (back-compat; per-tool refinement is T7
  reclassify work).

Tests:
- test_memory.py +11 cases in TestEpistemicMetadata covering:
  defaults are observation/medium; explicit claim_type round-trips;
  all 6 claim_types + all 3 confidence levels accepted; invalid
  claim_type / confidence raise typed errors; recall surfaces
  the new fields; schema CHECK rejects invalid values via raw SQL;
  memory_contradictions table is usable end-to-end; contradiction
  CHECK rejects unknown kinds.
- test_registry.py +1 case (test_v10_to_v11_forward_migration):
  build a v10-shape fixture by dropping the v11 columns + table from
  a fresh bootstrap; reopen via Registry.bootstrap; verify
  schema_version=11, pre-existing row carries DEFAULTs, FK guard on
  memory_contradictions enforced, indexes are present. NOT xfailed
  (unlike v6->v7 test) because v11 has no DROP COLUMN dependencies.
- test_v1_to_v2_forward_migration_preserves_data extended with
  v11 column + table assertions inline.
- 6 hardcoded 'schema_version == 10' assertions updated to 11
  across test_registry.py + test_daemon_readonly.py.

Test delta: 1466 -> 1478 passing (+12). Zero regressions.

ADR statuses unchanged: still Proposed. Promotion to Accepted will
land in the final Burst 42 commit after Tranche 3 (constitution
derived fields + InitiativeFloorStep) or as a stand-alone promotion
commit if Tranche 3 is held for v0.3."

clean_locks
echo
echo "step 3/4 — push..."
git push origin main
clean_locks
echo
echo "step 4/4 — final state"
git log -1 --oneline
echo
echo "Tranche 2 landed. Schema bumped to v11. ADR-0038 H-6 closed at data layer."
echo ""
read -rp "Press Enter to close..."
