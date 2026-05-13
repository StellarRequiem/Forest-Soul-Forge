#!/bin/bash
# Burst 255 — ADR-0063 T6: correction memory + recurrence.
#
# Closes the operator-facing answer to "which agents keep
# making the same wrong claim?" without walking the audit
# chain manually. Schema v20 adds reality_anchor_corrections;
# both T3 (dispatcher) and T5 (conversation) surfaces bump
# it on every contradicted finding and emit
# reality_anchor_repeat_offender when the same claim recurs.
#
# Files:
#
# 1. src/forest_soul_forge/registry/schema.py
#    SCHEMA_VERSION 19 → 20. New CREATE TABLE
#    reality_anchor_corrections in DDL_STATEMENTS + new
#    MIGRATIONS[20] mirroring it for in-place upgrades. Three
#    indexes (fact_id, agent_dna, count DESC). Added to
#    REBUILD_TRUNCATE_ORDER so rebuild-from-artifacts clears
#    the runtime governance state.
#
# 2. src/forest_soul_forge/registry/tables/reality_anchor_corrections.py (NEW)
#    RealityAnchorCorrectionsTable accessor. Public surface:
#      normalize_claim(s) → str          # lowercase + collapse ws + trim
#      claim_hash(s) → sha256 hex
#      table.bump_or_create(...) → int   # post-bump count
#      table.get(claim) → CorrectionRow | None
#      table.get_by_hash(hash_hex)       # same
#      table.list_repeat_offenders(min_repetitions=2, limit=100)
#    CorrectionRow dataclass (frozen) for stable read shape.
#    Worst-severity escalation: claim that started LOW becomes
#    CRITICAL on next bump if severity rises; never de-escalates.
#
# 3. src/forest_soul_forge/registry/registry.py
#    self.reality_anchor_corrections = RealityAnchorCorrectionsTable(conn).
#
# 4. src/forest_soul_forge/core/audit_chain.py
#    KNOWN_EVENT_TYPES += reality_anchor_repeat_offender.
#
# 5. src/forest_soul_forge/tools/governance_pipeline.py
#    RealityAnchorStep gains optional corrections_bump_fn.
#    New helper _maybe_emit_repeat_offender called after every
#    refuse/flag emission. surface='dispatcher'.
#
# 6. src/forest_soul_forge/tools/dispatcher.py
#    Pipeline construction passes _bump_anchor_correction
#    (new ToolDispatcher method) as the closure. None-safe
#    so test contexts without a registry degrade to no
#    recurrence detection.
#
# 7. src/forest_soul_forge/daemon/reality_anchor_turn.py
#    check_turn_against_anchor gains optional corrections_table
#    param. New helper _maybe_emit_turn_repeat_offender fires
#    with surface='conversation' so chain queries separate the
#    two surfaces cleanly.
#
# 8. src/forest_soul_forge/daemon/routers/conversations.py
#    Passes registry.reality_anchor_corrections through to the
#    hook call.
#
# 9. tests/unit/test_reality_anchor_corrections.py (NEW)
#    25+ tests: normalization (lowercase, whitespace, trim,
#    empty), hash stability across case + whitespace, hash
#    distinctness, bump_or_create (first sighting, repeat,
#    case variants, distinct claims, severity escalation,
#    severity non-de-escalation, first_seen preserved while
#    last_* fields overwrite), reads (miss → None, hit →
#    dataclass, list_repeat_offenders default + min param +
#    ordering), fresh-install table presence.
#
# 10. tests/unit/test_plugin_grants.py
#     test_schema_version_is_19 → test_schema_version_is_20.
#     Comment updated to track v19 (B243) + v20 (B255) bumps.
#
# 11. tests/unit/test_procedural_shortcuts.py
#     Same rename + comment update.
#
# 12. tests/unit/test_registry.py
#     6 occurrences of schema_version() == 19 → == 20.
#     (sed-applied; no semantic change beyond the literal.)
#
# 13. tests/unit/test_daemon_readonly.py
#     1 occurrence of body['schema_version'] == 19 → == 20.
#
# 14. docs/decisions/ADR-0063-reality-anchor.md
#     Status: T1+T2+T3+T4+T5+T6 shipped. T6 row marked
#     DONE B255 with full implementation detail. Only T7
#     (SoulUX pane) remains.
#
# Sandbox smoke (5 scenarios via standalone driver):
#   1. first sighting → count=1 ✓
#   2. exact repeat → count=2 ✓
#   3. case+whitespace variant of repeat → count=3 ✓
#   4. distinct claim → fresh count=1 ✓
#   5. list_repeat_offenders(min=2) → 1 row with count=3 ✓
#
# Per ADR-0063 D7: PRIMARY KEY on sha256(normalized claim)
#   so the same hallucination caught twice is recognized as
#   recurrent. ON CONFLICT DO UPDATE makes the bump atomic.
# Per CLAUDE.md §0 Hippocratic gate: any bump failure
#   degrades to "no recurrence event for this call"; the
#   gate's primary refuse/flag decision is the load-bearing
#   safety output.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/registry/schema.py \
        src/forest_soul_forge/registry/tables/reality_anchor_corrections.py \
        src/forest_soul_forge/registry/registry.py \
        src/forest_soul_forge/core/audit_chain.py \
        src/forest_soul_forge/tools/governance_pipeline.py \
        src/forest_soul_forge/tools/dispatcher.py \
        src/forest_soul_forge/daemon/reality_anchor_turn.py \
        src/forest_soul_forge/daemon/routers/conversations.py \
        tests/unit/test_reality_anchor_corrections.py \
        tests/unit/test_plugin_grants.py \
        tests/unit/test_procedural_shortcuts.py \
        tests/unit/test_registry.py \
        tests/unit/test_daemon_readonly.py \
        docs/decisions/ADR-0063-reality-anchor.md \
        dev-tools/commit-bursts/commit-burst255-adr0063-t6-corrections.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(reality-anchor): ADR-0063 T6 correction memory + recurrence (B255)

Burst 255. Closes the operator-facing answer to 'which agents
keep making the same wrong claim?' without walking the audit
chain manually.

Schema v20 (19 → 20) adds reality_anchor_corrections:
PRIMARY KEY claim_hash = sha256 of the normalized claim text
(lowercase + collapse whitespace + trim). bump_or_create is
an idempotent upsert via ON CONFLICT DO UPDATE; returns the
post-bump repetition_count. Worst-severity escalates only —
a claim that started LOW becomes CRITICAL if a later sighting
raises severity; never de-escalates.

Wired into both surfaces:
- RealityAnchorStep (dispatcher) gets corrections_bump_fn
  closure from the dispatcher's _bump_anchor_correction
  method. surface='dispatcher'.
- check_turn_against_anchor (conversation) gets corrections_table
  param threaded through routers/conversations.py.
  surface='conversation'.

When the post-bump count > 1, both surfaces fire
reality_anchor_repeat_offender alongside the per-event
refused/flagged emission. Distinct event type added to
KNOWN_EVENT_TYPES.

All bump failures degrade silently — corrections memory is
NOT load-bearing for the gate's primary refuse/flag decision.

Tests: 25+ in test_reality_anchor_corrections.py covering
normalization, hash stability, bump/severity escalation,
reads, fresh-install table presence. Schema version
assertions in test_plugin_grants, test_procedural_shortcuts,
test_registry, test_daemon_readonly all bumped 19 → 20.

ADR-0063 status: T1+T2+T3+T4+T5+T6 shipped. T7 (SoulUX
Reality Anchor pane) is the final tranche.

Per CLAUDE.md §0 Hippocratic gate: bump-or-fire is best-
effort; primary refuse/flag verdict is the load-bearing
safety output."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 255 complete ==="
echo "=== ADR-0063 T6 live. Repeat-offender detection active. ==="
echo "Press any key to close."
read -n 1
