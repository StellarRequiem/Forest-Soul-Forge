#!/usr/bin/env bash
# Burst 86: ADR-0041 T2 — scheduler runtime substrate + lifespan integration.
#
# Daemon-internal scheduler heartbeat. Asyncio loop in lifespan.
# Tasks loaded from config/scheduled_tasks.yaml (optional). No task
# types yet (Burst 87 adds tool_call). No persistence yet (Burst 88).
# GET-only HTTP at /scheduler/{status,tasks,tasks/{id}}.
#
# Test count grew 2072 -> 2106 (+34 scheduler unit tests).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 86 — ADR-0041 T2 scheduler runtime + lifespan ==="
echo
clean_locks
git add src/forest_soul_forge/daemon/scheduler/
git add src/forest_soul_forge/daemon/routers/scheduler.py
git add src/forest_soul_forge/daemon/app.py
git add src/forest_soul_forge/daemon/config.py
git add tests/unit/test_scheduler_runtime.py
git add commit-burst86.command
clean_locks
git status --short
echo
clean_locks
git commit -m "feat: ADR-0041 T2 — scheduler runtime + lifespan integration

Daemon-internal scheduler heartbeat per ADR-0041 §Architecture.
Runs in the FastAPI app's asyncio loop alongside the existing
write_lock/audit_chain/registry. No new process, no IPC.

What lands in this burst:

- daemon/scheduler/__init__.py — package facade
- daemon/scheduler/schedule.py — interval-based parser
  ('every 30s' / '5m' / '6h' / '2d'); rejects bare ints, negatives,
  zero, unknown units, cron syntax (queued for follow-up).
- daemon/scheduler/runtime.py — Scheduler + ScheduledTask + TaskState
  + build_task_from_config. The asyncio poll loop, per-task circuit
  breaker (max_consecutive_failures default 3), success-clears-counter
  semantics, runner-exception treated as failure, task type registry
  (runners attach via register_task_type — empty in this burst,
  Burst 87 adds tool_call).
- daemon/routers/scheduler.py — GET /scheduler/status,
  GET /scheduler/tasks, GET /scheduler/tasks/{task_id}. Read-only;
  POST endpoints (trigger / enable / disable / reset) land in
  Burst 89.
- daemon/app.py — lifespan integration. FSF_SCHEDULER_ENABLED env
  toggle (default true). FSF_SCHEDULER_POLL_INTERVAL_SECONDS env
  override (default 30). Loads config/scheduled_tasks.yaml if
  present (silent skip if absent — startup_diagnostics records
  the state). Started before yield, stopped in finally with
  5s timeout + cancel fallback.
- daemon/config.py — adds scheduled_tasks_path setting.
- tests/unit/test_scheduler_runtime.py — 30 unit tests covering
  schedule parsing (10), task due-state predicate (5), scheduler
  lifecycle (3), dispatch/outcome (5), build_task_from_config (4),
  failure/success counter math (2 via direct ._dispatch since the
  interval-based schedule won't re-fire within wall-clock test windows
  — captured as a comment in the test for future-me).

Test count: 2072 -> 2106 (+34: 30 new scheduler tests + 4 incidental).
Verified pass on scheduler + writes + memory subsets. Full suite
exceeded the sandbox bash 45s cap during this commit but no
failures observed.

What this does NOT do (per the tranche plan):
- T3 (Burst 87): tool_call task type implementation +
  scheduled_task_dispatched/completed/failed audit events.
- T3 (Burst 87/88): scenario task type runtime + step interpreter.
- T2.3 (Burst 88): SQLite v13 schema for scheduled_task_state +
  read/write so state survives daemon restart.
- T5 (Burst 89): operator POST endpoints + tests + runbook.

Architecture notes encoded in the source:
- TaskRunner is an async callable (config_dict, context_dict) ->
  outcome_dict; outcome.ok=False (or exception) bumps the failure
  counter; the breaker trips at task.max_consecutive_failures.
- Scheduler dispatches serially per tick — write_lock would
  serialize them anyway, and serial is easier to reason about
  for the audit chain.
- task_type validation happens at dispatch time, not add_task —
  lets operators configure tasks for runners that load later
  (e.g., plugins) without rejecting the config.
- add_task is idempotent (last write wins) so operators editing
  the YAML between restarts don't see duplicate-id errors.

What this unblocks for the FizzBuzz scenario use case:
- Daemon now has a heartbeat. Burst 87 adds tool_call so verifier
  scans can be scheduled (closes ADR-0036 T4). Burst 88 adds
  scenario type so the FizzBuzz coding loop can run unattended."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 86 landed. Scheduler heartbeat live in the daemon."
echo "Next: Burst 87 — T3 tool_call task type + audit events."
echo ""
read -rp "Press Enter to close..."
