#!/bin/bash
# Burst 295 - ADR-0075 T2: tick-over-budget detection.
#
# First runner on top of B293's scheduler scale substrate. The
# scheduler now measures wall-clock per tick and emits
# scheduler_lag(reason='tick_over_budget') when a dispatching tick
# exceeds the configured budget. Operator gets "is the scheduler
# keeping up?" visibility via the audit chain.
#
# What ships:
#
# 1. src/forest_soul_forge/daemon/scheduler/runtime.py:
#    - import time (for time.monotonic).
#    - Scheduler.__init__ gains `tick_budget_ms: float = 500.0` kwarg
#      (ADR-0075 Decision 3 default). Stored on self._tick_budget_ms.
#    - Scheduler._tick now:
#        * Records time.monotonic() at start. Monotonic so a system-
#          time jump mid-tick cant make duration negative or vault
#          artificially over budget.
#        * Counts dispatches as it iterates.
#        * After the loop, computes wall-clock ms.
#        * If dispatches > 0 AND duration > budget, emits
#          scheduler_lag(reason='tick_over_budget', task_id=None,
#                         tick_duration_ms, tick_budget_ms,
#                         dispatches_in_tick, budget_per_minute=None,
#                         dispatches_in_window=None, details=None).
#        * Idle over-budget ticks (dispatches=0) DO NOT emit -
#          environment hiccups (GC, OS scheduler) would spam the
#          chain.
#
# 2. src/forest_soul_forge/daemon/app.py:
#    - Reads FSF_SCHEDULER_TICK_BUDGET_MS env var (default 500).
#    - Passes scheduler_tick_budget_ms into Scheduler() construction.
#
# 3. tests/unit/test_scheduler_scale.py - appends four B295 cases:
#    - test_tick_under_budget_does_not_emit_scheduler_lag:
#      no-false-positive guard. Fast runner + generous budget -> 0 emits.
#    - test_tick_over_budget_emits_scheduler_lag:
#      80ms-sleep runner vs 10ms budget -> exactly one emit with the
#      full payload shape verified field-by-field. Locks the payload
#      shape that T3 (per-task enforcement) will share.
#    - test_tick_over_budget_with_zero_dispatches_does_not_emit:
#      0ms budget + 0 tasks -> 0 emits. Pins the dispatches>0
#      guard against future regressions.
#    - test_tick_budget_default_is_500ms:
#      pins ADR Decision 3 default at the constructor-default level.
#
# What's NOT in T2 (queued):
#   T3: per-task sliding-window enforcement with scheduler_lag(reason=
#       'budget_enforced') emit when a task's budget_per_minute is
#       hit.
#   T4: /scheduler/status endpoint + operator runbook + fsf scheduler
#       budget CLI.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/scheduler/runtime.py \
        src/forest_soul_forge/daemon/app.py \
        tests/unit/test_scheduler_scale.py \
        dev-tools/commit-bursts/commit-burst295-adr0075-t2-tick-over-budget.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(scale): ADR-0075 T2 - tick-over-budget detection (B295)

Burst 295. First runner on top of B293's scheduler scale
substrate. The scheduler now measures wall-clock per tick; a
dispatching tick that exceeds the configured budget emits
scheduler_lag(reason='tick_over_budget') so operators see
'is the scheduler keeping up?' via the audit chain.

What ships:

  - scheduler/runtime.py: Scheduler.__init__ gains tick_budget_ms
    kwarg (default 500.0ms per ADR Decision 3, stored on
    self._tick_budget_ms). Scheduler._tick wraps the dispatch
    loop with time.monotonic() measurement (monotonic so
    system-time jumps cant skew the duration) and tracks
    per-tick dispatch count. After the loop, if dispatches > 0
    AND duration > budget, emits scheduler_lag with the full
    payload shape locked in ADR-0075 (reason, task_id=None,
    tick_duration_ms, tick_budget_ms, dispatches_in_tick, plus
    T3-shared null fields budget_per_minute /
    dispatches_in_window / details). Idle over-budget ticks
    DONT emit - environment hiccups would spam the chain.

  - daemon/app.py: reads FSF_SCHEDULER_TICK_BUDGET_MS env var
    (default 500) and passes it through to Scheduler().

Tests: test_scheduler_scale.py - 4 new cases. Under-budget tick
emits zero (no-false-positive guard). Over-budget dispatching
tick (80ms runner vs 10ms budget) emits exactly one with the
full payload shape verified field-by-field. Idle over-budget
tick (0 budget + 0 tasks) emits zero (pins the dispatches>0
guard). Constructor default is 500ms (pins ADR Decision 3).

Queued T3-T4: per-task sliding-window enforcement with
scheduler_lag(reason='budget_enforced'), then /scheduler/status
endpoint + operator runbook + fsf scheduler budget CLI."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 295 complete - ADR-0075 T2 tick-over-budget shipped ==="
echo "Next: B296 - ADR-0075 T3 per-task budget enforcement OR another tranche."
echo ""
echo "Press any key to close."
read -n 1
