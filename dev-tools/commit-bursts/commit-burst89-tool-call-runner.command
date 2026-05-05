#!/usr/bin/env bash
# Burst 89: ADR-0041 T3 — tool_call task type runner + scheduler audit emit.
#
# Closes ADR-0036 T4 (Verifier Loop scheduled scans) by giving the
# scheduler a concrete task-type runner. After this commit lands,
# the operator can configure config/scheduled_tasks.yaml with
# tool_call entries and the scheduler will dispatch them on cadence
# through the standard ToolDispatcher (so all governance applies).
#
# WHAT'S NEW
#
# 1. daemon/scheduler/task_types/__init__.py — package facade
#    exposing tool_call_runner.
#
# 2. daemon/scheduler/task_types/tool_call.py — async runner.
#    Pulls agent from registry, builds-or-gets the dispatcher via
#    build_or_get_tool_dispatcher(app), holds write_lock for the
#    duration of dispatch, returns {ok: True/False, ...}. ADR-0041
#    open-question (a) resolved: dispatch landing in
#    DispatchPendingApproval surfaces as failure rather than silently
#    queueing — operator visibility > convenience.
#
# 3. daemon/deps.py refactor — extracted build_or_get_tool_dispatcher
#    helper from get_tool_dispatcher. The HTTP dep is now a thin
#    wrapper. New ToolDispatcherUnavailable exception decouples the
#    "deps not ready" path from HTTPException so non-HTTP callers
#    (the scheduler runner) can handle it without spoofing a Request.
#
# 4. daemon/scheduler/runtime.py — six audit events emitted by
#    Scheduler._dispatch:
#      * scheduled_task_dispatched (before runner)
#      * scheduled_task_completed (ok=True)
#      * scheduled_task_failed (ok=False or runner raised)
#      * scheduled_task_circuit_breaker_tripped (exactly once when
#        consecutive_failures first crosses max_consecutive_failures)
#    All emits go through chain.append("event_type", payload,
#    agent_dna=None). Failed audit appends are logged but never
#    propagate — scheduler keeps running even if the chain is down.
#    New _redact_outcome helper drops large/noisy fields from
#    runner outcomes before chaining to keep /audit/tail responsive.
#
# 5. daemon/app.py lifespan wires it all together:
#      from .scheduler.task_types import tool_call_runner
#      ...
#      scheduler.register_task_type("tool_call", tool_call_runner)
#    Also added "app": app to scheduler context so runners can
#    reach lazily-built subsystems via app.state.
#
# 6. tests/unit/test_scheduler_runtime.py +10 tests:
#      * test_dispatch_emits_dispatched_then_completed_on_success
#      * test_dispatch_emits_dispatched_then_failed_on_failure
#      * test_dispatch_emits_breaker_tripped_exactly_once_on_threshold
#      * test_dispatch_audit_emit_failure_does_not_break_scheduler
#      * test_dispatch_no_audit_chain_in_context_is_silent
#      * test_redact_outcome_keeps_small_fields
#      * test_redact_outcome_drops_large_fields
#      * test_tool_call_runner_rejects_missing_required_keys
#      * test_tool_call_runner_rejects_missing_context
#      * test_tool_call_runner_handles_agent_lookup_failure
#
# VERIFICATION
#
# Sandbox: 2112 unit tests pass (40 in test_scheduler_runtime.py;
# zero regressions in deps.py refactor).
#
# Host (post-commit, requires daemon restart):
#   curl -s http://127.0.0.1:7423/scheduler/status | python3 -m json.tool
# Should show:
#   "registered_runners": ["tool_call"]
# (was [] before this commit). With config/scheduled_tasks.yaml
# absent, task_count is still 0; configure to fire actual dispatches.
#
# WHAT'S STILL OUTSTANDING (per ADR-0041)
#
# T4: scenario task type runtime (multi-step birth + seed + iterate
#   + archive). Burst 90.
# T5: SQLite v13 persistence (scheduled_task_state table). Burst 91.
# T6: Operator control endpoints (/scheduler/tasks/{id}/trigger,
#   enable, disable, reset). Burst 92.
#
# After T6, the loop is fully operator-controllable: configure ->
# observe -> intervene without daemon restarts. The roadmap doc
# (Burst 87) tracks the full sequence.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 89 — ADR-0041 T3: tool_call runner + scheduler audit emit ==="
echo
clean_locks
git add src/forest_soul_forge/daemon/scheduler/task_types/__init__.py
git add src/forest_soul_forge/daemon/scheduler/task_types/tool_call.py
git add src/forest_soul_forge/daemon/scheduler/runtime.py
git add src/forest_soul_forge/daemon/deps.py
git add src/forest_soul_forge/daemon/app.py
git add tests/unit/test_scheduler_runtime.py
git add commit-burst89-tool-call-runner.command
clean_locks
git status --short
echo
clean_locks
git commit -m "feat(scheduler): tool_call task runner + audit emit (ADR-0041 T3)

Closes ADR-0036 T4 (Verifier Loop scheduled scans). The scheduler
substrate from Burst 86 now has a concrete task-type runner;
operators can configure config/scheduled_tasks.yaml entries with
type: tool_call and the daemon will dispatch them on cadence
through the standard ToolDispatcher.

Components landed:

1. daemon/scheduler/task_types/tool_call.py — async runner that:
   - validates required config keys (agent_id, tool_name, tool_version)
   - looks up the agent via registry.get_agent
   - builds/retrieves the cached dispatcher via the new
     build_or_get_tool_dispatcher helper
   - composes a daily-rotating session_id (sched-<agent>-<tool>-<YYYYMMDD>
     UTC) per ADR-0041 rate-limit mitigation — keeps per-day counter
     semantics without exhausting max_calls_per_session over weeks
   - holds the daemon's write_lock for the dispatch duration
   - maps DispatchSucceeded/Refused/PendingApproval/Failed to the
     scheduler's {ok: bool, ...} outcome shape
   - resolves ADR-0041 open-question (a): PendingApproval surfaces
     as failure rather than silently queueing. Operator visibility
     > convenience; v0.5 can revisit if scheduled-call-needs-approval
     is a real workflow.

2. daemon/deps.py refactor: extracted build_or_get_tool_dispatcher
   from get_tool_dispatcher. Same construction logic, parameterized
   by an app-like object instead of a Request. New
   ToolDispatcherUnavailable exception lets non-HTTP callers
   (the scheduler runner) handle deps-not-ready without spoofing
   a Request. The HTTP dep is now a 5-line wrapper that catches
   the exception and raises 503.

3. daemon/scheduler/runtime.py: six audit events now emitted by
   Scheduler._dispatch:
     - scheduled_task_dispatched (before runner)
     - scheduled_task_completed (ok=True)
     - scheduled_task_failed (ok=False or runner raised)
     - scheduled_task_circuit_breaker_tripped (exactly once on
       first threshold crossing)
   All emits flow through chain.append(event_type, payload,
   agent_dna=None). Failed appends are logged + swallowed —
   scheduler keeps running even if the chain is down. New
   _redact_outcome helper strips large/noisy fields (raw LLM
   blobs, deeply-nested metadata) from outcomes before logging
   so /audit/tail stays fast.

4. daemon/app.py lifespan: registers the runner via
   scheduler.register_task_type('tool_call', tool_call_runner)
   immediately after the Scheduler() constructor. Also adds 'app': app
   to the scheduler context so runners can reach lazily-built
   subsystems through app.state without import gymnastics.

5. tests/unit/test_scheduler_runtime.py +10 tests covering:
   - the four audit emit shapes (dispatched/completed/failed/tripped)
   - chain-failure tolerance + chain-absent tolerance
   - _redact_outcome keep/drop semantics
   - tool_call_runner config-validation + missing-context +
     agent-lookup-failure paths

Verification:
- Sandbox: PYTHONPATH=src python3 -m pytest tests/unit
  → 2112 passed, 3 skipped, 1 xfailed. Zero regressions in the
  deps.py refactor; new tests all pass.
- Host (post-restart): /scheduler/status will report
  registered_runners=['tool_call']. With config/scheduled_tasks.yaml
  absent, task_count stays 0 — configure the file to fire real
  dispatches.

Outstanding ADR-0041 work:
- T4: scenario task runner (multi-step birth+seed+iterate+archive)
- T5: SQLite v13 persistence (scheduled_task_state)
- T6: operator control endpoints (trigger/enable/disable/reset)

The roadmap doc (Burst 87) is the canonical sequence."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 89 landed. ADR-0041 T3 closes ADR-0036 T4 — the verifier"
echo "loop's deferred scheduler substrate is now real."
echo ""
echo "Restart the daemon to pick up the new runner registration."
echo ""
read -rp "Press Enter to close..."
