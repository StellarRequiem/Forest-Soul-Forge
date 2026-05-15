#!/bin/bash
# Burst 296 - ADR-0075 T3: per-task budget enforcement.
#
# Substrate from B293 (column) + B295 (event type) now load-bearing:
# the dispatch loop enforces budget_per_minute via a sliding 60-second
# window and emits scheduler_lag(reason='budget_enforced') when a
# task tries to fire over budget. A misbehaving task can no longer
# starve the rest.
#
# What ships:
#
# 1. src/forest_soul_forge/daemon/scheduler/runtime.py:
#    - ScheduledTask gains `budget_per_minute: int = 6` field
#      (matches schema column default).
#    - Scheduler.__init__ initializes `_dispatch_windows: dict[str,
#      Deque[float]]` keyed by task_id. Each deque holds monotonic
#      timestamps of dispatches in the last 60s.
#    - _hydrate_persisted_state plumbs row.budget_per_minute onto
#      task.budget_per_minute so an operator override survives daemon
#      restart (B293 already wired the persistence round-trip).
#    - _tick now calls _consume_budget(task, now_dt, now_mono)
#      BEFORE _dispatch — rate-limited tasks skip the dispatch and
#      continue.
#    - _consume_budget:
#        * budget == 0 -> soft-pause. Return False, push next_run_at
#          per schedule, NO emit (deliberate operator action).
#        * budget > 0 -> purge entries < (now_mono - 60s) from the
#          deque, count what's left.
#            - count < budget -> append now_mono, return True.
#            - count >= budget -> push next_run_at to
#              oldest_entry + 60s (when budget headroom returns),
#              emit scheduler_lag(reason='budget_enforced',
#              task_id, budget_per_minute, dispatches_in_window),
#              return False.
#    - build_task_from_config honors a `budget_per_minute` key in
#      scheduled_tasks.yaml so operators can declare an initial
#      budget at config time. (The persisted column wins on hydrate
#      if present.)
#
# 2. tests/unit/test_scheduler_scale.py - appends eight B296 cases:
#    - test_consume_budget_allows_until_window_fills:
#      budget=3 -> first 3 calls True, 4th False; window stays at 3.
#    - test_consume_budget_emits_scheduler_lag_on_enforcement:
#      payload shape verified field-by-field (T2-only fields nulled).
#    - test_consume_budget_pushes_next_run_at_forward_on_enforcement:
#      ~60-second push confirmed.
#    - test_consume_budget_purges_stale_entries:
#      70-second-old entry dropped before counting; in-budget call
#      passes.
#    - test_consume_budget_zero_is_soft_pause_no_emit:
#      budget=0 -> False, NO scheduler_lag emit, next_run_at advanced.
#    - test_consume_budget_negative_treated_as_zero:
#      defensive in-memory guard for impossible-on-disk values.
#    - test_scheduledtask_budget_default_is_six:
#      pins ADR Decision 2 default at the dataclass level.
#    - test_build_task_from_config_honors_budget_field:
#      YAML override works (operators can declare from config).
#
# What's NOT in T3 (queued):
#   T4: /scheduler/status endpoint surfaces per-task budget +
#       window count + recent lag events; operator runbook;
#       fsf scheduler budget CLI.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/scheduler/runtime.py \
        tests/unit/test_scheduler_scale.py \
        dev-tools/commit-bursts/commit-burst296-adr0075-t3-budget-enforcement.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(scale): ADR-0075 T3 - per-task budget enforcement (B296)

Burst 296. Substrate from B293 (column) + B295 (event type) goes
load-bearing. The dispatch loop now enforces budget_per_minute
via a sliding 60-second window per task. A misbehaving task can
no longer starve the rest - the budget caps its invocation rate
independent of its configured schedule.

What ships:

  - scheduler/runtime.py: ScheduledTask gains budget_per_minute
    field (default 6, matching schema column). Scheduler.__init__
    initializes _dispatch_windows keyed by task_id with
    monotonic-clock timestamps - monotonic so system-time jumps
    cant fold or invert the window. _hydrate_persisted_state
    plumbs row.budget_per_minute onto the in-memory task so an
    operator override survives daemon restart (B293 already
    wired the round-trip).

    _tick calls _consume_budget before _dispatch:
      * budget=0 -> soft-pause. Return False, push next_run_at
        per schedule, NO scheduler_lag emit (deliberate
        operator action isnt an anomaly worth flagging).
      * budget>0 -> purge entries older than 60s, count
        what remains.
          - count < budget -> append now_mono + dispatch.
          - count >= budget -> push next_run_at to
            oldest_entry + 60s (when budget headroom returns),
            emit scheduler_lag(reason='budget_enforced',
            task_id, budget_per_minute, dispatches_in_window),
            skip dispatch.

    build_task_from_config now honors a budget_per_minute key in
    scheduled_tasks.yaml so operators can declare initial budgets
    in config. (Persisted column wins on hydrate if present.)

Tests: test_scheduler_scale.py - 8 new cases covering allow-until-
fill, emit payload shape (T2-only fields nulled), next_run_at
~60s push, stale-entry purge, budget=0 soft-pause no-emit,
negative budget defensive guard, dataclass default 6, and
build_task_from_config YAML override.

Queued T4: /scheduler/status endpoint + operator runbook +
fsf scheduler budget CLI. Closes scheduler-arc runner work to
3/4."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 296 complete - ADR-0075 T3 budget enforcement shipped ==="
echo "Next: B297 - ADR-0075 T4 /scheduler/status endpoint OR another arc."
echo ""
echo "Press any key to close."
read -n 1
