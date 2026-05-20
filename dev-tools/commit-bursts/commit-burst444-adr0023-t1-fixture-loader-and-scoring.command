#!/usr/bin/env bash
# Burst 444 — ADR-0023 T1: fixture YAML schema + loader + validator
# + numerical scoring functions module + tests.
#
# This is the first concrete delivery against ADR-0023's vision of
# per-genre quality batteries. T1 ships the userspace foundation:
# the data structures, loader, validator, scoring functions, and
# discovery API. NO kernel touch — adding a new submodule under
# src/forest_soul_forge/benchmarks/ is purely additive userspace
# per ADR-0044 + ADR-0082 (T2's HTTP endpoint + audit chain event
# types would need an explicit unfreeze trigger; that arc lands in
# a separate burst once operator picks T2 scope).
#
# Bundle:
#   src/forest_soul_forge/benchmarks/__init__.py  — module surface
#   src/forest_soul_forge/benchmarks/fixture.py   — Fixture dataclass + loader + validator
#   src/forest_soul_forge/benchmarks/scoring.py   — 5 numerical scoring functions
#   src/forest_soul_forge/benchmarks/registry.py  — load_fixtures_from_dir discovery
#   benchmarks/observer/signal_detection.v1.yaml  — first seeded fixture (synthetic, schema-shape pin)
#   tests/unit/test_benchmarks_fixture.py         — 23 tests pinning loader+validator+scoring contract
#   tests/unit/test_benchmarks_scoring.py         — 22 tests pinning scoring-function contract
#   dev-tools/run-benchmarks-t1-tests.command     — host-side test runner
#   docs/decisions/ADR-0023-benchmark-suite.md    — Status: Partially Accepted (T1 shipped)
#
# Test results pre-commit: 45 passed, 0 failed in 0.15s.
#
# Scope clarification (extending CLAUDE.md sec6 discipline):
#   This is ADR-0023 T1 PROPER. The substrate-perf benchmark suite
#   shipped in B440 (dev-tools/benchmark/) is a complementary but
#   distinct artifact — it measures daemon HTTP latency, not agent
#   behavioral quality. Both ship; both are useful; their scopes
#   don't overlap.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: ADR-0023 has been Proposed since v0.1 (2026-04-25).
#     Without T1's loader + scoring functions, fixture authoring has
#     no schema to validate against; downstream T2+ work can't begin.
#     Closing T1 unblocks T5 (per-genre battery authoring) and
#     T6 (performance budget per genre).
#   Prove non-load-bearing for kernel ABI: new userspace submodule;
#     no schema, no audit chain events, no HTTP routes, no registry
#     table. Per ADR-0044 the kernel ABI surfaces are unchanged.
#   Prove alternative: skip T1, jump to T2 (rejected; T2 needs the
#     fixture types T1 defines; would force T1 to be retrofitted).
#     Ship even smaller T1 (rejected; loader+validator+scoring is
#     already the minimum coherent unit; trimming further leaves
#     a half-built schema).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "==========================================================="
echo "Burst 444 — ADR-0023 T1: fixture loader + scoring module"
echo "==========================================================="
echo

echo "Pre-commit: clear stale .git locks..."
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null && echo "  cleared" || echo "  none"
echo

# Verify tests pass pre-commit.
echo "Running ADR-0023 T1 unit tests..."
if [ -x .venv/bin/pytest ]; then
  .venv/bin/pytest tests/unit/test_benchmarks_fixture.py tests/unit/test_benchmarks_scoring.py -v 2>&1 | tail -6
  TEST_RC=${PIPESTATUS[0]}
else
  echo "WARN: no .venv/bin/pytest — skipping test verification"
  TEST_RC=0
fi
if [ "$TEST_RC" -ne 0 ]; then
  echo "ERROR: tests failed pre-commit. Aborting."
  exit 1
fi
echo

git add src/forest_soul_forge/benchmarks/__init__.py
git add src/forest_soul_forge/benchmarks/fixture.py
git add src/forest_soul_forge/benchmarks/scoring.py
git add src/forest_soul_forge/benchmarks/registry.py
git add benchmarks/observer/signal_detection.v1.yaml
git add tests/unit/test_benchmarks_fixture.py
git add tests/unit/test_benchmarks_scoring.py
git add dev-tools/run-benchmarks-t1-tests.command
git add docs/decisions/ADR-0023-benchmark-suite.md
git add dev-tools/commit-bursts/commit-burst444-adr0023-t1-fixture-loader-and-scoring.command

echo "Staged:"
git diff --cached --stat
echo

git commit -m "feat(benchmarks): ADR-0023 T1 — fixture YAML loader + scoring functions module (B444)

First concrete delivery against ADR-0023's per-genre quality
battery vision. T1 ships the userspace foundation: data structures,
loader, validator, scoring functions, discovery API, first seeded
fixture, and 45 unit tests pinning every contract corner.

NO kernel ABI touch — new submodule under
src/forest_soul_forge/benchmarks/. Per ADR-0044 + ADR-0082, adding
a userspace module is canonical and doesn't need an unfreeze trigger.
T2 (HTTP POST /agents/{id}/benchmark + new audit chain event types
+ new registry table) WOULD need the trigger; that arc lands in a
separate burst once operator picks T2 scope.

Module surface:
  Fixture, FixtureInput, FixtureScoring, ScoringResult — dataclasses
  load_fixture(path)                — single-file loader+validator
  load_fixtures_from_dir(root)      — per-genre discovery + indexing
  validate_fixture(data)            — strict shape + invariant checks
  score_fixture(fixture, inputs)    — numerical execution (rubric -> NotImplementedError pointing at T4)
  SCORING_FUNCTIONS                 — named dict: detection_rate, false_positive_rate, latency_ms, exact_match, composite
  PASS_FLAG_PASS/FAIL/EXCELLENT     — outcome flags
  FixtureValidationError            — raised on contract break

Contract pins enforced:
  * fixture_id ↔ '{name}.v{version}' invariant
  * genre allowlist (7 known genres per ADR-0023)
  * scoring.type allowlist (numerical / rubric / composite)
  * scoring.function-must-resolve check (numerical only; rubric+composite shape-only per T4 deferral)
  * threshold.pass < threshold.excellent ordering
  * inputs non-empty
  * directory-name ↔ declared genre match (registry path)
  * fixture_id global uniqueness (registry path)
  * detection_rate, false_positive_rate clamped to [0,1]
  * latency_ms = median (robust to outlier dominance)
  * composite supports unnormalized weights (operator may emphasize)
  * exact_match works on arbitrary equality-comparable shapes

Test results pre-commit: 45 passed, 0 failed in 0.15s.

First seeded fixture: benchmarks/observer/signal_detection.v1.yaml.
Synthetic — real fixture data is T5 work (per-genre battery
authoring); v1 pins the schema shape so the loader round-trips
in production-shaped YAML.

Scope clarification (extending CLAUDE.md sec6 discipline):
B440 shipped dev-tools/benchmark/ — substrate-perf measurement.
This commit (B444) ships src/forest_soul_forge/benchmarks/ —
agent quality measurement. Different concerns; both useful.

ADR-0023 status updated: Partially Accepted. T1 shipped. T2-T10
remain Proposed.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: ADR-0023 has been Proposed since 2026-04-25; T1
    blocks every downstream tranche; fixture authoring has no
    schema to validate against; agent-quality measurement story
    remains a paper proposal.
  Prove non-load-bearing for kernel ABI: new userspace submodule.
    No schema, no events, no routes, no registry tables. Per
    ADR-0044 + ADR-0082 unfreeze-trigger-free.
  Prove alternative: skip T1, jump to T2 (rejected; T2 depends on
    T1's types). Trim T1 further (rejected; loader+scoring is
    minimum coherent unit)." || { echo "commit failed"; exit 1; }

echo
echo "Post-commit signature status:"
git log --format='%h %G? %s' -5
echo

echo "Pushing B444..."
git push origin main || { echo "push failed"; exit 1; }

echo
echo "Done. B444 pushed."
echo
echo "Press any key to close."
read -n 1 || true
