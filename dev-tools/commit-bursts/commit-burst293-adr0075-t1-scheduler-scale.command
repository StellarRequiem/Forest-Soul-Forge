#!/bin/bash
# Burst 293 - ADR-0075 T1: scheduler scale substrate (schema v22).
#
# Sized for the ten-domain platform's hundreds of recurring tasks.
# T1 ships the substrate (schema additions + audit event type)
# without changing dispatch semantics. T2/T3 wire enforcement on
# top.
#
# What ships:
#
# 1. docs/decisions/ADR-0075-scheduler-scale.md - full record.
#    Three decisions:
#      D1 Partial index on scheduled_task_state.next_run_at
#         (supports /scheduler/status reads + future SQL-pull
#          dispatch; NOT a dispatch-loop optimization in T1)
#      D2 budget_per_minute INTEGER column (default 6 = ten-second
#         floor; 0 = soft-pause; CHECK >= 0)
#      D3 scheduler_lag audit event type (reason=budget_enforced |
#         tick_over_budget; payload shape locked here so T2/T3 can
#         target it)
#    Four tranches T1-T4.
#
# 2. src/forest_soul_forge/registry/schema.py:
#    - SCHEMA_VERSION 21 -> 22
#    - DDL_STATEMENTS: scheduled_task_state gains budget_per_minute
#      column + idx_scheduled_task_state_next_run_at partial index
#    - MIGRATIONS[22]: ALTER TABLE ADD COLUMN with DEFAULT 6 +
#      CHECK constraint, plus CREATE INDEX IF NOT EXISTS for the
#      partial next_run_at index. Pure additive.
#
# 3. src/forest_soul_forge/core/audit_chain.py:
#    - KNOWN_EVENT_TYPES gains "scheduler_lag" so T2/T3 emits won't
#      trip the verifier's unknown-event-type check.
#
# 4. src/forest_soul_forge/daemon/scheduler/persistence.py:
#    - PersistedState gains budget_per_minute: int = 6 field
#      matching the column DEFAULT.
#    - SchedulerStateRepo.read_all() selects the new column.
#    - SchedulerStateRepo.upsert() inserts the new column. The
#      ON CONFLICT clause deliberately omits budget_per_minute
#      from the UPDATE SET list - operator-owned values aren't
#      stomped by scheduler-driven outcome upserts.
#
# Tests (test_scheduler_scale.py - 10 cases):
#   Schema substrate:
#     - SCHEMA_VERSION == 22
#     - MIGRATIONS[22] has both statements with the right shape
#     - DDL_STATEMENTS includes the new column + index (no
#       fresh-vs-migrated drift)
#   Migration applied on v21-shaped DB:
#     - Adds column with default for pre-existing rows
#     - Registers the partial index
#     - CHECK constraint rejects negative budgets; 0 is legal
#   PersistedState round-trip:
#     - Default budget_per_minute == 6
#     - Custom budget persists + reads back unchanged
#     - ON CONFLICT preserves operator-owned budget across
#       outcome upserts
#     - Legacy rows (inserted without budget) read back as 6
#   Audit event registration:
#     - scheduler_lag in KNOWN_EVENT_TYPES
#
# Test fixture update:
#   tests/unit/test_scheduler_runtime.py _fresh_persistence_db()
#   helper updated to include the v22 column + index. Existing
#   persistence-layer tests still pass (they don't supply
#   budget_per_minute, default kicks in).
#
# What's NOT in T1 (queued):
#   T2: Tick-wall-clock measurement + scheduler_lag emit when
#       tick duration exceeds threshold (default 500ms).
#   T3: Per-task sliding-window enforcement + scheduler_lag emit
#       when a task's budget gets enforced.
#   T4: /scheduler/status endpoint surfacing budget + lag history,
#       plus operator runbook (fsf scheduler budget CLI lands here).

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0075-scheduler-scale.md \
        src/forest_soul_forge/registry/schema.py \
        src/forest_soul_forge/core/audit_chain.py \
        src/forest_soul_forge/daemon/scheduler/persistence.py \
        tests/unit/test_scheduler_runtime.py \
        tests/unit/test_scheduler_scale.py \
        dev-tools/commit-bursts/commit-burst293-adr0075-t1-scheduler-scale.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(scale): ADR-0075 T1 - scheduler scale substrate v22 (B293)

Burst 293. Phase alpha scale substrate. Sized for the ten-domain
platforms hundreds of recurring tasks. T1 ships the substrate
(schema additions + audit event type) without changing dispatch
semantics; T2/T3 wire enforcement on top.

What ships:

  - ADR-0075 full record. Three decisions:
    D1 partial index on scheduled_task_state.next_run_at
       (supports /scheduler/status + future SQL-pull dispatch).
    D2 budget_per_minute INTEGER column (default 6 = ten-second
       floor; 0 = soft-pause; CHECK >= 0). Stored on state (not
       YAML config) so operators can adjust budget for a
       misbehaving task at runtime without editing config.
    D3 scheduler_lag audit event (reason=budget_enforced |
       tick_over_budget; payload shape locked so T2/T3 target it).
    Four tranches T1-T4.

  - registry/schema.py: SCHEMA_VERSION 21 -> 22. DDL_STATEMENTS
    grows the new column + partial index. MIGRATIONS[22] is
    purely additive (ALTER TABLE ADD COLUMN with DEFAULT, plus
    CREATE INDEX IF NOT EXISTS). Existing rows pick up
    budget=6 at migration time.

  - core/audit_chain.py: KNOWN_EVENT_TYPES gains scheduler_lag
    so T2/T3 emits dont trip the verifiers unknown-event-type
    check.

  - daemon/scheduler/persistence.py: PersistedState gains
    budget_per_minute: int = 6. SchedulerStateRepo.read_all
    selects the new column; upsert INSERTs it. ON CONFLICT
    deliberately omits budget_per_minute from the UPDATE SET
    list - operator-owned values arent stomped by
    scheduler-driven outcome upserts (ADR Decision 2).

Tests: test_scheduler_scale.py - 10 cases covering schema
version + MIGRATIONS[22] shape + DDL canonical-match + v22
migration applied on v21-shaped DB (column added, index
present, CHECK constraint working, 0 legal / -1 refused) +
PersistedState round-trip (default 6, custom budget, ON
CONFLICT preserve, legacy-row default) + scheduler_lag in
KNOWN_EVENT_TYPES.

test_scheduler_runtime.py _fresh_persistence_db() helper
updated to include the v22 column + index so existing
persistence-layer tests still pass.

Queued T2-T4: tick-over-budget detection + per-task
sliding-window enforcement + /scheduler/status endpoint +
operator runbook."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 293 complete - ADR-0075 T1 scheduler scale shipped ==="
echo "Next: B294 - ADR-0074 memory consolidation T1 OR ADR-0075 T2 tick-over-budget detection."
echo ""
echo "Press any key to close."
read -n 1
