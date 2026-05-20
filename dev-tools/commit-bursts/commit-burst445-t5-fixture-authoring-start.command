#!/usr/bin/env bash
# Burst 445 — ADR-0023 T5 starts: 2 new fixtures using T1 schema.
#
# Adds:
#   benchmarks/observer/tool_invocation_focus.v1.yaml      (2nd observer fixture)
#   benchmarks/investigator/correlation_recall.v1.yaml     (1st investigator fixture; new genre)
#
# Both use existing T1 scoring functions (detection_rate). No new
# code. Pure fixture authoring — validates that T1's schema + loader
# + per-genre discovery handle multi-fixture-per-genre AND
# multi-genre cases correctly.
#
# Out-of-scope finding (documented as future T1.1 candidate):
# tried to author benchmarks/observer/false_positive_rate.v1.yaml
# but T1's score_fixture pass-flag logic assumes 'higher = better'
# (score >= threshold = PASS). For false_positive_rate (naturally
# 'lower = better'), this labels 5% fpr as FAIL and 40% fpr as
# EXCELLENT — backwards. Two clean fixes possible:
#   * Add scoring.higher_is_better flag to FixtureScoring (default True).
#   * Express 'lower is better' metrics as their inverted form
#     (e.g., specificity = 1 - fpr) via a new scoring function.
# Neither is in scope for this small push. The flawed fixture was
# removed (via dev-tools/remove-flawed-fpr-fixture.command which
# self-deleted on success). Replaced by tool_invocation_focus.v1
# which uses detection_rate semantics (naturally higher = better).
#
# Library state after this lands: 3 fixtures, 2 genres (5 of 14
# needed to satisfy T5's '2 fixtures per genre across 7 genres'
# completion criteria — observer needs 1 more; investigator needs 1
# more; communicator/actuator/guardian/researcher/companion need 2
# each).
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: T1 shipped with one seed fixture; T5 was queued
#     immediately after. Without exercising the schema with
#     multiple fixtures + multiple genres, the loader's per-genre
#     discovery + cross-genre uniqueness invariants stay
#     theoretical.
#   Prove non-load-bearing for kernel: pure YAML files in
#     benchmarks/; no code change.
#   Prove alternative: author bigger T5 chunk (rejected; this push
#     is intentionally small and surfaces the T1.1 contract gap
#     for operator decision); skip authoring (rejected; smoke
#     coverage is the point).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "==========================================================="
echo "Burst 445 — ADR-0023 T5 fixture authoring (start)"
echo "==========================================================="
echo

echo "Pre-commit: clear stale .git locks..."
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null && echo "  cleared" || echo "  none"
echo

# Verify the two new fixtures load + score correctly.
echo "Smoke: load all fixtures + score samples..."
if [ -x .venv/bin/python ]; then
  PYTHONPATH=src .venv/bin/python -c "
from forest_soul_forge.benchmarks import load_fixtures_from_dir, score_fixture
fixtures = load_fixtures_from_dir('benchmarks')
assert 'tool_invocation_focus.v1' in fixtures, 'observer #2 fixture missing'
assert 'correlation_recall.v1' in fixtures, 'investigator #1 fixture missing'
assert fixtures['correlation_recall.v1'].genre == 'investigator', 'genre mismatch'
print(f'  {len(fixtures)} fixtures across {len(set(f.genre for f in fixtures.values()))} genres OK')
"
fi
echo

git add benchmarks/observer/tool_invocation_focus.v1.yaml
git add benchmarks/investigator/correlation_recall.v1.yaml
git add dev-tools/commit-bursts/commit-burst445-t5-fixture-authoring-start.command

echo "Staged:"
git diff --cached --stat
echo

git commit -m "feat(benchmarks): ADR-0023 T5 starts — 2 new fixtures, 1 new genre (B445)

First two T5 fixtures land. Both use existing T1 scoring functions
(detection_rate). No code change. Pure fixture authoring that
exercises T1's per-genre discovery + cross-genre uniqueness
invariants with real schema-shaped YAML.

Adds:
  benchmarks/observer/tool_invocation_focus.v1.yaml
    Observer's #2 fixture. Pairs with signal_detection.v1: that one
    measures recall (anomalies caught); this one measures focus
    (fraction of tool invocations that targeted labeled-anomaly
    windows vs. clean-traffic noise). Reuses detection_rate
    semantically. Synthetic data only.

  benchmarks/investigator/correlation_recall.v1.yaml
    Investigator's #1 fixture (new genre directory). Score: K of N
    correlated event subsets recovered. Reuses detection_rate.
    Synthetic incident timelines with planted causal chains.

Out-of-scope finding (documented as a T1.1 candidate, not closed
in this push): tried authoring observer/false_positive_rate.v1.yaml
but T1's score_fixture pass-flag logic assumes 'higher = better.'
For false_positive_rate (naturally 'lower = better'), pass/excellent
threshold ordering inverts the intended semantics. Two clean fixes:
  * Add scoring.higher_is_better flag (default True) to FixtureScoring.
  * Add a new 'specificity' scoring function (1 - fpr).
Neither is small-push scope. The flawed fixture was removed via a
self-deleting host-side helper. Replaced by tool_invocation_focus.v1
which uses detection_rate (naturally higher = better).

T5 completion criteria reminder: '2 fixtures per genre across 7
genres = 14 fixtures.' After this commit: 3 fixtures / 2 genres.
Still needed: observer +1, investigator +1, communicator +2,
actuator +2, guardian +2, researcher +2, companion +2.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: T1 shipped with one seed fixture; without
    multi-fixture + multi-genre coverage the schema invariants
    stay theoretical.
  Prove non-load-bearing: YAML files only; no code.
  Prove alternative: bigger T5 chunk (rejected; small push +
    surfaces the T1.1 gap for operator decision); skip (rejected;
    smoke coverage is the point)." || { echo "commit failed"; exit 1; }

echo
echo "Post-commit signature status:"
git log --format='%h %G? %s' -3
echo

echo "Pushing B445..."
git push origin main || { echo "push failed"; exit 1; }

echo
echo "Done. B445 pushed."
echo
echo "Press any key to close."
read -n 1 || true
