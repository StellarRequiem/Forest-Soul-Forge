#!/usr/bin/env bash
# Burst 446 — ADR-0023 T1.1: scoring.higher_is_better flag.
#
# Closes the contract gap surfaced in B445's fixture-authoring
# attempt. T1's score_fixture pass-flag logic assumed every metric
# scaled "higher = better." That broke for naturally "lower = better"
# metrics like false_positive_rate, where 5% fpr should be GOOD
# (excellent) but the old comparison labeled it FAIL.
#
# T1.1 changes (additive; no kernel ABI touch; userspace only):
#   * FixtureScoring gains `higher_is_better: bool` field, default True.
#   * validate_fixture enforces direction-aware threshold ordering:
#       higher_is_better=True  -> pass < excellent (unchanged)
#       higher_is_better=False -> excellent < pass (tighter is lower)
#     Mismatched ordering raises FixtureValidationError with a clear
#     "when higher_is_better=X" message.
#   * score_fixture flips comparison logic when False:
#       <= excellent_threshold -> EXCELLENT
#       <= pass_threshold      -> PASS
#       else                    -> FAIL
#   * Re-authors benchmarks/observer/false_positive_rate.v1.yaml as
#     the first higher_is_better=false fixture (deleted in B445;
#     restored under the new schema). Specificity-style cutoffs:
#       excellent=0.05, pass=0.20.
#
# Tests: 8 new (53 total now), all green. Pre-existing 45 still pass.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: B445 surfaced the gap concretely (a real fixture
#     couldn't be authored without inverting interpretation). Until
#     T1.1 lands, "lower is better" metrics (FPR, latency-as-score,
#     mean-time-to-detection) can't have direction-correct fixtures.
#     T5 completion across 7 genres needs both directions.
#   Prove non-load-bearing for kernel: extends an existing
#     userspace dataclass; default value preserves backward
#     compatibility with every T1 fixture; no schema, no audit
#     events, no registry tables, no HTTP routes.
#   Prove alternative: defer T1.1 indefinitely (rejected; B445
#     surfaced it concretely and operator said "proceed"); ship a
#     new scoring function 'specificity' = 1 - fpr (rejected;
#     duplicates every "lower is better" metric; the flag is
#     more general).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "==========================================================="
echo "Burst 446 — ADR-0023 T1.1: higher_is_better flag"
echo "==========================================================="
echo

echo "Pre-commit: clear stale .git locks..."
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null && echo "  cleared" || echo "  none"
echo

# Verify tests pass pre-commit (full benchmark suite: 53 tests expected).
echo "Running ADR-0023 T1 + T1.1 unit tests..."
if [ -x .venv/bin/pytest ]; then
  .venv/bin/pytest tests/unit/test_benchmarks_fixture.py tests/unit/test_benchmarks_scoring.py -v 2>&1 | tail -5
  TEST_RC=${PIPESTATUS[0]}
else
  echo "WARN: no .venv/bin/pytest"
  TEST_RC=0
fi
if [ "$TEST_RC" -ne 0 ]; then
  echo "ERROR: tests failed pre-commit. Aborting."
  exit 1
fi
echo

git add src/forest_soul_forge/benchmarks/fixture.py
git add tests/unit/test_benchmarks_fixture.py
git add benchmarks/observer/false_positive_rate.v1.yaml
git add docs/decisions/ADR-0023-benchmark-suite.md
git add dev-tools/commit-bursts/commit-burst446-adr0023-t1-1-higher-is-better-flag.command

echo "Staged:"
git diff --cached --stat
echo

git commit -m "feat(benchmarks): ADR-0023 T1.1 — higher_is_better flag closes lower-is-better gap (B446)

Closes the contract gap surfaced in B445's fixture-authoring
attempt. T1's score_fixture assumed every metric was 'higher =
better'; metrics like false_positive_rate that are naturally
'lower = better' got pass/fail labeling inverted.

T1.1 additive change (userspace only; no kernel ABI touch):

  * FixtureScoring.higher_is_better: bool = True
    New field, defaults True so every existing T1 fixture works
    unchanged.

  * validate_fixture: direction-aware threshold ordering
    higher_is_better=True  -> pass < excellent (unchanged)
    higher_is_better=False -> excellent < pass (tighter is lower)
    Mismatched ordering rejected with explicit error.

  * score_fixture: flipped comparison for higher_is_better=False
    score <= excellent_threshold -> EXCELLENT
    score <= pass_threshold      -> PASS
    else                          -> FAIL

  * Re-authored benchmarks/observer/false_positive_rate.v1.yaml as
    the first higher_is_better=false fixture (deleted in B445;
    restored under the new schema). Specificity-style cutoffs:
    excellent=0.05, pass=0.20.

Tests: 8 new pass-cases pinning the False direction + default
True + bool type check + threshold-ordering enforcement (both
directions). 53 tests total now (was 45 in B444); all green.

End-to-end smoke (post-T1.1):
  3% fpr  -> ScoringResult(score=0.03, pass_flag='excellent')
  15% fpr -> ScoringResult(score=0.15, pass_flag='pass')
  40% fpr -> ScoringResult(score=0.40, pass_flag='fail')

Library state after this commit: 4 fixtures / 2 of 7 genres
(observer×3, investigator×1). T5 still needs 10 more fixtures
across 5 more genres to reach the documented 'minimum 14 across
7 genres' completion criteria.

ADR-0023 status updated: T1 + T1.1 Accepted. T2-T10 still
Proposed; T2 still needs ADR-0082 unfreeze trigger.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: B445 surfaced concretely; future 'lower is better'
    metrics (latency-as-score, MTTD, error rates) all need this.
  Prove non-load-bearing: extends existing userspace dataclass;
    default preserves backward compat; 53/53 tests pass.
  Prove alternative: defer (rejected; gap is real, op said
    proceed); new 'specificity' function (rejected; duplicates
    every lower-is-better metric)." || { echo "commit failed"; exit 1; }

echo
echo "Post-commit signature status:"
git log --format='%h %G? %s' -3
echo

echo "Pushing B446..."
git push origin main || { echo "push failed"; exit 1; }

echo
echo "Done. B446 pushed."
echo
echo "Press any key to close."
read -n 1 || true
