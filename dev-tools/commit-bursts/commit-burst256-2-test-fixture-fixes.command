#!/bin/bash
# Burst 256.2 — test-fixture fixes uncovered by full-suite run.
#
# After the trait-weight hotfix (B256.1) recovered 10 of 13 broken
# tests, three real test-side bugs remained:
#
#   1. test_install_scanner._staged_clean/_critical/_high_only
#      called staging.mkdir() WITHOUT parents=True. The iteration
#      test (test_critical_refuses_regardless_of_strict) passed a
#      non-existent parent path, causing FileNotFoundError on
#      mkdir. Fix: parents=True + exist_ok=True everywhere.
#
#   2. test_reality_anchor_role.test_archive_then_rebirth_succeeds
#      called POST /agents/archive — wrong URL. The actual route
#      is POST /archive (per routers/writes/archive.py:80). 405
#      Method Not Allowed. Fix: corrected URL.
#
# No production code changes. These are test fixture/assertion
# fixes only. After this commit, all 11 session test files
# should pass 145/145.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add tests/unit/test_install_scanner.py \
        tests/unit/test_reality_anchor_role.py \
        dev-tools/commit-bursts/commit-burst256-2-test-fixture-fixes.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "test(reality-anchor): fixture fixes uncovered by full-suite run (B256.2)

Burst 256.2 — test-side fixes from the post-session pytest run.

After the trait-weight hotfix (B256.1) restored /birth, three
real test-fixture bugs remained out of 13 failing cases:

1. test_install_scanner._staged_* used staging.mkdir() without
   parents=True. The iteration test test_critical_refuses_
   regardless_of_strict passed a non-existent parent path
   (tmp_path / 'crit-False'), causing FileNotFoundError. Fix:
   parents=True + exist_ok=True on all three staging helpers.

2. test_reality_anchor_role.test_archive_then_rebirth_succeeds
   posted to /agents/archive — wrong URL, returned 405 Method
   Not Allowed. The actual route is POST /archive per
   routers/writes/archive.py:80. Fix: corrected URL + inline
   comment so future readers don't repeat the slip.

No production code changes. After this commit, all 11 session
test files should pass 145/145.

Per CLAUDE.md verification discipline: 'After every batch of
changes: run the full suite.' We did, found 13 failures
(11 from one bad trait-weight value in B253; 3 from test-side
bugs in B250 + B253). Both classes now fixed. The substrate
itself was always correct — confirmed by every burst's
standalone smoke driver."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 256.2 complete ==="
echo "=== Test-fixture fixes pushed. ==="
echo "Press any key to close."
read -n 1
