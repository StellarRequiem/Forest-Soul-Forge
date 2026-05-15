#!/bin/bash
# Burst 297 - ADR-0075 T4: /scheduler/status payload + operator runbook.
#
# Closes ADR-0075 (4/4). Phase alpha scheduler arc complete.
# Operator gets one endpoint that surfaces tick_budget_ms +
# per-task budget + dispatch-window counts. Runbook documents
# every operational lever (tick budget tuning, per-task budget
# override via SQL, soft-pause workflow, scheduler_lag diagnosis,
# breaker reset).
#
# What ships:
#
# 1. src/forest_soul_forge/daemon/scheduler/runtime.py:
#    - Scheduler.status() extended with:
#        * tick_budget_ms (the configured ceiling)
#        * tasks_paused (count of budget=0 soft-paused tasks)
#        * dispatch_windows: {total_in_window, per_task} - the
#          live B296 enforcement state surfaced for operator
#          visibility.
#
# 2. src/forest_soul_forge/daemon/routers/scheduler.py:
#    - _serialize_task gains an optional `scheduler` parameter.
#      When supplied, the serialized task includes budget_per_minute
#      and dispatches_in_window. Backward-compatible: callers that
#      don't pass scheduler still get a valid dict.
#    - The list and per-task GET endpoints both pass the scheduler
#      through so per-task views include the budget snapshot.
#
# 3. docs/runbooks/scheduler-scale.md - operator runbook.
#    Covers:
#      * Reading /scheduler/status (what each field means)
#      * Tuning FSF_SCHEDULER_TICK_BUDGET_MS
#      * Adjusting per-task budget via SQL UPDATE on
#        scheduled_task_state (until the fsf CLI tranche lands)
#      * Diagnosing scheduler_lag events from the audit chain
#      * Soft-pause workflow (budget=0)
#      * Resetting a tripped circuit breaker
#
# Tests (test_scheduler_scale.py - 5 new cases):
#   - test_status_includes_tick_budget_ms
#   - test_status_includes_dispatch_window_summary
#   - test_status_counts_paused_tasks
#   - test_router_serialize_task_includes_budget_and_window
#   - test_router_serialize_task_no_scheduler_defaults_window_zero
#   (Router tests pull fastapi - sandbox skip, host pytest covers.)
#
# What's NOT in T4 (future):
#   - fsf scheduler budget CLI for per-task budget edits without
#     restart. The runbook documents the SQL workaround in the
#     meantime.
#   - Live per-tick budget reread (currently restart-required).

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/scheduler/runtime.py \
        src/forest_soul_forge/daemon/routers/scheduler.py \
        docs/runbooks/scheduler-scale.md \
        tests/unit/test_scheduler_scale.py \
        dev-tools/commit-bursts/commit-burst297-adr0075-t4-status-runbook.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(scale): ADR-0075 T4 - /scheduler/status + operator runbook (B297)

Burst 297. Closes ADR-0075 (4/4). Phase alpha scheduler arc
complete. Operator gets one endpoint surfacing tick_budget_ms +
per-task budget + dispatch-window counts; runbook documents
every operational lever.

What ships:

  - scheduler/runtime.py: Scheduler.status() extended with
    tick_budget_ms (configured ceiling), tasks_paused (count of
    budget=0 soft-paused tasks), and dispatch_windows
    {total_in_window, per_task} - the live B296 enforcement
    state surfaced for operator visibility.

  - daemon/routers/scheduler.py: _serialize_task gains optional
    scheduler kwarg. When supplied the task dict includes
    budget_per_minute + dispatches_in_window from the live
    dispatch_windows dict. Backward-compatible: bare calls
    still produce a valid dict (window=0).

  - docs/runbooks/scheduler-scale.md: operator runbook covering
    reading /scheduler/status, tuning
    FSF_SCHEDULER_TICK_BUDGET_MS, adjusting per-task budget via
    SQL UPDATE on scheduled_task_state (the fsf CLI lands in a
    future tranche), diagnosing scheduler_lag events from the
    audit chain, soft-pause workflow (budget=0), resetting a
    tripped breaker.

Tests: test_scheduler_scale.py - 5 new cases pinning status
extension (tick_budget_ms surfaced, dispatch window summary,
paused count) and router _serialize_task budget/window inclusion
with and without scheduler.

Phase alpha scale ADRs now: 5/10 with at least one runner
tranche. ADR-0075 closed; ADR-0067 at 7/8; ADR-0050 closed;
others at T1 substrate."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 297 complete - ADR-0075 closed 4/4 ==="
echo ""
echo "Press any key to close."
read -n 1
