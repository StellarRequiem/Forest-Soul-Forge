#!/usr/bin/env bash
# Burst 429 — fix TraitEngine API drift in B428's test fixture.
#
# Bug
# ---
# B428's commit script ran the new test file as part of pre-commit
# verification. Output:
#
#   5 errors in 0.08s
#   ERROR tests/unit/test_constitution_tool_constraints.py::*
#   tests/unit/test_constitution_tool_constraints.py:34: AttributeError
#   tribute 'from_yaml'
#
# Root causes (two API drifts):
#   1. Fixture used TraitEngine.from_yaml(Path(...)) — that method
#      doesn't exist. Correct factory is the constructor itself:
#      TraitEngine(tree_path=Path(...)).
#   2. Tests used profile.profile_for(role, trait_values={}) — that
#      method doesn't exist. Correct is build_profile(role,
#      overrides=None).
#
# Same class of mistake as B427 (column-name drift in idempotency
# fixture): writing fixtures from memory instead of verifying against
# the source. Reinforces the [feedback_check_the_checkers] pattern —
# need to verify external dependencies even in fixtures.
#
# Hippocratic gate (CLAUDE.md sec0)
# ---------------------------------
# 1. Prove harm: 5 red tests on the B428 contract; backward-compat
#    isn't pinned; future regressions of the merge logic won't be
#    caught. Same harm class as B427.
# 2. Prove non-load-bearing: test fixture only; kernel code unchanged.
#    The B428 merge logic itself is sound — the tests just can't
#    reach it through the broken fixture.
# 3. Prove alternative: revert B428 test file (rejected — patch is
#    correct, just fixture is wrong). Skip the tests entirely
#    (rejected — masks future regressions of the merge logic).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "==========================================================="
echo "Burst 429 — TraitEngine API drift hotfix (B428 follow-up)"
echo "==========================================================="
echo
echo "Pre-commit: clearing stale .git lock files (if any)..."
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null && echo "  removed" || echo "  none"
echo

git add tests/unit/test_constitution_tool_constraints.py
git add dev-tools/commit-bursts/commit-burst429-test-fixture-trait-engine-api-fix.command

echo "Pre-commit status:"
git status -s | head -10
echo
echo "Running unit tests for the fixed fixture..."
if [ -x .venv/bin/pytest ]; then
  .venv/bin/pytest tests/unit/test_constitution_tool_constraints.py -v 2>&1 | tail -20
elif [ -x .venv/bin/python ]; then
  .venv/bin/python -m pytest tests/unit/test_constitution_tool_constraints.py -v 2>&1 | tail -20
else
  echo "  venv pytest not found"
fi
echo

git commit -m "fix(test): TraitEngine API drift in B428 fixture (B429)

B428 commit script ran the new test file; 5 of 5 tests errored at
fixture setup with AttributeError on TraitEngine.from_yaml.

Root causes (two API drifts):
  1. TraitEngine.from_yaml(Path) — method doesn't exist. Correct:
     TraitEngine(tree_path=Path(...)).
  2. engine.profile_for(role, trait_values={}) — method doesn't
     exist. Correct: engine.build_profile(role, overrides=None).

Same class of mistake as B427 (column-name drift in idempotency
fixture). Reinforces the lesson: when writing fixtures, verify
external API surfaces against source (or an existing passing test)
instead of writing from memory.

Patch: corrected both API calls in the fixture + per-test profile
creation. Inline comment notes the B428 -> B429 drift trail.

The B428 merge logic itself is sound — the layer-4 tool_constraints
merge in constitution.build() is correct, and once the fixture works
the 5 tests should all pass.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: 5 red tests masking future regressions of the layer-4
    merge logic.
  Prove non-load-bearing: test fixture only.
  Prove alternative: revert B428 tests (rejected — patch is good).

Next: restart daemon to load the B428 merge code (still pending),
then archive sibling-2 Reviewer-Main + rebirth as sibling 3 to
verify allowed_paths actually land in the new constitution." || { echo "commit failed"; exit 1; }

echo
echo "Pushing to origin..."
git push origin main || { echo "push failed"; exit 1; }

echo
echo "Done."
echo
echo "Press any key to close."
read -n 1 || true
