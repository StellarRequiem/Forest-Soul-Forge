#!/bin/bash
# Burst 294 - ADR-0074 T1: memory consolidation substrate (schema v23).
#
# Phase alpha closer. Last untouched scale ADR. ADR-0022 (memory
# subsystem) declared the three-layer working/episodic/consolidated
# lifecycle but never shipped the consolidation step; at ten-domain
# scale memory_entries grows unbounded (~300-1000 entries per
# active day) so the rollup substrate becomes load-bearing.
#
# What ships:
#
# 1. docs/decisions/ADR-0074-memory-consolidation.md - full record.
#    Four decisions:
#      D1 consolidation_state TEXT 5-state enum on memory_entries
#         (pending | consolidated | summary | pinned | purged)
#         with CHECK constraint pinning the enum.
#      D2 consolidated_into TEXT self-FK to memory_entries.entry_id
#         (NULL except when state=consolidated).
#      D3 consolidation_run TEXT for run-UUID traceability.
#      D4 Three audit event types: run_started + memory_consolidated
#         (per-entry) + run_completed.
#    Five tranches T1-T5.
#
# 2. src/forest_soul_forge/registry/schema.py:
#    - SCHEMA_VERSION 22 -> 23.
#    - DDL_STATEMENTS: memory_entries grows three columns + two
#      partial indexes + a third FOREIGN KEY clause for the self-FK.
#    - MIGRATIONS[23]: 3x ALTER TABLE ADD COLUMN (with the inline
#      REFERENCES on consolidated_into - legal because the default
#      is NULL) + 2x CREATE INDEX IF NOT EXISTS. Pure additive.
#
# 3. src/forest_soul_forge/core/audit_chain.py:
#    - KNOWN_EVENT_TYPES gains the three event types from D4.
#
# Tests (test_memory_consolidation.py - 9 cases):
#   Schema substrate sanity:
#     - SCHEMA_VERSION == 23
#     - MIGRATIONS[23] has the 5 expected statements with the
#       expected shape (column names, default, CHECK, REFERENCES,
#       index WHERE clauses).
#     - DDL_STATEMENTS includes the new columns + indexes (no
#       fresh-vs-migrated drift).
#   Migration applied on v22-shaped DB:
#     - Legacy row reads consolidation_state=pending,
#       consolidated_into=NULL, consolidation_run=NULL.
#     - Both indexes register.
#   CHECK constraint enforcement:
#     - Garbage state rejected.
#     - All 5 valid states accepted.
#   Self-FK on consolidated_into:
#     - Pointer to a real summary entry accepted.
#     - Pointer to a non-existent entry_id refused (with PRAGMA
#       foreign_keys=ON).
#   Index partial-ness:
#     - sqlite_master.sql for both indexes contains the WHERE
#       clause (proves they're partial, not full).
#   Audit event registration:
#     - All three event types in KNOWN_EVENT_TYPES.
#
# What's NOT in T1 (queued):
#   T2: ConsolidationSelector - age + layer + claim_type policy
#       producing a candidate batch.
#   T3: ConsolidationSummarizer - LLM call producing summary
#       content + lineage.
#   T4: Scheduled task wiring (uses ADR-0075 budget cap) + runner
#       end-to-end with full audit emit.
#   T5: /memory/consolidation/status endpoint + operator runbook +
#       fsf memory pin/unpin CLI.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0074-memory-consolidation.md \
        src/forest_soul_forge/registry/schema.py \
        src/forest_soul_forge/core/audit_chain.py \
        tests/unit/test_memory_consolidation.py \
        dev-tools/commit-bursts/commit-burst294-adr0074-t1-memory-consolidation.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(scale): ADR-0074 T1 - memory consolidation substrate v23 (B294)

Burst 294. Phase alpha closer - last untouched scale ADR.
ADR-0022 declared the working/episodic/consolidated lifecycle in
v0.1 but never built the rollup step; at ten-domain scale
memory_entries grows unbounded so this substrate is load-bearing.

What ships:

  - ADR-0074 full record. Four decisions:
    D1 consolidation_state 5-state enum (pending | consolidated |
       summary | pinned | purged) with CHECK constraint.
    D2 consolidated_into self-FK to memory_entries.entry_id
       (NULL unless state=consolidated; FK enforced when
       foreign_keys pragma is on).
    D3 consolidation_run TEXT for run-UUID traceability that
       pairs with the audit-chain bookend events.
    D4 Three audit event types: run_started + memory_consolidated
       per-entry + run_completed. Bookend pair lets operator
       detect crashed runs; per-entry events prove originals are
       folded-not-lost.
    Five tranches T1-T5.

  - registry/schema.py: SCHEMA_VERSION 22 -> 23. DDL_STATEMENTS
    grows three columns on memory_entries (state + into + run) +
    two partial indexes (one for the selector pending-scan, one
    for operator lineage queries) + a third FOREIGN KEY clause
    enforcing the self-reference. MIGRATIONS[23] is pure
    additive: 3 ALTER TABLE ADD COLUMN (with NULL default so the
    inline REFERENCES clause is legal under SQLite) + 2 CREATE
    INDEX IF NOT EXISTS.

  - core/audit_chain.py: KNOWN_EVENT_TYPES gains the three event
    types so T2-T5 emits dont trip the verifiers
    unknown-event-type check.

Tests: test_memory_consolidation.py - 9 cases covering schema
version + MIGRATIONS[23] shape + canonical DDL match + v23
migration applied on v22-shaped DB (legacy rows migrate as
pending, both indexes register) + CHECK rejects garbage / accepts
all 5 valid states + self-FK accepts good pointer / refuses
dangling pointer + index partial-ness confirmed via sqlite_master
+ all 3 audit events registered.

Queued T2-T5: ConsolidationSelector (age + layer + claim_type
candidate batching), ConsolidationSummarizer (LLM rollup),
scheduled-task wiring with ADR-0075 budget cap, operator endpoint
+ runbook + pin/unpin CLI."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 294 complete - ADR-0074 T1 memory consolidation shipped ==="
echo "Phase alpha substrate: 9 ADRs in motion, 0 untouched scale ADRs remaining."
echo ""
echo "Press any key to close."
read -n 1
