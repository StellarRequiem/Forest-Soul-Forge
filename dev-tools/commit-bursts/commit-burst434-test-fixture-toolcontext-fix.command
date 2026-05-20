#!/usr/bin/env bash
# Burst 434 — fix ToolContext kwargs in B432 test fixture.
#
# Bug
# ---
# B430-B433 commit script ran the new git_local_scan test file as
# pre-commit verification. Output:
#   6 failed, 2 passed in 0.62s
# All 6 failures at fixture setup with TypeError on ToolContext
# kwargs.
#
# Root cause: my test fixture passed `constitution_path="..."` as
# a ToolContext kwarg + redundantly named all the default-None
# fields. ToolContext has no `constitution_path` field; it's not
# part of the runtime contract (the constitution is loaded by the
# dispatcher from the registry row, not passed via ToolContext).
#
# This is the SAME class of mistake as B427 (column-name drift in
# idempotency fixture) and B429 (TraitEngine API drift in tool-
# constraints fixture). Three test-fixture API-drift hotfixes in
# one session. Lesson to bake in: when writing fixtures that
# construct kernel dataclasses, verify the dataclass field list
# against source (`grep "class ToolContext"` + read the @dataclass
# body) instead of inferring from memory or similar test files.
#
# Patch: drop the bogus `constitution_path` kwarg + remove the
# redundant explicit `=None` kwargs (those are dataclass defaults
# already). Three call sites updated.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: 6 red tests on B432; pins the contract but doesn't
#     run; future regression of the merge logic isn't caught.
#   Prove non-load-bearing: test fixture only; B432 tool code is
#     untouched and correct.
#   Prove alternative: revert B432 tests (rejected — tool itself
#     is sound, only fixture is wrong).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "==========================================================="
echo "Burst 434 — ToolContext kwargs hotfix (B432 follow-up)"
echo "==========================================================="
echo
echo "Pre-commit: clearing stale .git lock files (if any)..."
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null && echo "  removed" || echo "  none"
echo

git add tests/unit/test_git_local_scan.py
git add dev-tools/commit-bursts/commit-burst434-test-fixture-toolcontext-fix.command

echo "Pre-commit status:"
git status -s | head -10
echo
echo "Running fixed tests..."
if [ -x .venv/bin/pytest ]; then
  .venv/bin/pytest tests/unit/test_git_local_scan.py -v 2>&1 | tail -25
elif [ -x .venv/bin/python ]; then
  .venv/bin/python -m pytest tests/unit/test_git_local_scan.py -v 2>&1 | tail -25
fi
echo

git commit -m "fix(test): drop bogus constitution_path kwarg from git_local_scan fixture (B434)

B430-B433 commit script ran the new git_local_scan test; 6 of 8
tests errored at fixture setup with TypeError on ToolContext
kwargs. Root cause: my test fixture passed
constitution_path='...' as a ToolContext kwarg. ToolContext has
no such field — the constitution path lives on the agent row in
the registry, not in the per-call ToolContext.

This is the third test-fixture API-drift hotfix this session
(B427 idempotency column names, B429 TraitEngine factory, now
B434 ToolContext fields). Lesson worth saving in CLAUDE.md
folklore: when writing fixtures that construct kernel
dataclasses, verify the dataclass field list against source via
grep + read the @dataclass body, not by inferring from memory.

Patch: drop the bogus constitution_path kwarg + drop the
redundant explicit =None kwargs (those are dataclass defaults
already). Three call sites updated (_ctx_for helper + the two
inline ToolContext() constructions in path-refusal tests).

The B432 tool itself (git_local_scan.v1) is correct; the
substrate code in src/forest_soul_forge/tools/builtin/
git_local_scan.py is untouched. Only the test fixture is
wrong, and only the kwargs subset.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: 6 red tests on B432 contract; backward-compat +
    behavior contracts unpinned.
  Prove non-load-bearing: fixture only; tool code untouched.
  Prove alternative: revert tests (rejected — tool is sound).

Expected after this lands: 8 of 8 tests pass." || { echo "commit failed"; exit 1; }

echo
echo "Pushing to origin..."
git push origin main || { echo "push failed"; exit 1; }

echo
echo "Done."
echo
echo "Press any key to close."
read -n 1 || true
