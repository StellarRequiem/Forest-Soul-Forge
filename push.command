#!/usr/bin/env bash
# git push origin <current-branch> with a tiny status read-out before/after.
# Persistent — does NOT self-delete. Double-click from Finder.
#
# Why this exists: the Cowork sandbox can't reach github.com directly,
# so commits I make from there have to be pushed by you. This script
# turns "open Terminal, cd, push" into one double-click.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

bar() { printf "\n=== %s ===\n" "$1"; }

bar "Pre-push state"
git log --oneline -5
echo ""
echo "Branch: $(git branch --show-current)"
echo "Tracking: $(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null || echo 'no upstream set')"
echo "Ahead/behind:"
git status -sb | head -3

bar "git push"
branch="$(git branch --show-current)"
git push origin "$branch"

bar "Post-push state"
git log --oneline -3

echo ""
echo "Press return to close."
read -r _
