#!/usr/bin/env bash
# Burst 450 — automate the §5 sandbox-index-lock race away with a
# host-side WatchPaths watcher.
#
# Why this exists:
#   Sandbox-side git operations create .git/index.lock files the
#   sandbox can't unlink (FUSE mount permission asymmetry). Manual
#   clean-git-locks.command was the recovery path for every signed
#   commit in today's 15-commit day arc. Operator asked: 'is there
#   a process we could set up so it automatically cleans the lock
#   before we push the next commit?'  This burst is the answer.
#
# Three artifacts:
#   dev-tools/git-lock-watcher.command (new) — long-fire-with-loop
#     watcher. Sleeps CHECK_INTERVAL_SEC (default 5s) between checks,
#     bails after MAX_WAIT_SEC (default 60s). Removes any .git/
#     index.lock or HEAD.lock whose mtime > STALE_THRESHOLD_SEC
#     (default 15s) old. lsof check confirms no host process holds
#     the lock open before unlinking.
#   dev-tools/launchd/dev.forest.git-lock-watcher.plist.template (new)
#     LaunchAgent with WatchPaths on .git/index.lock. Event-driven —
#     fires only when sandbox touches the lock. RunAtLoad=false +
#     no KeepAlive: each fire runs the watcher once + exits.
#   dev-tools/install-launchd-git-lock-watcher.command (new)
#     Idempotent installer mirroring the B439/B441/B442 pattern.
#     Bails clean if already bootstrapped.
#
# Also folds in two orphan files from B449's recovery:
#   dev-tools/commit-bursts/commit-burst449-phase-ii-welcome-tour.command
#     The original B449 script that hit the §5 lock race. Recovery
#     used commit-burst449b-simple.command (already in B449 itself).
#     Folding 449 (original) in so the audit trail is complete.
#   dev-tools/try-push-b449.command
#     One-off push helper from the same recovery. Folds it in here.
#
# Verified end-to-end:
#   * Installed launch agent (now 7 of 7 dev.forest plists live).
#   * Touched .git/index.lock from sandbox to simulate the bug.
#   * Watcher fired at the touch, checked age every 5s, removed
#     the lock at the 15s mark.
#   * .run/git-lock-watcher.log records every fire + decision.
#
# Net effect after this lands: sandbox-stale locks get cleaned up
# automatically within ~15-20s of the lock becoming stale. The
# operator no longer needs to run clean-git-locks.command between
# commit-burst-* runs.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: every signed commit today hit the §5 lock race at
#     least once; recovery requires manual clean-git-locks step;
#     operator workflow has a stutter every time.
#   Prove non-load-bearing for kernel: dev-tools/ scripts + launchd
#     plist. No source code, no schema, no events, no routes.
#   Prove alternative: keep manual cleanup (rejected — user
#     explicitly asked for automation); polling-based watcher
#     (rejected — WatchPaths is event-driven, dormant 99.99%
#     of the time); aggressive unlink-on-fire (rejected — would
#     race host commits in progress).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "==========================================================="
echo "Burst 450 — host-side git-lock watcher (auto-clean §5 race)"
echo "==========================================================="
echo

echo "Pre-commit: clear stale .git locks..."
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null && echo "  cleared" || echo "  none"
echo

git add dev-tools/git-lock-watcher.command
git add dev-tools/launchd/dev.forest.git-lock-watcher.plist.template
git add dev-tools/install-launchd-git-lock-watcher.command
git add dev-tools/commit-bursts/commit-burst449-phase-ii-welcome-tour.command
git add dev-tools/try-push-b449.command
git add dev-tools/commit-bursts/commit-burst450-git-lock-watcher.command

echo "Staged:"
git diff --cached --stat
echo

git commit -m "chore(ops): host-side git-lock watcher auto-cleans §5 sandbox-stale locks (B450)

Closes the §5 sandbox-index-lock race that every signed commit in
today's 15-commit day arc had to manually recover from. Sandbox
git operations create .git/index.lock files the sandbox can't
unlink (FUSE permission asymmetry); host-side cleanup was
manual via clean-git-locks.command between bursts.

Three new artifacts:

  dev-tools/git-lock-watcher.command
    Loop watcher. Sleeps CHECK_INTERVAL_SEC=5s between checks,
    bails after MAX_WAIT_SEC=60s. Removes any .git/index.lock or
    HEAD.lock whose mtime > STALE_THRESHOLD_SEC=15s old. Confirms
    no host process holds the lock via lsof before unlinking.
    Loop is important: a single fire's first check often sees the
    lock under-threshold, and WatchPaths doesn't re-trigger
    without a file-change event — so the watcher itself must
    re-check.

  dev-tools/launchd/dev.forest.git-lock-watcher.plist.template
    LaunchAgent with WatchPaths on .git/index.lock. Event-driven
    — fires only when sandbox actually leaves a stale lock. No
    polling overhead. RunAtLoad=false; no KeepAlive (each fire
    runs once and exits).

  dev-tools/install-launchd-git-lock-watcher.command
    Idempotent installer mirroring B439/B441/B442/B449 pattern.

Folds in two orphan files from B449's recovery:
  dev-tools/commit-bursts/commit-burst449-phase-ii-welcome-tour.command
    The B449 original script that hit the §5 race. Recovery used
    commit-burst449b-simple (already in B449's history). Folding
    the original in completes the audit trail.
  dev-tools/try-push-b449.command
    One-off push helper from same recovery.

Verified end-to-end:
  * Installed launch agent. Inventory now 7 of 7 dev.forest plists
    live (added git-lock-watcher to the 6 from B442).
  * Touched .git/index.lock from sandbox to simulate the bug.
  * Watcher fired at the touch event; checked age at +5s (5s under
    threshold), +10s (10s under), +15s (REMOVED).
  * .run/git-lock-watcher.log records every fire + decision with
    UTC timestamps + lock-age + action taken.

Net effect: sandbox stale locks cleaned up automatically within
~15-20s of becoming stale. Operator no longer needs to run
clean-git-locks.command between commit-burst-* runs. The §5 race
documented in CLAUDE.md becomes a substrate non-event.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: every commit today hit the race; operator manual
    recovery is stutter every burst.
  Prove non-load-bearing: dev-tools + launchd. No source change.
  Prove alternative: keep manual (rejected; user asked for
    automation); polling (rejected; WatchPaths is event-driven);
    immediate unlink (rejected; races host commits)." || { echo "commit failed"; exit 1; }

echo
echo "Post-commit signature status:"
git log --format='%h %G? %s' -3
echo

echo "Pushing B450..."
git push origin main || { echo "push failed"; exit 1; }

echo
echo "Done. B450 pushed."
echo
echo "Press any key to close."
read -n 1 || true
