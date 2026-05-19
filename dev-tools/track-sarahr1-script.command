#!/usr/bin/env bash
# Track commit-sarahr1-review.command — follow-up commit for audit trail.
# Matches v0.1.1 precedent (commit-audit-v0.1.1.command and
# tag-v0.1.1.command are both tracked under the v0.1.1 commit).
# This is a one-shot; the script self-deletes after pushing.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
}

echo "=== Track commit-sarahr1-review.command ==="
echo

clean_locks
git add commit-sarahr1-review.command track-sarahr1-script.command
clean_locks
git commit -m "Track sarahr1-review commit scripts for audit trail

Both scripts (commit-sarahr1-review.command + this self-tracking
helper) get committed alongside the review-absorption commit they
produced. Matches v0.1.1 precedent — commit-audit-v0.1.1.command
and tag-v0.1.1.command are both tracked under the v0.1.1 commit
so the script that produced the commit is recoverable from the
chain."
clean_locks
git push origin main
clean_locks

echo
git log -1 --oneline
echo
echo "Tracked. Script self-deleting..."
rm -- "$0"
echo "Done."
echo ""
read -rp "Press Enter to close..."
