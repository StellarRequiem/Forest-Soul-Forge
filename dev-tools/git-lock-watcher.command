#!/usr/bin/env bash
# B450 — host-side watcher for stale .git/index.lock + .git/HEAD.lock
# files left behind by sandbox-side git operations (FUSE mount asymmetry
# means sandbox can create the lock but can't unlink it; only the host
# user can clean up).
#
# Invocation contract (launchd-driven):
#   * Bootstrapped by ~/Library/LaunchAgents/dev.forest.git-lock-watcher.plist
#   * Plist uses WatchPaths on .git/index.lock — fires when the path is
#     created OR modified. RunAtLoad=false so we don't fire on every
#     daemon restart, only on real lock events.
#   * On fire: sleep STALE_THRESHOLD_SEC (default 15s) to give legitimate
#     host git operations time to complete, then check if the lock STILL
#     exists + its age. If still there + older than the threshold,
#     remove it.
#   * Logs every action to .run/git-lock-watcher.log (sandbox-readable).
#
# Why a sleep + recheck instead of immediate unlink:
#   A host-side `git commit` takes ~1-3 seconds during which the lock is
#   legitimately held. We don't want to race-condition our own commits.
#   15 seconds is plenty for a host commit + push; sandbox-stale locks
#   are typically left over after the sandbox process exited (lock stays
#   forever). The threshold disambiguates.
#
# Why launchd WatchPaths instead of polling:
#   WatchPaths is event-driven. The watcher process is dormant 99.99%
#   of the time and only fires when sandbox actually leaves a stale
#   lock — typically a few times per session. No polling overhead.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: every signed commit this session hit the §5 lock race
#     at least once; recovery requires manual clean-git-locks.command;
#     operator-driven commit flow has a stutter every time.
#   Prove non-load-bearing for kernel: dev-tools script + launchd plist.
#     No source code change. No new event types. No registry tables.
#     Pure userspace per ADR-0044 + ADR-0082.
#   Prove alternative: ignore + keep manually clearing (rejected; user
#     explicitly asked for automation); host-side pre-commit hook
#     (rejected; hooks run too late — the lock is already held);
#     blanket-unlink-on-fire (rejected; would race host commits).

set -uo pipefail

REPO_ROOT="/Users/llm01/Forest-Soul-Forge"
cd "$REPO_ROOT" || exit 1

LOG="$REPO_ROOT/.run/git-lock-watcher.log"
mkdir -p "$(dirname "$LOG")"
STALE_THRESHOLD_SEC=${FSF_GIT_LOCK_STALE_SEC:-15}

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%dT%H:%M:%SZ')" "$1" >> "$LOG"
}

check_and_remove() {
  local lock="$1"
  if [ ! -e "$lock" ]; then
    return 0
  fi

  # File age in seconds. macOS stat syntax.
  local mtime now age
  mtime=$(stat -f %m "$lock" 2>/dev/null || echo 0)
  now=$(date +%s)
  age=$((now - mtime))

  if [ "$age" -lt "$STALE_THRESHOLD_SEC" ]; then
    log "lock present at $lock (age ${age}s; under threshold ${STALE_THRESHOLD_SEC}s) — leaving alone"
    return 0
  fi

  # Belt-and-suspenders: confirm no host process holds it open. macOS lsof
  # exits non-zero if no process has the file open.
  if command -v lsof >/dev/null 2>&1; then
    if lsof "$lock" >/dev/null 2>&1; then
      log "lock at $lock is held by a process (age ${age}s) — leaving alone"
      return 0
    fi
  fi

  # Stale. Remove.
  if rm -f "$lock" 2>>"$LOG"; then
    log "removed stale lock $lock (age ${age}s)"
  else
    log "ERROR — failed to remove $lock"
  fi
}

log "fired (PID $$, threshold ${STALE_THRESHOLD_SEC}s)"

# WatchPaths fires ONCE per file-change event. If the first check sees
# the lock still inside the threshold window (legitimate host operation
# in progress), we'd previously exit + never re-check. The lock would
# then sit forever. Loop until removed-or-timeout instead.
MAX_WAIT_SEC=${FSF_GIT_LOCK_MAX_WAIT_SEC:-60}
CHECK_INTERVAL_SEC=${FSF_GIT_LOCK_CHECK_INTERVAL_SEC:-5}
elapsed=0
while [ "$elapsed" -lt "$MAX_WAIT_SEC" ]; do
  # Brief sleep so legitimate host operations have time to complete
  # before the first check (and between subsequent re-checks).
  sleep "$CHECK_INTERVAL_SEC"
  elapsed=$((elapsed + CHECK_INTERVAL_SEC))

  # Check both index.lock and HEAD.lock; either may have been left
  # behind by sandbox-side operations.
  index_present=0
  head_present=0
  [ -e "$REPO_ROOT/.git/index.lock" ] && index_present=1
  [ -e "$REPO_ROOT/.git/HEAD.lock" ] && head_present=1
  if [ "$index_present" -eq 0 ] && [ "$head_present" -eq 0 ]; then
    log "both locks clear at ${elapsed}s — exiting"
    break
  fi

  check_and_remove "$REPO_ROOT/.git/index.lock"
  check_and_remove "$REPO_ROOT/.git/HEAD.lock"
done

if [ "$elapsed" -ge "$MAX_WAIT_SEC" ]; then
  log "max wait ${MAX_WAIT_SEC}s reached — exiting (locks may still exist)"
fi

log "done"
