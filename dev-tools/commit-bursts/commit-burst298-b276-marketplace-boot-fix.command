#!/bin/bash
# Burst 298 - B276 marketplace tab boot-race fix.
#
# Same shape as B260.1 (Security pane) and B260.2 (Skills/Tools
# panels). Three frontend panels had start() calls only in the
# trait-tree-failure catch branch of app.js#run() — on the common
# success path their tab handlers never wired up, so clicking any
# of the affected tabs left them stuck on the placeholder
# "loading…" markup their HTML ships with.
#
# Affected panels:
#   - Forged Proposals (B205 / ADR-0030 + ADR-0031)
#   - Catalog Grants   (B223 / ADR-0060 T6)
#   - Marketplace      (B228 / ADR-0055 M4) — the one flagged
#                       during the B248-B259 frontend audit.
#
# Fix:
#   frontend/js/app.js — add the three .start() calls at the end
#   of the success-path branch, with a comment block linking back
#   to B260.1/B260.2 so future readers see the recurring pattern.
#   The trait-tree-failure catch branch keeps its own calls as
#   the degraded-mode fallback path.
#
# Verification: node --check frontend/js/app.js passes. Visual
# verification: load the daemon, click Marketplace tab; it should
# fetch the index instead of staying stuck. Same for the other
# two tabs.
#
# No test file — frontend.js doesn't have a JS test harness yet.
# The visual smoke check is the gate.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add frontend/js/app.js \
        dev-tools/commit-bursts/commit-burst298-b276-marketplace-boot-fix.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(frontend): wire forged/grants/marketplace panels in success branch (B298 / B276)

Burst 298. Closes B276. Same boot-asymmetry as B260.1 (Security)
and B260.2 (Skills/Tools): three frontend panels had start()
calls only in the trait-tree-failure catch branch of
app.js#run(). On the common success path their tab handlers
never wired up — Marketplace, Forged Proposals, and Catalog
Grants all sat stuck on their placeholder 'loading…' markup
because the modules never ran start(), so no tab-click handler
and no refresh-button handler ever attached.

Fix: add the three .start() calls at the end of the success-path
branch with a comment block linking back to B260.1/B260.2 so
future readers see the recurring pattern. The failure catch
branch keeps its calls as the degraded-mode fallback.

Verified: node --check frontend/js/app.js passes."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 298 complete - B276 marketplace boot-race closed ==="
echo "Verify on host: load daemon, click Marketplace tab, confirm fetch."
echo ""
echo "Press any key to close."
read -n 1
