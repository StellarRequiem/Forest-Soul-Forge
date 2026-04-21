#!/usr/bin/env bash
# scripts/initial_push.sh
#
# One-shot initial commit + push for the Forest Soul Forge repo.
# Safe to read before running — it does exactly what's below, nothing more.
#
# Run from: ~/Projects/Forest-Soul-Forge
# Requires: git with keychain credentials configured for github.com
#
# What this does:
#   1. Removes any stale .git dir (left over from sandbox init attempt)
#   2. git init -b main
#   3. Sets REPO-LOCAL user.name / user.email (your global .gitconfig has
#      smart/curly quotes around the email — fix that separately when you can)
#   4. Adds the GitHub remote
#   5. Stages everything, commits, and pushes to main

set -euo pipefail

REPO_DIR="$HOME/Projects/Forest-Soul-Forge"
REMOTE_URL="https://github.com/StellarRequiem/Forest-Soul-Forge.git"

if [[ "$PWD" != "$REPO_DIR" ]]; then
  echo "ERROR: run this from $REPO_DIR (you are in $PWD)"
  exit 1
fi

# 1. Clean up any partial .git from the sandbox init attempt
if [[ -d .git ]]; then
  echo "Removing existing .git directory..."
  rm -rf .git
fi

# 2. Fresh init on main
git init -b main

# 3. Repo-local identity (avoids touching your global .gitconfig)
git config user.name "Alexander Price"
git config user.email "alexanderprice91@yahoo.com"

# 4. Remote
git remote add origin "$REMOTE_URL"

# 5. Stage and commit
git add -A
git status --short

git commit -m "chore: initial repo scaffolding

Set up directory structure, documentation skeleton, and project metadata
for Forest Soul Forge — a local-first blue-team personal agent factory.

- src/forest_soul_forge/ package layout (core, agents, soul, ui)
- docs/ split into vision, architecture, decisions (ADRs), audits, changelog
- Apache 2.0 license
- pyproject.toml with ruff + pytest + mypy dev deps
- Preserved original handoff brief at docs/vision/handoff-v0.1.md
  with open questions appended for later resolution

No functional code yet. Phase 1 (trait tree design) starts in the next commit."

# 6. Push
echo
echo "About to push to $REMOTE_URL"
echo "If this is the wrong repo, Ctrl-C now."
read -rp "Press Enter to push, or Ctrl-C to abort: "

git push -u origin main

echo
echo "Done. Verify at: https://github.com/StellarRequiem/Forest-Soul-Forge"
