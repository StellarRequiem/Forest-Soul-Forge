#!/usr/bin/env bash
# Burst 91: ADR-0041 T6 — operator control endpoints for the scheduler.
#
# After Bursts 86/89/90 the scheduler has runtime + tool_call runner +
# persistence. The remaining gap before the orchestrator is fully
# operator-controllable: the operator could only configure tasks via
# config/scheduled_tasks.yaml (requires daemon restart) and observe
# them via GET endpoints. There was no way to:
#   - Force a task to fire RIGHT NOW (out-of-band trigger)
#   - Pause a task without removing it from config
#   - Resume a paused task
#   - Clear a tripped circuit breaker after fixing the underlying issue
#
# Burst 91 closes that gap with four POST endpoints + four
# Scheduler methods backing them.
#
# WHAT'S NEW
#
# 1. daemon/scheduler/runtime.py — three operator-control methods
#    on the Scheduler class:
#
#    - async trigger(task_id) -> dict
#        Force-dispatch one task RIGHT NOW. Counts as a real run
#        (updates next_run_at, increments total_runs). Refuses if
#        task is unknown / disabled / breaker-open with a structured
#        {ok: False, reason: ...} so the HTTP layer can map to the
#        right status code.
#
#    - set_enabled(task_id, bool) -> bool
#        Toggle the enabled flag. Returns False if the task isn't
#        registered. Disabling does NOT cancel an in-flight dispatch
#        — serial dispatch under the write_lock means the running
#        task finishes before the next due() check sees the flag.
#        Emits scheduled_task_enabled / scheduled_task_disabled to
#        the audit chain so the change is visible in the timeline.
#
#    - reset(task_id) -> bool
#        Clear circuit breaker, zero consecutive_failures, null
#        last_failure_reason. Leaves last_run_outcome intact so the
#        operator looking at /scheduler/tasks/{id} after reset can
#        still see "the last outcome was 'failed', I just cleared
#        the breaker." Persists the cleared state and emits
#        scheduled_task_circuit_breaker_reset.
#
# 2. daemon/routers/scheduler.py — four POST endpoints:
#
#    - POST /scheduler/tasks/{id}/trigger
#        404 on unknown task; 409 Conflict on disabled / breaker-open
#        (operator-recoverable, distinct from 404). Returns the
#        dispatch outcome so the operator gets immediate feedback.
#
#    - POST /scheduler/tasks/{id}/enable
#        404 on unknown; otherwise {ok: True, enabled: True}.
#
#    - POST /scheduler/tasks/{id}/disable
#        404 on unknown; otherwise {ok: True, enabled: False}.
#
#    - POST /scheduler/tasks/{id}/reset
#        404 on unknown; otherwise {ok: True, circuit_breaker_open:
#        False, consecutive_failures: 0}.
#
#    All four POSTs are gated by require_writes_enabled +
#    require_api_token, same posture as the /writes routes — 403
#    fires before 401 when writes are disabled, which is the more
#    informative error.
#
# 3. tests/unit/test_scheduler_runtime.py +9 unit tests:
#    - test_trigger_dispatches_immediately
#    - test_trigger_unknown_task_returns_not_found
#    - test_trigger_disabled_task_returns_disabled
#    - test_trigger_breaker_open_refuses
#    - test_set_enabled_toggles_and_emits_audit
#    - test_set_enabled_unknown_task_returns_false
#    - test_reset_clears_breaker_and_counters
#    - test_reset_unknown_task_returns_false
#    - test_reset_persists_cleared_state (verifies the SQLite
#      side-effect, not just the in-memory mutation)
#
# VERIFICATION
#
# Sandbox: PYTHONPATH=src python3 -m pytest tests/unit
#   → 2129 passed, 3 skipped, 1 xfailed. +9 from Burst 90's 2120.
#   Zero regressions.
#
# Host (post-restart, requires API token + writes enabled):
#
#   # Force-trigger a task that's been waiting for its next tick
#   curl -X POST -H "X-API-Token: $TOKEN" \
#     http://127.0.0.1:7423/scheduler/tasks/verifier_24h_scan/trigger
#
#   # Pause a noisy task
#   curl -X POST -H "X-API-Token: $TOKEN" \
#     http://127.0.0.1:7423/scheduler/tasks/heartbeat_5m/disable
#
#   # Clear a breaker after fixing the underlying issue
#   curl -X POST -H "X-API-Token: $TOKEN" \
#     http://127.0.0.1:7423/scheduler/tasks/verifier_24h_scan/reset
#
#   # Resume the paused task
#   curl -X POST -H "X-API-Token: $TOKEN" \
#     http://127.0.0.1:7423/scheduler/tasks/heartbeat_5m/enable
#
# WHAT THIS CLOSES
#
# After Burst 91, ADR-0041 is **complete except for T4** (scenario
# task type). The orchestrator has:
#
#   T1 design (Burst 85)
#   T2 runtime + lifespan integration (Burst 86)
#   T3 tool_call task type + audit emit (Burst 89)
#   T5 SQLite v13 persistence (Burst 90)
#   T6 operator control endpoints (this burst)
#   T4 scenario task type ← still outstanding
#
# T4 is a substantial chunk on its own (multi-step DSL, scenario
# YAML loader, FizzBuzz port). Splitting it from the rest of the
# arc lets us tag a v0.4.0-rc with the production-grade
# tool_call-only scheduler before adding the more speculative
# scenario surface. That's a useful checkpoint.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 91 — ADR-0041 T6: operator control endpoints ==="
echo
clean_locks
git add src/forest_soul_forge/daemon/scheduler/runtime.py
git add src/forest_soul_forge/daemon/routers/scheduler.py
git add tests/unit/test_scheduler_runtime.py
git add commit-burst91-scheduler-control.command
clean_locks
git status --short
echo
clean_locks
git commit -m "feat(scheduler): operator control endpoints (ADR-0041 T6)

Closes the operator-control gap. The scheduler had runtime + runner
+ persistence after Bursts 86/89/90, but the operator could only
configure tasks via YAML (requires daemon restart) and observe them
via GET. No way to force-trigger, pause, resume, or unblock a
tripped breaker without bouncing the daemon.

Scheduler runtime (daemon/scheduler/runtime.py):

- trigger(task_id) async — force-dispatch out-of-band. Counts as
  a real run (next_run_at advances; total_runs ticks). Refuses
  with structured {ok: False, reason: ...} for unknown / disabled
  / breaker-open so the HTTP layer can map to 404 vs 409. Manual
  trigger still emits the same scheduled_task_dispatched/completed/
  failed audit pair as a normal tick — manual and tick dispatches
  are the same operation, just on different clocks.

- set_enabled(task_id, bool) — toggle the flag. False return on
  unknown task. Disabling doesn't cancel an in-flight dispatch
  (serial dispatch under write_lock means the current run completes
  before the next due() check sees the change). Emits
  scheduled_task_enabled / scheduled_task_disabled.

- reset(task_id) — clear circuit_breaker_open, zero
  consecutive_failures, null last_failure_reason. Leaves
  last_run_outcome intact so the operator can still see context
  after the reset. Persists the cleared state, emits
  scheduled_task_circuit_breaker_reset.

HTTP endpoints (daemon/routers/scheduler.py):

- POST /scheduler/tasks/{id}/trigger
    404 on unknown task. 409 Conflict on disabled / breaker-open
    (operator-recoverable, distinct from 404). Returns the
    dispatch outcome so the operator gets immediate feedback.

- POST /scheduler/tasks/{id}/enable
- POST /scheduler/tasks/{id}/disable
- POST /scheduler/tasks/{id}/reset
    All return 404 on unknown task; success returns the new state.

All four POSTs gated by require_writes_enabled + require_api_token,
same posture as /writes — 403 before 401 when writes are disabled.

Tests +9 in test_scheduler_runtime.py:
- trigger immediate-dispatch + total_runs increment
- trigger unknown / disabled / breaker-open paths
- set_enabled toggle + audit emit pair
- set_enabled unknown returns False
- reset clears breaker + counters but preserves last_run_outcome
- reset unknown returns False
- reset persists cleared state to SQLite (round-trip via repo)

Verification: 2129 unit tests pass (was 2120 before Burst 91).
Zero regressions in scheduler runtime, persistence, deps refactor,
or HTTP routes.

ADR-0041 is now complete except for T4 (scenario task type) —
that's a substantial DSL + YAML loader + FizzBuzz port; splitting
it lets us tag v0.4.0-rc with the production-grade tool_call-only
scheduler as a checkpoint before adding the more speculative
scenario surface."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 91 landed. Scheduler is now operator-controllable end-to-end."
echo "ADR-0041 is complete except for T4 (scenario task type)."
echo ""
read -rp "Press Enter to close..."
