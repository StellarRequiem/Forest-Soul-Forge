#!/bin/bash
# Burst 142 — fix DispatchFailed.reason AttributeError in scheduler runner.
#
# Surfaced live 2026-05-05 by activate-scheduled-tasks.command (B141).
# Within 15 minutes of activation, dashboard_watcher_healthz_5m had
# already tripped its circuit breaker after 3 consecutive crashes.
#
# Root cause:
#   src/forest_soul_forge/daemon/scheduler/task_types/tool_call.py:209
#   referenced `outcome.reason` on a DispatchFailed dataclass.
#   DispatchFailed has fields tool_key / exception_type / audit_seq —
#   no .reason attribute. The line raised AttributeError every time a
#   dispatch crashed, masking the real failure with a runner crash.
#
#   The runner's exception bubbled to scheduler/runtime.py:417's outer
#   try/except, which counted it as a "task failure". Three of those
#   tripped the circuit breaker in <15 min for any actively-firing
#   schedule. Status_reporter (24h schedule) hadn't fired yet but
#   would have hit the same wall on first run.
#
# What ships:
#
#   src/forest_soul_forge/daemon/scheduler/task_types/tool_call.py
#     - Line 209: outcome.reason → outcome.exception_type +
#       contextual fields (tool_key + audit_seq) so log readers know
#       which tool errored AND where to find the full traceback in
#       the audit chain.
#     - Inline comment documenting the bug + fix date so future
#       maintainers understand why this looks like deliberate
#       error-message composition rather than minimal change.
#
#   fix-bug1-restart-and-reset.command (new at repo root) — runtime
#     steps to apply the fix to the live system: launchctl
#     kickstart -k dev.forest.daemon, wait for /healthz, POST
#     /scheduler/tasks/{id}/reset for the 3 active tasks to clear
#     their breakers, fire one trigger to confirm.
#
#   diagnose-chat.command + dump-err-log.command (new at repo root)
#     — diagnostic tooling used to surface this bug. Worth keeping
#     as permanent operator utilities for future investigations.
#
#   .gitignore — adds _diagnostic_*.txt (ephemeral session-specific
#     log dumps).
#
# Verified live 2026-05-05:
#   - All 3 active scheduled tasks reset via POST /reset
#   - dashboard_watcher_healthz_5m trigger returned HTTP 200,
#     ok=true (runner ran cleanly, no AttributeError)
#   - Scheduler /tasks shows breaker_open=false on all 3
#
# Post-fix discovery: the underlying dispatch (llm_think) still
# returns DispatchFailed. Bug #1 fix unmasks Bug #2/#3 (SQLite
# InterfaceError on chat reads + ConversationOut all-None) which
# appears related — same code area, same root-cause hypothesis.
# Tracked as Task #22 for separate investigation.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/scheduler/task_types/tool_call.py \
        fix-bug1-restart-and-reset.command \
        diagnose-chat.command \
        dump-err-log.command \
        .gitignore \
        dev-tools/commit-bursts/commit-burst142-scheduler-dispatchfailed-fix.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(scheduler): DispatchFailed has no .reason attribute (B142)

Burst 142. Surfaced live 2026-05-05 by activate-scheduled-tasks
(B141). Within 15 min of going live, dashboard_watcher_healthz_5m
tripped its circuit breaker after 3 consecutive crashes.

Root cause:
  src/forest_soul_forge/daemon/scheduler/task_types/tool_call.py:209
  referenced outcome.reason on DispatchFailed. DispatchFailed has
  tool_key / exception_type / audit_seq — no .reason. Line raised
  AttributeError every time a dispatch crashed, masking the real
  failure as a runner exception. Three of those tripped the breaker
  for any actively-firing schedule.

Ships:
- tool_call.py:209: outcome.reason → outcome.exception_type +
  contextual fields (tool_key + audit_seq). Inline comment
  documents the bug + fix date.
- fix-bug1-restart-and-reset.command: runtime steps to apply the
  fix live (kickstart -k daemon, reset 3 breakers, trigger
  confirm).
- diagnose-chat.command + dump-err-log.command: diagnostic
  tooling used to surface this bug. Kept as permanent operator
  utilities.
- .gitignore: adds _diagnostic_*.txt (ephemeral log dumps).

Verified live 2026-05-05:
- 3 active scheduled tasks reset via POST /reset
- dashboard_watcher trigger returned HTTP 200, ok=true (runner ran
  cleanly, no AttributeError)
- /scheduler/tasks shows breaker_open=false on all 3

Post-fix discovery: underlying llm_think dispatch still returns
DispatchFailed (different bug, was masked by B142). Likely related
to a separate SQLITE_MISUSE bug in conversations.py:843 — same
code area, same root-cause hypothesis. Tracked as Task #22 for
separate investigation."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 142 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
