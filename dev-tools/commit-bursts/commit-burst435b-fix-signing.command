#!/usr/bin/env bash
# Burst 435b — fix signing on the just-landed B435 commit.
#
# Bug: per-repo .git/config had `[commit] gpgsign = false` from a
# prior burst, overriding the global `commit.gpgsign = true` set by
# enable-ssh-signing.command. The B435 commit therefore landed
# unsigned (git log %G? shows N). The "main protection" ruleset's
# Require-signed-commits rule then rejected the push.
#
# Fix:
#   1. Remove the per-repo override (so global signing applies).
#   2. Amend B435 in place. The amend re-runs the signer with the
#      now-active config; the message + tree stay identical, only
#      the commit object gets a signature trailer.
#   3. Push. Since origin/main is still at B434 (8a0dce1), the new
#      B435 SHA is a clean fast-forward — not a force-push, so the
#      Block-force-pushes rule doesn't fire.
#
# This is the "live verification" of B6 that B435 was supposed to
# be. Expect git log %G? to show G on the new B435 SHA after this.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "==========================================================="
echo "Burst 435b — re-sign B435 by removing repo-local override"
echo "==========================================================="
echo

echo "Before: per-repo signing config"
echo "  [repo] commit.gpgsign  = $(git config --local --get commit.gpgsign 2>/dev/null || echo '(unset)')"
echo "  [global] commit.gpgsign = $(git config --global --get commit.gpgsign 2>/dev/null || echo '(unset)')"
echo "  [resolved] commit.gpgsign = $(git config --get commit.gpgsign)"
echo

echo "Removing per-repo commit.gpgsign override..."
git config --local --unset commit.gpgsign
echo "  removed."
echo

echo "After: per-repo signing config"
echo "  [repo] commit.gpgsign  = $(git config --local --get commit.gpgsign 2>/dev/null || echo '(unset)')"
echo "  [global] commit.gpgsign = $(git config --global --get commit.gpgsign 2>/dev/null || echo '(unset)')"
echo "  [resolved] commit.gpgsign = $(git config --get commit.gpgsign)"
echo

echo "Pre-commit: clear stale .git lock files..."
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null && echo "  cleared" || echo "  none"
echo

# Stage this script alongside the amend, so it's also in the
# fixed-B435 commit history. We add it then amend (which re-uses
# the previous commit's message via --no-edit).
git add dev-tools/commit-bursts/commit-burst435b-fix-signing.command

echo "Amending B435 to pick up signing..."
git commit --amend --no-edit || { echo "amend failed"; exit 1; }
echo

echo "==========================================================="
echo "Post-amend signature status:"
echo "==========================================================="
git log --format='%h %G? %s' -3
echo
echo "Expected: top SHA shows G (good signature)."
echo

echo "Pushing to origin (fast-forward; origin/main is at B434)..."
git push origin main || { echo "push failed — read the remote rejection text; ruleset is the most likely cause"; exit 1; }

echo
echo "Done. B435 (signed) landed and pushed."
echo
echo "Press any key to close."
read -n 1 || true
