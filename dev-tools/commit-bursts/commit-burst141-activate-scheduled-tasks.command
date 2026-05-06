#!/bin/bash
# Burst 141 — scheduled-tasks activation helper + .gitignore.
#
# Follow-on to B140 (specialist stable). After birthing the 6
# specialists, T6 noted that scheduled-task activation was
# operator-side: copy the .example, substitute IDs, restart the
# daemon. This burst ships the wrapper so that's a one-double-click
# operation instead of a manual sequence.
#
# What ships:
#
#   activate-scheduled-tasks.command (new at repo root) — Finder-
#     launchable activation script. Verifies config/scheduled_tasks.yaml
#     exists, counts enabled/disabled tasks, restarts dev.forest.daemon
#     via launchctl kickstart -k, waits up to 20s for /healthz, then
#     pulls /scheduler/status + /scheduler/tasks to confirm the
#     scheduler picked up the config and parsed it cleanly.
#
#   .gitignore — adds config/scheduled_tasks.yaml. The .example is
#     shipped (operator-facing template); the activated file is
#     local-only because instance_ids differ per Forest installation.
#     Same pattern as .env / .env.example.
#
# Bug found + fixed mid-flight: the first version of
# activate-scheduled-tasks.command didn't `cd` to the script's
# directory. Finder launches .command files with cwd=\$HOME, so the
# preflight `if [[ ! -f config/scheduled_tasks.yaml ]]` resolved
# against \$HOME and reported the file missing even though it existed
# at the repo root. Fix: standard HERE=\"\$(cd \"\$(dirname \"\$0\")\" && pwd)\";
# cd \"\$HERE\" prelude that the other repo-root scripts use. Worth
# noting because if a future commit-burst*.command lands without the
# prelude, this will silently break the same way.
#
# Verified live 2026-05-05:
#   - Daemon restart via launchctl kickstart -k
#   - /healthz back up in 2s
#   - /scheduler/status: running=true, task_count=6, tasks_enabled=3,
#     tasks_breaker_open=0
#   - /scheduler/tasks: 3 enabled tasks have real next_run_at
#     timestamps; 3 disabled show fire-on-first-tick (correct — they're
#     not scheduled because disabled)
#
# What this commit does NOT include:
#   - config/scheduled_tasks.yaml itself — gitignored as of this
#     commit. The activated yaml on this machine has the real
#     instance_ids from the B140 specialist births, but those are
#     per-installation state and don't belong in version control.
#
# Closes the operator-side activation step from T6 follow-on. The
# scheduler is now actively running; first dashboard_watcher healthz
# poll will fire ~5 min after this commit lands.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add activate-scheduled-tasks.command \
        .gitignore \
        dev-tools/commit-bursts/commit-burst141-activate-scheduled-tasks.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(ops): scheduled-tasks activation helper + .gitignore (B141)

Burst 141. Follow-on to B140 specialist stable. After birthing the
6 specialists, T6 noted that activation was operator-side: copy
the .example, substitute IDs, restart the daemon. This burst ships
the wrapper so that's a one-double-click operation.

Ships:

- activate-scheduled-tasks.command (new at repo root): Finder-
  launchable activation script. Verifies config/scheduled_tasks.yaml
  exists, counts enabled/disabled, restarts dev.forest.daemon via
  launchctl kickstart -k, waits up to 20s for /healthz, then pulls
  /scheduler/status + /scheduler/tasks to confirm the scheduler
  parsed the config cleanly.

- .gitignore: adds config/scheduled_tasks.yaml. The .example ships;
  the activated file is local-only because instance_ids differ per
  Forest installation. Same pattern as .env / .env.example.

Bug found + fixed mid-flight: first run failed because the script
didn't cd to its own directory. Finder launches .command with
cwd=HOME, so the preflight resolved against HOME and missed the
yaml at the repo root. Standard HERE=...; cd HERE prelude added.
Worth noting in case a future commit-burst*.command lands without
the prelude — same silent failure mode.

Verified live 2026-05-05:
- Daemon restart via launchctl kickstart -k
- /healthz back up in 2s
- /scheduler/status: running=true, task_count=6, tasks_enabled=3,
  tasks_breaker_open=0
- /scheduler/tasks: 3 enabled (status_reporter daily, dashboard_watcher
  every 5m, signal_listener hourly) have real next_run_at; 3
  disabled show fire-on-first-tick

Doesn't include config/scheduled_tasks.yaml itself — gitignored
as of this commit. The activated file on this machine has the
real instance_ids from the B140 specialist births, but those are
per-installation state.

Closes T6 follow-on. Scheduler is actively running; first
dashboard_watcher healthz poll fires ~5 min after this commit."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 141 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
