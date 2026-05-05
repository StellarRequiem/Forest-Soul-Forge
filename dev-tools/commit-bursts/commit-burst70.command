#!/usr/bin/env bash
# Burst 70: ADR-0036 T6+T7 — flagged_state column + recall surface extension.
#
# Closes the lifecycle polish on top of the auto-detection pipeline.
# Operators can now ratify Verifier flags through a four-state
# lifecycle, and the recall surface stops surfacing rejected flags
# by default.
#
# After this burst, ADR-0036 is feature-complete (modulo T4 scheduled-
# task substrate, deferred with scope note in Burst 69).
#
# Test delta: 2058 -> 2072 passing (+14).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 70 — ADR-0036 T6+T7 flagged_state lifecycle ==="
echo
clean_locks
git add src/forest_soul_forge/registry/schema.py \
        src/forest_soul_forge/core/memory.py \
        tests/unit/test_memory_flagged_state.py \
        tests/unit/test_registry.py \
        tests/unit/test_daemon_readonly.py \
        commit-burst70.command
clean_locks
git status --short
echo
clean_locks
git commit -m "ADR-0036 T6+T7: flagged_state column + recall surface extension

Schema v11 -> v12 migration: adds the flagged_state column to
memory_contradictions with a four-value CHECK enum
{flagged_unreviewed, flagged_confirmed, flagged_rejected,
auto_resolved}. Operators ratify Verifier flags through this
lifecycle; the recall surface stops surfacing rejected flags by
default so a known-false flag stops surfacing on every recall.

After this burst, ADR-0036 is feature-complete (modulo T4
per-Verifier scheduled cron, which was deferred in Burst 69 with a
scope note — needs its own scheduled-task substrate ADR).

T6 — schema migration:
- SCHEMA_VERSION bumped 11 -> 12.
- Bootstrap DDL extends memory_contradictions with flagged_state
  TEXT NOT NULL DEFAULT 'flagged_unreviewed' CHECK (...).
- Forward migration (MIGRATIONS[12]) is pure additive ALTER TABLE
  ADD COLUMN with the same default — old v11 rows land at
  flagged_unreviewed (the right semantic, pre-T6 contradictions
  weren't reviewed yet).
- New partial index idx_contradictions_state filters
  flagged_unreviewed rows only — the operator review surface
  (ADR-0037 dashboard) hits this path often enough to warrant
  a covering index.

T7 — recall surface + writer + ratification path:
- memory.flag_contradiction now sets flagged_state explicitly to
  'flagged_unreviewed' on insert. Try/except handles v11-shape
  in-memory test DBs that haven't migrated.
- New memory.set_contradiction_state(contradiction_id, new_state) ->
  bool — operator ratification path. Validates against
  VALID_FLAGGED_STATES, returns True on success, False on unknown
  id, raises ValueError on bogus state.
- memory.unresolved_contradictions_for now surfaces flagged_state
  in each row dict and filters flagged_rejected by default. New
  include_rejected=True kwarg overrides for operator review +
  audit-trail queries.
- Two-tier query fallback handles v11-shape DBs (no flagged_state
  column) by falling back to the v11 query and stamping
  'flagged_unreviewed' synthetically on each row — keeps the API
  shape stable.

Tests (test_memory_flagged_state.py +14 cases):
- TestSchemaV12 (3): SCHEMA_VERSION constant; fresh DB has the
  column; CHECK constraint rejects bogus states.
- TestFlagDefault (1): new flag_contradiction lands at
  flagged_unreviewed.
- TestSetState (5): unreviewed -> confirmed; unreviewed -> rejected;
  invalid state raises ValueError; unknown id returns False; all
  four valid states accepted (round-trip lifecycle test).
- TestRecallSurface (5): default filters rejected; include_rejected
  surfaces them; unreviewed surfaces by default; confirmed
  surfaces; auto_resolved surfaces (system-driven state, not a
  rejection so it surfaces).

Test pin updates:
- test_registry.py: 6 assertions updated 11 -> 12.
- test_daemon_readonly.py: schema_version comment refreshed +
  literal updated 11 -> 12.

Test delta: 2058 -> 2072 passing (+14). Zero regressions.

Schema version: v11 -> v12.

ADR-0036 status after this burst:
- T1 (verifier_loop role)            shipped 4b743f7
- T2 (memory_flag_contradiction.v1)  shipped 62125b1
- T3a (find_candidate_pairs)         shipped 11d7788
- T3b (VerifierScan runner)          shipped 7b3df28
- T4 (per-Verifier scheduled cron)   DEFERRED — needs scheduled-
                                                 task substrate
                                                 ADR-grade work
- T5 (/verifier/scan endpoint)       shipped e77ab32
- T6 (schema v12 flagged_state)      shipped this burst
- T7 (recall surface extension)      shipped this burst

Verifier auto-detection + ratification dial complete. Operators can
trigger scans (T5), see flags surface in recall (T7), and ratify
through the four-state lifecycle (T6). Cross-agent scans, full
write-time auto-detection, and embedding-based pre-filter remain
v0.4 candidates per ADR-0036 'rejected alternatives' + open questions."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 70 landed. ADR-0036 T6+T7 in production. Lifecycle complete."
echo "ADR-0036 feature-complete (modulo deferred T4 scheduled cron)."
echo "Next: take stock, or pivot — ADR-0035 Persona Forge / ADR-0037 Observability"
echo "      / Phase G.1.B web tools / etc. Operator's call."
echo ""
read -rp "Press Enter to close..."
