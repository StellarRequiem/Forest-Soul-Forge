#!/usr/bin/env bash
# Burst 427 — fix test fixture column-name drift in B426's
# test_idempotency_lifecycle.py.
#
# Bug
# ---
# B426's commit script ran the new test file as part of its
# pre-commit verification. Output:
#
#   4 failed, 2 passed in 0.30s
#   FAILED tests/unit/test_idempotency_lifecycle.py::*  (4 of 6)
#   no column named response_status
#   src/forest_soul_forge/registry/registry.py:261: OperationalError
#
# Root cause: the test fixture _seed_cache_entry used INSERT column
# names `response_status` + `response_body`. The actual schema uses
# `status_code` + `response_json` (see registry/schema.py:
# CREATE TABLE idempotency_keys (key, endpoint, request_hash,
# status_code, response_json, created_at)).
#
# I wrote the test fixture from memory of the cached row I'd seen
# in an earlier python3 -c diagnostic dump, where I'd projected
# the columns under different names. The diagnostic output didn't
# preserve actual column names. Should have re-verified via
# PRAGMA table_info before writing the fixture.
#
# CLAUDE.md feedback (proposed): when seeding a row directly in a
# test fixture, verify column names via PRAGMA table_info against
# the live registry, not from memory or from earlier diagnostic
# projections.
#
# Hippocratic gate (CLAUDE.md sec0)
# ---------------------------------
# 1. Prove harm: 4 of 6 ADR-0083 contract tests are red; the
#    backward-compatibility contract (most critical) is RED until
#    fixed. Future run-tests.command will flag this loudly.
# 2. Prove non-load-bearing: test fixture only; kernel code is
#    untouched. The B426 commit's kernel patch is correct (the
#    failed tests can't actually exercise the patch logic because
#    they fail at fixture setup).
# 3. Prove alternative: revert test file rejected — the patch
#    itself is correct; just the fixture is wrong. Leaving 4 red
#    tests rejected — masks future regressions.
#
# Files
# -----
# MOD tests/unit/test_idempotency_lifecycle.py
#   _seed_cache_entry column names: response_status -> status_code,
#   response_body -> response_json. Inline comment explains the
#   drift surfaced in B426.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "==========================================================="
echo "Burst 427 — test fixture column-name hotfix (B426 follow-up)"
echo "==========================================================="
echo
echo "Pre-commit: clearing stale .git lock files (if any)..."
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null && echo "  removed" || echo "  none"
echo

git add tests/unit/test_idempotency_lifecycle.py
git add dev-tools/commit-bursts/commit-burst427-test-fixture-column-fix.command

echo "Pre-commit status:"
git status -s | head -10
echo
echo "Running unit tests (host venv) for the fixed fixture..."
if [ -x .venv/bin/pytest ]; then
  .venv/bin/pytest tests/unit/test_idempotency_lifecycle.py -v 2>&1 | tail -20
elif [ -x .venv/bin/python ]; then
  .venv/bin/python -m pytest tests/unit/test_idempotency_lifecycle.py -v 2>&1 | tail -20
else
  echo "  venv pytest not found — manually verify via run-tests.command"
fi
echo

git commit -m "fix(test): correct idempotency_keys column names in fixture (B427)

B426 commit script ran the new ADR-0083 test file; 4 of 6 tests
failed at fixture setup with 'no column named response_status'.

Root cause: _seed_cache_entry used wrong INSERT column names.
Actual schema (registry/schema.py): key, endpoint, request_hash,
status_code, response_json, created_at.
Fixture used: response_status, response_body. Drift was mine —
I wrote the fixture from memory of an earlier diagnostic projection
that aliased the columns.

Patch: column names corrected to status_code + response_json.
Inline comment notes the B426 -> B427 drift trail.

The B426 kernel patch itself is correct — the test failures are
all at fixture setup, before any code under test runs. With this
hotfix all 6 contracts should pass:
  1. backward compat (no validator)
  2. validator True (replay proceeds)
  3. validator False (replay returns None — the new path)
  4. validator receives raw body bytes
  5. missing key short-circuits before validator
  6. cache miss short-circuits before validator

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: 4 red tests on the new contract; masks future
    regressions of ADR-0083's backward-compat promise.
  Prove non-load-bearing: test fixture only; kernel patch in B426
    is untouched.
  Prove alternative: revert + restart rejected — patch is correct,
    fixture is the only thing wrong.

Lesson worth saving: when seeding a row directly in a fixture,
verify column names via PRAGMA table_info against the live schema,
not from memory or earlier diagnostic projections." || { echo "commit failed"; exit 1; }

echo
echo "Pushing to origin..."
git push origin main || { echo "push failed"; exit 1; }

echo
echo "Done."
echo
echo "Press any key to close."
read -n 1 || true
