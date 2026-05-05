#!/usr/bin/env bash
# Burst 43 Tranche 3b: memory_recall.v1 epistemic enrichments (ADR-0027-am T3).
#
# memory_recall.v1 always surfaces claim_type / confidence /
# last_challenged_at on every entry (zero behavior change for callers
# that don't read them). New optional parameters:
#   - surface_contradictions: bool — attaches unresolved contradictions
#   - staleness_threshold_days: int — flags is_stale per entry
# K1 verification fold: verified entries surface as confidence=high.
#
# Test delta: 1512 -> 1521 passing (+9, 0 regressions).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 43 Tranche 3b — memory_recall.v1 epistemic enrichments ==="
echo
clean_locks
git add src/forest_soul_forge/core/memory.py \
        src/forest_soul_forge/tools/builtin/memory_recall.py \
        tests/unit/test_memory_recall_tool.py \
        commit-burst43-tranche3b.command
clean_locks
git status --short
echo
clean_locks
git commit -m "memory_recall.v1 epistemic enrichments (ADR-0027-am T3)

ADR-0027-amendment §7.3 + §7.4 + §7.6 — read-side surfacing of the
v11 epistemic metadata that landed in Tranche 2 (commit fcd8d2c).

Memory class additions (core/memory.py):
- unresolved_contradictions_for(entry_id) -> list[dict]
  Returns open contradictions where the entry is earlier or later
  side. Defensive: returns [] on v10-shape DB without the table.
- is_entry_stale(entry, threshold_days, now_iso=None) -> bool
  Computes staleness: True iff last_challenged_at (or created_at as
  fallback) is older than threshold_days. ISO-8601 lexicographic
  compare; tolerates both T-separator and space-separator timestamp
  formats. now_iso injectable for deterministic testing.

memory_recall.v1 (tools/builtin/memory_recall.py):
- Every entry in output gains claim_type / confidence /
  last_challenged_at (always surfaced; zero behavior change for
  callers that don't read these keys).
- ADR-0027-am §7.6 K1 fold: when memory_verifications has an
  active grant for the entry, confidence surfaces as 'high'
  regardless of stored value. Stored confidence unchanged in DB.
- New optional param surface_contradictions: bool. When True,
  every entry includes unresolved_contradictions: list[dict] of
  open contradictions. metadata.contradicted_count counts entries
  with any open contradiction.
- New optional param staleness_threshold_days: int (>= 1). When
  set, every entry includes is_stale: bool. metadata.stale_count
  counts stale entries; metadata.staleness_threshold_days echoes
  the threshold for inspection.
- Validation rejects: non-bool surface_contradictions, non-int /
  bool / float / negative / zero staleness_threshold_days.

Tests (test_memory_recall_tool.py +9 cases in TestEpistemicSurfaces):
- test_default_fields_always_surfaced: defaults match schema
  CHECK column DEFAULTs; optional keys absent without params.
- test_explicit_claim_type_round_trips_through_recall: agent_inference
  + low confidence preserved end-to-end.
- test_k1_verification_promotes_confidence_to_high: K1 fold in
  effect at recall time; stored value unchanged.
- test_surface_contradictions_attaches_open_conflicts: open
  contradictions surface on both earlier + later entries; metadata
  count correct.
- test_resolved_contradictions_not_surfaced: resolved (non-NULL
  resolved_at) excluded; recall only shows what's still open.
- test_staleness_threshold_flags_old_entries: 30-day threshold
  fires on entry backdated to 2020.
- test_staleness_threshold_does_not_flag_fresh_entries: just-
  created entry inside the 365-day window not flagged.
- test_invalid_surface_contradictions_type_rejected: non-bool fails.
- test_invalid_staleness_threshold_rejected: 0, negative, float,
  bool all fail validation.

Test delta: 1512 -> 1521 passing (+9). Zero regressions.

ADR-0027-am T3 status: implemented. Per-claim-type defaults for
surface_contradictions / staleness_threshold_days (the §7.3 +
§7.4 default behaviors keyed on claim_type) deferred to v0.3 —
the v0.2 surface is operator/caller-driven uniformly."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Tranche 3b landed. Continue with memory_challenge.v1 (Tranche 3c)."
echo ""
read -rp "Press Enter to close..."
