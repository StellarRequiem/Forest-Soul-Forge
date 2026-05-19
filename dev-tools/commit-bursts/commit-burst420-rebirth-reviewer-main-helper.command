#!/bin/bash
# Burst 420 - rebirth-reviewer-main helper for B416 allowed_paths pickup.
#
# B416 added allowed_paths defaults to code_reviewer's constitution
# template. Existing Reviewer-Main was born before that change;
# constitution-immutability means it can't pick up the new defaults.
# Helper script automates the archive + rebirth cycle.
#
# Idempotent: archive endpoint returns a clean status if already
# archived; birth-triune-main.command only births missing agents.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/rebirth-reviewer-main.command \
        dev-tools/commit-bursts/commit-burst420-rebirth-reviewer-main-helper.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(triune): rebirth-reviewer-main helper for B416 (B420)

Burst 420. B416 added allowed_paths defaults to code_reviewer
template. Existing Reviewer-Main was born before that change;
immutability means it can't pick up new defaults.

Helper script: archive existing Reviewer-Main + re-run idempotent
birth-triune-main.command (which only births missing agents).
Same B376 pattern as chaz/Kraine/Victor.

After landing:
  bash dev-tools/rebirth-reviewer-main.command
  (verify allowed_paths landed in fresh constitution)
  bash dev-tools/run-reviewer-review.command
  (Option C should now reach status=succeeded)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 420 complete - rebirth helper shipped ==="
echo "=========================================================="
echo "Press any key to close."
read -n 1 || true
