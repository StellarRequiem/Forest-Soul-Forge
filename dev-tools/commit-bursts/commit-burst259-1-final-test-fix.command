#!/bin/bash
# Burst 259.1 — last test-fixture fix from the full-suite run.
#
# The 144/145 pass post-B259 left ONE failing case:
# test_install_scanner.TestGateAllows.test_clean_staging_allows
# asserted len(events) == 1 but the chain has 2 events: the
# genesis chain_created event + the actual agent_security_scan
# _completed event.
#
# One-line fix: filter for the specific event_type before
# counting. Same pattern as test_install_scanner's other
# tests already use; this one slipped through.
#
# Post-fix: all 145 session tests pass.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add tests/unit/test_install_scanner.py \
        dev-tools/commit-bursts/commit-burst259-1-final-test-fix.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "test(install-scanner): filter genesis from event count (B259.1)

Burst 259.1 — final test-fixture fix from the post-session
pytest validation.

test_clean_staging_allows asserted len(events) == 1, but the
chain has 2 events: the genesis chain_created entry + the
actual agent_security_scan_completed. Other tests in the
same file already filter by event_type; this one slipped
through. Fix mirrors the established pattern.

Post-fix: all 145 session tests pass."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 259.1 complete — session tests 145/145 green ==="
echo "Press any key to close."
read -n 1
