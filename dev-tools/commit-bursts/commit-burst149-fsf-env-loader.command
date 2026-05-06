#!/bin/bash
# Burst 149 — operator-script env loader (T25 follow-on).
#
# B148 made FSF_API_TOKEN required for all write endpoints. Existing
# operator scripts had ${FSF_API_TOKEN:-} fallback that returned
# empty when the var wasn't in shell env — pre-B148 fine, post-B148
# = 401 on every write. The fix: a small sourceable helper that
# loads the token from .env if not already in shell, plus injecting
# `source dev-tools/_fsf-env.sh` into 9 active operator scripts.
#
# What ships:
#
#   dev-tools/_fsf-env.sh (new) — sourceable bash helper. Reads
#     FSF_API_TOKEN from ./.env if not already in shell env. Re-source-
#     safe: shell-set value wins. Quiet on success; logs only when the
#     token can't be resolved (.env missing, or .env has no token line).
#
#   9 active operator scripts (sed-injected) — adds one line right
#     after `cd "$HERE"` (or after `set -uo pipefail` for scripts
#     without the cd anchor). Affected:
#       - diagnose-chat.command
#       - verify-b143.command
#       - verify-b144.command
#       - verify-b148.command
#       - verify-t22-scheduler-post-b143.command
#       - fix-bug1-restart-and-reset.command
#       - birth-specialist-stable.command
#       - birth-dashboard-watcher.command (HERE/cd added too)
#       - activate-scheduled-tasks.command
#     Plus scripts/birth-specialist-stable.sh — same pattern using
#     ${BASH_SOURCE[0]} so it works whether sourced or executed.
#
# What doesn't ship:
#
#   Historical scripts (live-test-*.command, swarm-bringup.command,
#     scripts/security-*.sh, etc.) — operator can update those if
#     they re-run them. Skipping to keep this commit focused on
#     scripts that are actively used post-B148.
#
#   Old commit-burst*.command scripts — past-arc artifacts, not
#     re-runnable.
#
# Effect: any of the 9 updated operator scripts now works without
# manual `export $(grep ^FSF_API_TOKEN .env)` step. The helper reads
# .env transparently. If the operator already has FSF_API_TOKEN in
# shell env (e.g., for testing override), the helper no-ops.
#
# Verified live 2026-05-05: verify-b148.command run after B149
# injection — daemon restart, .env read via helper, write endpoint
# properly authenticated. No regression.
#
# Migration note: operator who runs an UN-updated script (e.g.,
# live-test-fizzbuzz.command) post-B148 will get 401s. Two paths:
# (a) `export $(grep ^FSF_API_TOKEN .env)` once per shell session
# before running, OR (b) add the source line to the script (one
# sed call following the pattern in this commit's helper).
#
# Closes B148's operator-side migration story. Auth-by-default works
# end-to-end for the 9 most-used operator scripts.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/_fsf-env.sh \
        diagnose-chat.command \
        verify-b143.command \
        verify-b144.command \
        verify-b148.command \
        verify-t22-scheduler-post-b143.command \
        fix-bug1-restart-and-reset.command \
        birth-specialist-stable.command \
        birth-dashboard-watcher.command \
        activate-scheduled-tasks.command \
        scripts/birth-specialist-stable.sh \
        dev-tools/commit-bursts/commit-burst149-fsf-env-loader.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(ops): _fsf-env.sh helper + inject into 9 operator scripts (B149)

Burst 149. B148 follow-on. Operator-script env loader so existing
scripts auto-pick up FSF_API_TOKEN from .env without manual export.

Background: B148 made FSF_API_TOKEN required for all write endpoints.
Existing operator scripts used \${FSF_API_TOKEN:-} fallback that
returned empty when the var wasn't in shell env — pre-B148 fine,
post-B148 = 401 on every write.

Ships:
- dev-tools/_fsf-env.sh: sourceable helper. Reads FSF_API_TOKEN from
  ./.env if not in shell env. Re-source-safe (shell-set value wins).
  Quiet on success.
- 9 active operator scripts updated (sed-injected) to source the
  helper after cd \"\$HERE\":
    diagnose-chat, verify-b143, verify-b144, verify-b148,
    verify-t22-scheduler-post-b143, fix-bug1-restart-and-reset,
    birth-specialist-stable (.command + .sh), birth-dashboard-watcher,
    activate-scheduled-tasks

Verified live: verify-b148.command runs end-to-end without manual
export after B149 injection. Helper transparent.

Skipped (intentional): historical scripts (live-test-*, old
commit-burst*) not re-runnable; operator updates if/when they re-use.

Closes B148's operator-side migration. Auth-by-default works
end-to-end for the most-used operator scripts."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 149 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
