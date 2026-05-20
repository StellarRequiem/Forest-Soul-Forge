#!/usr/bin/env bash
# B449 — simple commit path, no fancy framing. Commits whatever is
# staged + the additions for B449. Tees output so we can post-mortem.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

OUT=/tmp/forest-b449-commit.log
{
  echo "=== $(date) — B449 commit attempt ==="
  echo

  echo "Clearing locks..."
  rm -f .git/index.lock .git/HEAD.lock
  ls -la .git/index.lock 2>&1 | head -1

  echo "Adding files..."
  git add frontend/js/tour.js
  git add dev-tools/commit-bursts/commit-burst449-phase-ii-welcome-tour.command
  git add dev-tools/commit-bursts/commit-burst449b-simple.command

  echo "Staged:"
  git diff --cached --stat

  echo "Committing..."
  git commit -m "feat(frontend): Phase II — welcome tour orients to vertical sidebar (B449)

Adds a 'welcome' tour to frontend/js/tour.js that walks a first-
visit operator through the four sidebar groups introduced in B448
(Build / Run / Observe / Govern), the live status bar, and the
path to the first concrete task (birth an agent in Forge).

7 steps:
  1. Centered intro (no anchor).
  2. BUILD group title (forge / skills / tools / marketplace).
  3. RUN group title (agents / approvals / chat / voice).
  4. OBSERVE group title (audit / memory / provenance / capabilities).
  5. GOVERN group title (security / reality / orchestrator / operator).
  6. #statusbar (daemon health + agents + chain head + last activity).
  7. .tab[data-tab=forge] (CTA to click Forge or take per-tab tour).

Auto-launch changed from 'forge' to 'welcome' on first visit.
Forge / agents / audit tours stay registered + relaunchable via
the ? tour button.

Verified end-to-end via Chrome MCP against the live daemon:
  * Cleared fsf:toursSeen via JS; reloaded.
  * Welcome tour fired automatically after 1.5s.
  * Step 1/7 centered tooltip rendered.
  * Step 2/7 spotlight cut around BUILD group title.
  * skip / back / next buttons functional.

Phase II of the external-facing arc closed. Phase III (Homebrew
installer formula) is the next deliverable.

Note: this commit script (commit-burst449b-simple.command) is the
recovery path after the initial commit-burst449 script ran into
the §5 sandbox-index-lock race documented in CLAUDE.md. The
content + intent matches commit-burst449."
  RC=\$?
  echo "Commit exit: \$RC"

  if [ \$RC -eq 0 ]; then
    echo "Pushing..."
    git push origin main
    echo "Push exit: \$?"
  fi

  echo "Final HEAD: \$(git rev-parse --short HEAD)"
  echo "=== DONE ==="
} 2>&1 | tee "$OUT"

echo
echo "Output saved to $OUT"
echo "Press any key to close."
read -n 1 || true
