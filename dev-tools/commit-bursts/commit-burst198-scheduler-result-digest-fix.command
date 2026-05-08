#!/bin/bash
# Burst 198 — fix scheduler audit-emit crash on result_digest
# (latent bug surfaced post-Tahoe-26.4.1 reboot when the daemon
# tried to flush in-flight scheduled tasks on shutdown).
#
# What ships:
#
#   src/forest_soul_forge/daemon/scheduler/task_types/tool_call.py:
#     Line 204: outcome.result.result_digest -> outcome.result.result_digest()
#     The dispatcher elsewhere calls result_digest() (with parens) — see
#     dispatcher.py L797, L1214, L1627. The scheduler missed the parens
#     and was passing a bound method into the audit event payload, which
#     blew up at chain.append's json.dumps with:
#       TypeError: Object of type method is not JSON serializable
#       when serializing dict item 'result_digest'
#       when serializing dict item 'outcome'
#       when serializing dict item 'event_data'
#     Triggered during scheduled_task_completed audit emit; caught by
#     _emit_audit's broad except + logged but the event was lost.
#
#   src/forest_soul_forge/daemon/scheduler/scenario_runtime.py:
#     Line 415: same fix in the scenario runner's into-context capture.
#     Less critical (scenario context isn't audit-chained directly) but
#     same shape of bug.
#
#   dev-tools/force-restart-daemon.command: NEW.
#     Aggressive port-7423 cleanup + start.command handoff. Handles cases
#     stop.command misses: launchd-managed daemons that respawn on kill,
#     processes in CLOSE_WAIT/TIME_WAIT not caught by the LISTEN-only
#     filter, stray uvicorn processes outside the standard supervisor.
#     Surfaced because the post-Tahoe reboot left a zombie holding 7423
#     that stop.command couldn't shake — operator-tooling gap fixed.
#
# Per ADR-0044 D3: zero ABI changes. Pre-B198 daemons would crash on
# scheduled task completion when the chain tried to hash; post-B198
# they emit cleanly. Behavior is now correct rather than silently
# losing audit events.
#
# Per ADR-0001 D2: no identity surface touched.
#
# Verification:
#   - daemon back online after force-restart-daemon
#   - wallpaper widgets pulling real data (200 OKs visible in
#     .run/daemon.log for /healthz, /agents, /audit/tail)
#   - bug found by tracing daemon.log after force-stop+start cycle
#     hit a port-bind error and surfaced the latent shutdown crash

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/scheduler/task_types/tool_call.py \
        src/forest_soul_forge/daemon/scheduler/scenario_runtime.py \
        dev-tools/force-restart-daemon.command \
        dev-tools/commit-bursts/commit-burst198-scheduler-result-digest-fix.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(scheduler): result_digest method-vs-value bug + ops tooling (B198)

Burst 198. Two-line fix to a latent scheduler bug + a force-restart
operator script.

Bug:

scheduler/task_types/tool_call.py L204 and scheduler/scenario_runtime.py
L415 both read outcome.result.result_digest WITHOUT parens — passing
a bound method into the audit event payload. chain.append's json.dumps
then died:

  TypeError: Object of type method is not JSON serializable
  when serializing dict item 'result_digest'
  when serializing dict item 'outcome'
  when serializing dict item 'event_data'

The dispatcher elsewhere correctly calls result_digest() (dispatcher.py
L797, L1214, L1627). Scheduler missed the parens. Triggered on every
scheduled_task_completed emit; the broad except in _emit_audit caught
it but the audit event was silently lost.

Fix: add the parens. Two characters per file.

Operational tooling:

dev-tools/force-restart-daemon.command — aggressive cleanup for cases
stop.command can't recover from. Unloads launchd job, kills ALL pids
on port 7423 (not just LISTEN), kills stray uvicorn processes, waits
for the port to drain, then handoff exec to start.command. Surfaced
when a post-Tahoe-26.4.1 reboot left a zombie that polite stop+start
couldn't clear.

Per ADR-0044 D3: zero ABI changes. Pre-B198 daemons silently lost
audit events on scheduled task completion; post-B198 events emit
cleanly.

Per ADR-0001 D2: no identity surface touched.

Verification: daemon back online via force-restart-daemon; wallpaper
widgets pulling 200 OKs from /healthz, /agents, /audit/tail."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 198 complete ==="
echo "=== Scheduler audit-emit crash fixed; force-restart tooling shipped. ==="
echo "Press any key to close."
read -n 1
