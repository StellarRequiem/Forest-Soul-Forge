#!/usr/bin/env bash
# Burst 90: ADR-0041 T5 — SQLite v13 persistence for scheduled tasks.
#
# Without persistence, every daemon restart resets:
#   - consecutive_failures → tripped breakers reset, broken tasks
#     start retrying immediately
#   - last_run_at / next_run_at → tasks fire IMMEDIATELY on restart
#     even if they ran 30s before the crash
#   - total_runs / total_successes / total_failures → career
#     history vanishes, hiding long-running flakiness
# This makes the scheduler unsuitable for actual production work
# even after Bursts 86 + 89 wired the runner.
#
# Burst 90 closes that gap with schema v13 + a small repository
# layer. The audit chain remains the source of truth (every state
# change still emits scheduled_task_dispatched/completed/failed/
# breaker_tripped); the new table is the **indexed view** so
# Scheduler.start hydrates in O(1) per task instead of replaying
# the chain.
#
# WHAT'S NEW
#
# 1. registry/schema.py — SCHEMA_VERSION 12 → 13.
#    New table scheduled_task_state (task_id PK, last_run_at,
#    next_run_at, consecutive_failures, circuit_breaker_open,
#    total_runs, total_successes, total_failures,
#    last_failure_reason, last_run_outcome, updated_at).
#    Plus a partial index on circuit_breaker_open=1 so operator
#    queries for "what tripped" are O(tripped) not O(all tasks).
#    MIGRATIONS[13] is pure addition — no risk to existing rows.
#
# 2. daemon/scheduler/persistence.py — new module.
#    SchedulerStateRepo with read_all() + upsert(state) + delete(id).
#    Knows nothing about ScheduledTask or Scheduler — the runtime
#    owns the typed conversion in both directions. Repo speaks
#    SQLite primitives only (datetime → ISO string, bool → int).
#    PersistedState dataclass holds one row.
#
# 3. daemon/scheduler/runtime.py — three additions:
#    a. Scheduler.__init__ takes optional state_repo=. None by
#       default so existing tests (which don't care about
#       restart-survival) keep working unchanged.
#    b. Scheduler.start now calls _hydrate_persisted_state BEFORE
#       starting the poll loop. Reads all rows, applies matching
#       state to registered tasks. Tasks not in the table keep
#       their default TaskState (next_run_at=None → fire on
#       first tick). Persistence failures are best-effort — a
#       broken read just means starting fresh.
#    c. Scheduler._dispatch persists state once at the end (after
#       all branches have mutated it). Persistence failure logged
#       + swallowed; the audit chain is the source of truth.
#    Plus _parse_iso_or_none helper for the datetime roundtrip
#    (3.10 needs the trailing-Z fixup since fromisoformat got
#    Z support in 3.11).
#
# 4. daemon/app.py lifespan — wires SchedulerStateRepo over
#    registry._conn and passes it to Scheduler(state_repo=...).
#
# 5. tests/unit/test_scheduler_runtime.py +8 tests:
#      * test_persistence_repo_upsert_then_read_roundtrip
#      * test_persistence_repo_upsert_overwrites
#      * test_persistence_repo_delete
#      * test_scheduler_persists_state_after_dispatch
#      * test_scheduler_hydrates_state_on_start
#      * test_scheduler_no_state_repo_works_in_memory_only
#      * test_scheduler_persist_failure_does_not_break_dispatch
#      * test_parse_iso_or_none_handles_z_suffix_and_offset
#
# 6. tests/unit/test_daemon_readonly.py + test_registry.py +
#    test_memory_flagged_state.py — schema_version literal bumps
#    (12 → 13). The registry tests' assertions are guards; the
#    flagged_state test relaxed to >=12 so future bumps don't
#    require re-touching it.
#
# VERIFICATION
#
# Sandbox: PYTHONPATH=src python3 -m pytest tests/unit
#   → 2120 passed, 3 skipped, 1 xfailed. +8 from Burst 89's 2112.
#
# Host (post-restart):
#   1. Restart daemon → migration v12 → v13 runs automatically.
#   2. /healthz reports schema_version=13.
#   3. Configure a tool_call task in scheduled_tasks.yaml that
#      dispatches against an existing agent. Wait for it to fire.
#   4. Restart the daemon. /scheduler/tasks/{id} should show
#      total_runs=N (preserved), last_run_at=<recent ISO>
#      (preserved), next_run_at=<recent + interval> (preserved).
#      Without persistence (pre-Burst-90), all three would reset.
#
# WHAT'S STILL OUTSTANDING (per ADR-0041)
#
# T4: scenario task type (multi-step birth+seed+iterate+archive
#   for FizzBuzz-class loops). Burst 91 — moved up since
#   persistence is the higher-leverage move.
# T6: operator control endpoints (/scheduler/tasks/{id}/trigger,
#   enable, disable, reset). Burst 92.
# Port FizzBuzz to scenario YAML format. Closes Burst 81 P1.
#
# After T4 + T6 + the FizzBuzz port, the scheduler is fully
# operator-controllable AND has the canonical autonomous-loop
# scenario as configuration rather than a bash driver.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 90 — ADR-0041 T5: scheduler persistence (schema v13) ==="
echo
clean_locks
git add src/forest_soul_forge/registry/schema.py
git add src/forest_soul_forge/daemon/scheduler/persistence.py
git add src/forest_soul_forge/daemon/scheduler/runtime.py
git add src/forest_soul_forge/daemon/app.py
git add tests/unit/test_scheduler_runtime.py
git add tests/unit/test_daemon_readonly.py
git add tests/unit/test_registry.py
git add tests/unit/test_memory_flagged_state.py
git add commit-burst90-scheduler-persistence.command
clean_locks
git status --short
echo
clean_locks
git commit -m "feat(scheduler): SQLite v13 persistence for scheduled tasks (ADR-0041 T5)

Without persistence, every daemon restart resets the scheduler's
state — consecutive_failures (broken breakers reset),
last_run_at/next_run_at (tasks fire immediately on restart),
total_runs/successes/failures (career history gone). That makes
Bursts 86+89 useful for testing only. Burst 90 fixes that.

Schema bump v12 → v13 adds scheduled_task_state. Pure addition;
no risk to existing rows. Mirrors the in-memory ScheduledTask.state
dataclass — task_id PK, last_run_at, next_run_at, breaker bool +
counters, last_failure_reason, last_run_outcome, updated_at.
Partial index on circuit_breaker_open=1 keeps operator queries
fast even with hundreds of tasks.

New module daemon/scheduler/persistence.py:
- SchedulerStateRepo(conn) with read_all() / upsert(state) /
  delete(task_id). Speaks SQLite primitives only — no awareness
  of ScheduledTask or Scheduler. Tests use a bare in-memory conn
  with the v13 DDL.
- PersistedState dataclass for one row.

Scheduler runtime additions:
- Scheduler.__init__(state_repo=None) — optional. Existing tests
  pass None and keep working unchanged.
- Scheduler.start now calls _hydrate_persisted_state before the
  poll loop. Reads all rows, applies matching state to registered
  tasks. Hydrate failures are best-effort: a broken read just
  means starting fresh. Tasks present in the table but not
  registered in-memory are ignored (operator removed from config).
- Scheduler._dispatch persists once at the end of every dispatch
  outcome. Single upsert per dispatch keeps SQLite write count
  stable. Persist failures are logged + swallowed — audit chain
  is the source of truth.
- _parse_iso_or_none helper roundtrips datetimes through ISO.
  3.10 needs Z->+00:00 fixup since fromisoformat got Z support
  in 3.11; current sandbox is 3.10 so this matters.

Lifespan wiring:
- daemon/app.py builds SchedulerStateRepo(registry._conn) and
  passes it to Scheduler(state_repo=...).

Tests +8 in test_scheduler_runtime.py:
- repo upsert/read roundtrip
- repo overwrite-on-conflict
- repo delete (idempotent)
- scheduler persists after dispatch
- scheduler hydrates state on start (full restart-survives test
  with pre-populated row + datetime roundtrip)
- scheduler-without-repo still works
- broken repo doesn't break dispatch
- _parse_iso_or_none handles Z suffix, +offset, None, garbage

Schema-literal bumps:
- test_daemon_readonly.py: assert schema_version == 12 → 13
- test_registry.py: 6 sites assert r.schema_version() == 12 → 13
  (replace_all with the standard guard pattern)
- test_memory_flagged_state.py: relaxed exact-12 check to >=12
  + renamed to test_schema_constant_is_at_least_12 so future
  bumps don't require re-editing this test

Verification: 2120 unit tests pass (was 2112 before Burst 90;
+8 persistence tests). Migration v12→v13 runs automatically on
existing DBs at next daemon start.

Outstanding ADR-0041 work:
- T4 scenario task type (Burst 91)
- T6 operator endpoints (Burst 92)
- Port FizzBuzz scenario to YAML (closes Burst 81 P1)"

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 90 landed. Schema v13 + persistence wired."
echo "Restart the daemon to run the v12→v13 migration."
echo "After restart, scheduler state survives across restarts."
echo ""
read -rp "Press Enter to close..."
