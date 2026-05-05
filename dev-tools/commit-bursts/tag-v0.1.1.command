#!/usr/bin/env bash
# Tag v0.1.1 — audit + hardening release.
#
# Run AFTER clean-git-locks.command + a5-finalize.command have landed
# the working-tree commits to origin. This script just creates the
# annotated tag and pushes it.
#
# Idempotent-ish: if the tag already exists locally, we don't overwrite
# it; we just try the push (which is also idempotent — origin will
# either accept or report "already up to date").

set -euo pipefail

cd "$(dirname "$0")"

echo "=== Tag v0.1.1 — audit + hardening release ==="
echo

# Verify we're at a clean tree before tagging.
if [ -n "$(git status --porcelain)" ]; then
  echo "ERROR: working tree is not clean. Run a5-finalize.command first."
  echo "Uncommitted changes:"
  git status --short
  read -rp "Press Enter to exit..."
  exit 1
fi

# Verify HEAD is on origin (commits pushed).
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main 2>/dev/null || echo "")
if [ "$LOCAL" != "$REMOTE" ]; then
  echo "WARNING: local HEAD differs from origin/main."
  echo "  local:  $LOCAL"
  echo "  origin: $REMOTE"
  read -rp "Press Enter to continue anyway, or Ctrl-C to abort: "
fi

# Create the annotated tag (skip if already exists locally).
if git tag -l v0.1.1 | grep -q v0.1.1; then
  echo "Tag v0.1.1 already exists locally — skipping create."
else
  git tag -a v0.1.1 -m "audit + hardening release

Test suite: 992 -> 1439 passing (+447, +45%); 122 broken -> 0.
Two §0-gated bug fixes (one-line each); rest is coverage closure,
decomposition, documentation, and verified-not-removed cleanup.

See CHANGELOG.md [0.1.1] entry for the full ledger."
  echo "✓ Tag v0.1.1 created locally."
fi

echo
echo "Pushing tag to origin..."
git push origin v0.1.1

echo
echo "✓ v0.1.1 tagged and pushed."
echo "Verify at: https://github.com/StellarRequiem/Forest-Soul-Forge/releases"
echo
read -rp "Press Enter to close..."
