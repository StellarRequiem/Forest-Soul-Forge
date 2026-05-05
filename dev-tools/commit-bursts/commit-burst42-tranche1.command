#!/usr/bin/env bash
# Burst 42 Tranche 1: Genre engine — min_trait_floors + initiative ladder.
#
# Implements ADR-0038 T1 (min_trait_floors enforcement at birth) and
# ADR-0021-amendment §2 T1 (max_initiative_level + default_initiative_level
# fields on genres). All 13 genres in genres.yaml gain the new fields per
# ADR-0021-am §3 mapping; Companion gains H-1-mitigation floors per
# ADR-0038 §3.
#
# Test delta: 1434 -> 1466 passing (+32, 0 regressions).
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

echo "=== Burst 42 Tranche 1 — genre engine extensions ==="
echo
clean_locks
echo "step 1/4 — staging..."
git add config/genres.yaml \
        docs/decisions/ADR-0038-companion-harm-model.md \
        src/forest_soul_forge/core/genre_engine.py \
        src/forest_soul_forge/daemon/routers/writes.py \
        tests/unit/test_genre_engine.py \
        tests/unit/test_daemon_writes.py \
        commit-burst42-tranche1.command
clean_locks
git status --short
echo
echo "step 2/4 — commit..."
clean_locks
git commit -m "Genre engine — min_trait_floors + initiative ladder (ADR-0038 T1, ADR-0021-am T1)

Implements two of the three Proposed ADRs from the SarahR1 review absorption
(commit 889e362). Both target the same surface (genres.yaml + genre_engine.py
loader + writes.py birth-path enforcement) so they land together.

ADR-0038 T1 — min_trait_floors:
- New per-genre map of trait_name -> integer floor [0, 100].
- Companion declares evidence_demand >= 50, transparency >= 60 (H-1
  sycophancy mitigation; matches the trait engine's integer scale).
- Birth-time enforcement in writes.py via _enforce_genre_trait_floors;
  refuses with 400 listing every violation in one error.
- Loader rejects: non-int values, bools (isinstance(True,int) gotcha),
  floats, out-of-[0,100] values, non-dict shape.

ADR-0021-am T1 — max_initiative_level + default_initiative_level:
- L0-L5 ladder orthogonal to existing max_side_effects.
- All 13 genres pinned per ADR-0021-am §3 table:
    observer L3/L3, investigator L4/L3, communicator L3/L2,
    actuator L5/L5, guardian L3/L3, researcher L4/L3, companion L2/L1,
    security_low L3/L3, security_mid L4/L3, security_high L4/L3,
    web_observer L3/L3, web_researcher L4/L3, web_actuator L5/L5.
- Loader validates: level value is in {L0..L5}, default <= max.
- Helper initiative_exceeds_ceiling() and _initiative_index() symmetric
  to existing memory_scope_exceeds_ceiling().
- Birth-time enforcement deferred to ADR-0021-am T2/T3 (constitution
  derived fields + InitiativeFloorStep dispatcher integration).

Tests:
- test_genre_engine.py +27 cases across TestMinTraitFloors (8),
  TestMinTraitFloorsParse (6), TestInitiativeLadder (7),
  TestInitiativeLadderParse (6).
- test_daemon_writes.py +5 cases in TestGenreTraitFloors covering:
  Companion default traits pass; below evidence_demand floor refuses;
  below transparency floor refuses; both-low lists both; Observer
  unaffected (no floor declared).

Existing 13-genre catalog passes the new loader. No existing test
broke. Companion default traits (85, 85) sit comfortably above the
50/60 floors so existing Companion tests are unaffected.

Documentation: ADR-0038 §3 example floor values changed 0.5/0.6 ->
50/60 to match the trait engine's integer-in-[0,100] scale (caught
during implementation; floats are now explicitly rejected at load).

Test delta: 1434 -> 1466 passing (+32). Zero regressions.

ADR statuses unchanged: still Proposed. Promotion to Accepted will
land in a separate commit after Tranches 2 + 3 (schema v10->v11 +
MemoryEntry epistemic fields)."

clean_locks
echo
echo "step 3/4 — push..."
git push origin main
clean_locks
echo
echo "step 4/4 — final state"
git log -1 --oneline
echo
echo "Tranche 1 landed. Continue with schema v10->v11 (Tranche 2)."
echo ""
read -rp "Press Enter to close..."
