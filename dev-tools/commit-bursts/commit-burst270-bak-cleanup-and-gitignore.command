#!/bin/bash
# Burst 270 — sandbox-edit .bak cleanup + .gitignore patch.
#
# Small follow-up to B269. Two pieces:
#
# 1. .gitignore patch: add `*.bak` and `*.py.bak` to the editor-
#    artifacts section. The existing rule `*.bak.[0-9]*` only
#    catches timestamped backups (e.g. file.bak.20260513); the
#    plain `*.bak` form falls through. Bursts 261-269 surfaced
#    7 `.py.bak` files in the working tree from Edit-tool
#    operations the sandbox couldn't rm post-hoc.
#
# 2. New cleanup-bak-files.command at repo root: operator-runnable
#    cleanup script. find + rm any .bak files outside .venv / .git
#    / node_modules. Safe to re-run; no-op when the tree is
#    already clean. Excludes timestamped .bak.N* (those are
#    covered by the existing rule).
#
# Not in scope: the actual deletion of the 7 .bak files in the
# tree. Those need the operator to double-click
# cleanup-bak-files.command after this commit lands. We commit
# the SCRIPT first; the script's run is a separate operator
# action (untracked files don't count toward repo state).
#
# Why a separate burst: B269 was scoped to T4 memory body
# encryption (one coherent change). Folding gitignore/cleanup
# into B269 would have mixed concerns — the §0 Hippocratic gate
# discipline says one coherent change per commit.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add .gitignore \
        cleanup-bak-files.command \
        dev-tools/commit-bursts/commit-burst270-bak-cleanup-and-gitignore.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "chore: .gitignore *.bak + cleanup script for sandbox-edit artifacts (B270)

Burst 270. Follow-up to B269. Two pieces of housekeeping for
the sandbox-edit-artifact pattern that surfaced repeatedly
through the ADR-0051 + ADR-0050 security arc (Bursts 261-269).

.gitignore patch:

  - Existing rule \`*.bak.[0-9]*\` catches timestamped backups
    (e.g. file.bak.20260513) but not the plain \`*.bak\` form
    that Edit-tool operations produce.
  - This adds \`*.bak\` and \`*.py.bak\` to the editor-artifacts
    section with a comment pointing at the originating pattern.
  - Future Edit-heavy bursts won't surface .bak files as
    untracked noise in git status.

cleanup-bak-files.command at repo root:

  - Operator-runnable cleanup. find + rm any .bak files
    outside .venv / .git / node_modules.
  - Safe to re-run; no-op when tree is clean.
  - Why: the Cowork sandbox writes .bak snapshots during Edit
    operations, then can't rm them because host filesystem
    permissions block the delete (\"Operation not permitted\").
    The operator (host user) has to clear them. This script
    automates the clear.

Not in this commit: the actual deletion of the 7 .py.bak files
currently in the tree. Those are untracked, so don't affect
repo state. Operator double-clicks cleanup-bak-files.command
after this commit lands to clear them.

Why a separate burst: B269 was scoped to T4 memory body
encryption (one coherent change). §0 Hippocratic gate
discipline says one coherent change per commit; gitignore +
cleanup script is its own concern."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 270 complete — .bak cleanup substrate shipped ==="
echo ""
echo "Next: double-click cleanup-bak-files.command at repo root"
echo "      to delete the 7 .py.bak files currently in the tree."
echo ""
echo "Press any key to close."
read -n 1
