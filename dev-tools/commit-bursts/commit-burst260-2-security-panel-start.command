#!/bin/bash
# Burst 260.2 — second of two findings from the live smoke test.
#
# B260.1 fixed the Reality + Security backend handlers (async +
# chain.tail). After daemon restart, Reality pane populated
# correctly but Security pane STILL stuck at "loading…". Real-time
# debug found the cause: ``securityPanel.start()`` is missing from
# app.js's success branch — only present in the trait-tree-failure
# catch branch. When the trait tree loads successfully (the common
# case), security.js's start() never runs, so no tab-click or
# refresh-button handler is ever attached. Reality Anchor doesn't
# have this bug because realityAnchorPanel.start() IS in the
# success branch (line 139).
#
# Fix: add securityPanel.start() to the success branch directly
# after realityAnchorPanel.start(). Mirrors the established
# pattern for lazy-loaded pane modules.
#
# Verified post-fix: Security tab populates with 16 IoC rules +
# 0 24h refuse/allow/critical counts + IoC catalog table rendering
# correctly with CRITICAL/HIGH severity chips. Reality tab unchanged.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add frontend/js/app.js \
        dev-tools/commit-bursts/commit-burst260-2-security-panel-start.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(frontend): wire securityPanel.start() in success branch (B260.2)

Burst 260.2. Second of two findings from the live smoke test:
the Security tab stayed stuck at 'loading…' even after the
B260.1 async-handler fix and the daemon restart. Real-time
debug surfaced the cause: securityPanel.start() was only
called in app.js's trait-tree-failure catch branch (line 85),
never in the success branch where the rest of the panel
.start() calls live. When the trait tree loads successfully
(the common path), security.js never wires its tab-click or
refresh-button handler — clicking the tab leaves the pane's
hardcoded 'loading…' HTML untouched.

Reality Anchor doesn't have this bug because
realityAnchorPanel.start() IS in the success branch.

Fix: add securityPanel.start() after realityAnchorPanel.start()
in the success branch. Mirrors the established pattern.

Post-fix live smoke confirms Security pane populates with
16 IoC rules + the catalog table (CRITICAL eval_atob_obfuscation,
home_dir_wipe_python, etc.). Reality pane unchanged."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 260.2 complete — Security pane lazy-load wired in success branch ==="
echo "Press any key to close."
read -n 1
