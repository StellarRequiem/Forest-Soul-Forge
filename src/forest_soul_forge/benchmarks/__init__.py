"""ADR-0023 — per-genre benchmark fixtures.

T1 surface: fixture YAML schema, loader, validator, numerical
scoring functions module. T2+ (HTTP endpoint, audit events,
registry table, rubric scoring, lifecycle endpoints) are NOT in
this module — they require the ADR-0082 unfreeze trigger and
will land in a separate burst arc.

Public API:
    Fixture                 — frozen dataclass for a single fixture
    FixtureInput            — single input artifact (typed)
    FixtureScoring          — scoring metadata (type, function, thresholds)
    ScoringResult           — (score, pass_flag) tuple after a function runs
    PASS_FLAG_*             — outcome flags ('pass', 'fail', 'excellent')
    load_fixture            — load a single fixture YAML file
    load_fixtures_from_dir  — discover + load all fixtures under benchmarks/
    validate_fixture        — strict validator (raises FixtureValidationError)
    FixtureValidationError  — raised when a fixture YAML breaks contract
    SCORING_FUNCTIONS       — name → callable map for numerical scoring
    score_fixture           — convenience: run scoring function + emit ScoringResult

The scope-clarification from CLAUDE.md sec6: this is ADR-0023 T1
proper, not the substrate-perf measurement tool in dev-tools/benchmark/
which is a complementary but distinct artifact.
"""
from forest_soul_forge.benchmarks.fixture import (
    Fixture,
    FixtureInput,
    FixtureScoring,
    FixtureValidationError,
    PASS_FLAG_EXCELLENT,
    PASS_FLAG_FAIL,
    PASS_FLAG_PASS,
    ScoringResult,
    load_fixture,
    score_fixture,
    validate_fixture,
)
from forest_soul_forge.benchmarks.registry import load_fixtures_from_dir
from forest_soul_forge.benchmarks.scoring import SCORING_FUNCTIONS

__all__ = [
    "Fixture",
    "FixtureInput",
    "FixtureScoring",
    "FixtureValidationError",
    "PASS_FLAG_EXCELLENT",
    "PASS_FLAG_FAIL",
    "PASS_FLAG_PASS",
    "ScoringResult",
    "SCORING_FUNCTIONS",
    "load_fixture",
    "load_fixtures_from_dir",
    "score_fixture",
    "validate_fixture",
]
