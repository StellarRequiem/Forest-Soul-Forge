#!/usr/bin/env bash
# Burst 44 Tranche 4a:
#   - Promote ADR-0027-am from Proposed -> Accepted (fully shipped T1+T2+T3+T4)
#   - ADR-0021-am T2: constitution.yaml gains derived initiative_level +
#     initiative_ceiling fields. Hash bump for new agents who carry
#     non-default values (every Companion / SW-track agent etc.).
#     Existing agents on disk keep their stored hashes.
#
# Test delta: 1538 -> 1546 passing (+8, 0 regressions).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 44 Tranche 4a — ADR-0027-am Accepted + ADR-0021-am T2 constitution ==="
echo
clean_locks
git add docs/decisions/ADR-0027-amendment-epistemic-metadata.md \
        src/forest_soul_forge/core/constitution.py \
        src/forest_soul_forge/daemon/routers/writes.py \
        src/forest_soul_forge/daemon/routers/preview.py \
        tests/unit/test_constitution.py \
        commit-burst44-tranche4a.command
clean_locks
git status --short
echo
clean_locks
git commit -m "Constitution gains initiative_level + ADR-0027-am Accepted (ADR-0021-am T2)

Two changes in one commit:

1. ADR-0027-am promoted from Proposed to Accepted. All four
   tranches shipped (commits fcd8d2c T1+T2 schema v10->v11 +
   MemoryEntry write/read paths; 24ec62b T3 memory_recall.v1
   epistemic enrichments; fdef95b T4 memory_challenge.v1 tool).
   T7 (operator-driven memory_reclassify.v1) deferred to v0.3 as
   quality-of-life follow-up, not blocking. Status line updated
   on docs/decisions/ADR-0027-amendment-epistemic-metadata.md.

2. ADR-0021-am T2: constitution.yaml gains initiative_level +
   initiative_ceiling derived fields, populated from the genre's
   default_initiative_level + max_initiative_level at birth.

Constitution dataclass (core/constitution.py):
- Two new fields: initiative_level: str = 'L5', initiative_ceiling:
  str = 'L5'. Both default to L5 for back-compat: a constitution
  built without these args keeps v1 'no initiative ceiling'
  behavior.
- canonical_body() includes both fields unconditionally — same
  shape pattern as the v1 'genre' field. Two agents with identical
  role + traits + tools but different initiative postures get
  different constitution hashes, which is correct: their effective
  autonomy posture differs.
- to_yaml() emits the pair conditionally: when at default L5/L5
  the YAML stays byte-identical to pre-amendment artifacts
  (back-compat for callers that don't engage the new mechanism);
  when either is non-default, both surface as a pair so an
  inspector always sees a complete posture.
- build() accepts initiative_level + initiative_ceiling kwargs;
  passes them through to the Constitution.

Birth path (writes.py):
- New _resolve_initiative_posture(genre_engine, role) helper
  reads the genre's default + ceiling. Returns ('L5', 'L5') for
  unclaimed roles (back-compat).
- Threaded through to build_constitution() in /birth + /spawn.

Preview path (preview.py):
- Same threading so /preview's constitution_hash matches what
  /birth would produce.

Tests (test_constitution.py +8 cases in TestInitiativeLadder):
- default_l5_l5_when_no_genre: dataclass defaults preserve back-compat.
- explicit_initiative_round_trips: kwargs land on the dataclass.
- canonical_body_includes_both_fields: hash shape stable.
- hash_changes_when_initiative_level_changes: level is policy.
- hash_changes_when_ceiling_changes: ceiling is policy.
- to_yaml_emits_initiative_pair_when_non_default: inspector sees
  'initiative_level: L1' + 'initiative_ceiling: L2' on Companion.
- to_yaml_omits_when_l5_l5_back_compat: pre-amendment YAML
  byte-identical for L5/L5 case.
- to_yaml_emits_pair_when_only_one_non_default: pair-or-nothing
  rule — inspector never sees a half-pair.

Test delta: 1538 -> 1546 passing (+8). Zero regressions.

Hash impact: every NEW Companion / Observer / SW-track / etc. born
post-amendment carries an initiative-aware constitution_hash that
differs from a pre-amendment Companion of identical traits + tools.
Existing agents in registries keep their stored hashes (constitution
isn't auto-re-derived; hash is birth-time content-addressing per
ADR-0001). The L5/L5 back-compat default means an unclaimed-role
agent gets an unchanged hash relative to pre-amendment.

ADR-0021-am T3 (InitiativeFloorStep dispatcher) follows in next
tranche; it consumes the initiative_level field this commit lands."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Tranche 4a landed. ADR-0027-am Accepted. Constitution carries initiative."
echo ""
read -rp "Press Enter to close..."
