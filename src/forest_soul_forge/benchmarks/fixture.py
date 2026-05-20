"""Fixture YAML schema + loader + validator per ADR-0023 T1.

A fixture is one canonical test scenario for a single genre. Each
fixture lives at ``benchmarks/{genre}/{name}.v{N}.yaml`` and follows
the schema documented in ADR-0023 §"Scenario shape". Old fixtures
are NEVER edited in place — same versioning discipline as the tool
catalog (ADR-0018). v1 stays exact; v2 is a parallel file.

Why a frozen dataclass + named validator rather than pydantic:
forge-substrate code uses dataclasses + manual validation throughout
(see ToolContext, GenreSpec, etc.). New code matches the existing
style so cross-module reuse stays straightforward.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PASS_FLAG_PASS = "pass"
PASS_FLAG_FAIL = "fail"
PASS_FLAG_EXCELLENT = "excellent"

_PASS_FLAGS = (PASS_FLAG_PASS, PASS_FLAG_FAIL, PASS_FLAG_EXCELLENT)

# Genres recognized by ADR-0021 + ADR-0023. The fixture loader
# checks against this set; a fixture whose declared genre isn't here
# fails validation. New genres added to genres.yaml must also be
# added here OR the loader gets a configurable allowlist (T6+ work).
KNOWN_GENRES = frozenset({
    "observer",
    "investigator",
    "communicator",
    "actuator",
    "guardian",
    "researcher",
    "companion",
})

# Scoring types per ADR-0023. v1 ships numerical; rubric + composite
# are validated as schema-shape but their executors land in T4+.
KNOWN_SCORING_TYPES = frozenset({"numerical", "rubric", "composite"})

_FIXTURE_ID_RE = re.compile(r"^[a-z_][a-z0-9_]*\.v[0-9]+$")


class FixtureValidationError(ValueError):
    """Raised when a fixture YAML breaks the ADR-0023 schema.

    Carries the fixture identifier (or filename if id couldn't be
    extracted) plus the human-readable reason. Callers display this
    directly to the operator; format is stable.
    """


@dataclass(frozen=True)
class FixtureInput:
    """A single input artifact a fixture provides to the agent under
    test. Type values are open per ADR-0023 (the executor consumes
    them); the loader only enforces presence + non-empty source."""
    type: str
    source: str


@dataclass(frozen=True)
class FixtureScoring:
    """Scoring metadata: how to score the fixture outcome.

    `function` resolves against ``scoring.SCORING_FUNCTIONS`` at score
    time. For ``type="numerical"`` the function must exist; for
    ``rubric`` / ``composite`` types the loader accepts the field as
    a string (T4 will resolve rubric functions; T1 just validates
    shape).
    """
    type: str
    function: str
    threshold_pass: float
    threshold_excellent: float


@dataclass(frozen=True)
class Fixture:
    """One ADR-0023 fixture, fully parsed + validated."""
    fixture_id: str
    genre: str
    name: str
    version: str
    description: str
    inputs: tuple[FixtureInput, ...]
    scoring: FixtureScoring
    baseline: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoringResult:
    """The (numeric_score, pass_flag) tuple emitted after a scoring
    function runs. ``pass_flag`` is one of PASS_FLAG_* constants
    based on the fixture's thresholds."""
    score: float
    pass_flag: str


def _require(cond: bool, fixture_label: str, reason: str) -> None:
    if not cond:
        raise FixtureValidationError(f"{fixture_label}: {reason}")


def validate_fixture(data: dict[str, Any], *, fixture_label: str = "<unknown>") -> None:
    """Strict validator. Raises ``FixtureValidationError`` with a
    fixture-tagged message on the first contract break. Does NOT
    attempt to recover or coerce — the discipline of ADR-0018's
    catalog versioning carries here: if the YAML is malformed, fix
    the YAML, don't paper over it in the loader."""
    label = fixture_label
    _require(isinstance(data, dict), label, "top-level must be a mapping")

    for required in ("fixture_id", "genre", "name", "version", "description", "inputs", "scoring"):
        _require(required in data, label, f"missing required field {required!r}")

    fixture_id = data["fixture_id"]
    _require(
        isinstance(fixture_id, str) and _FIXTURE_ID_RE.match(fixture_id) is not None,
        label,
        f"fixture_id must match {_FIXTURE_ID_RE.pattern!r}, got {fixture_id!r}",
    )

    expected_id = f"{data.get('name')}.v{data.get('version')}"
    _require(
        fixture_id == expected_id,
        label,
        f"fixture_id {fixture_id!r} must equal '{{name}}.v{{version}}' = {expected_id!r}",
    )

    genre = data["genre"]
    _require(
        isinstance(genre, str) and genre in KNOWN_GENRES,
        label,
        f"genre {genre!r} not in KNOWN_GENRES {sorted(KNOWN_GENRES)}",
    )

    _require(
        isinstance(data["description"], str) and data["description"].strip(),
        label,
        "description must be non-empty",
    )

    inputs = data["inputs"]
    _require(
        isinstance(inputs, list) and len(inputs) > 0,
        label,
        "inputs must be a non-empty list",
    )
    for i, inp in enumerate(inputs):
        _require(isinstance(inp, dict), label, f"input[{i}] must be a mapping")
        _require("type" in inp and isinstance(inp["type"], str) and inp["type"], label, f"input[{i}] missing/empty 'type'")
        _require("source" in inp and isinstance(inp["source"], str) and inp["source"], label, f"input[{i}] missing/empty 'source'")

    scoring = data["scoring"]
    _require(isinstance(scoring, dict), label, "scoring must be a mapping")
    for key in ("type", "function", "threshold"):
        _require(key in scoring, label, f"scoring missing required key {key!r}")
    _require(
        scoring["type"] in KNOWN_SCORING_TYPES,
        label,
        f"scoring.type {scoring['type']!r} not in {sorted(KNOWN_SCORING_TYPES)}",
    )
    _require(isinstance(scoring["function"], str) and scoring["function"], label, "scoring.function must be non-empty string")
    threshold = scoring["threshold"]
    _require(isinstance(threshold, dict), label, "scoring.threshold must be a mapping")
    _require("pass" in threshold, label, "scoring.threshold missing 'pass'")
    _require("excellent" in threshold, label, "scoring.threshold missing 'excellent'")
    _require(
        isinstance(threshold["pass"], (int, float)),
        label,
        "scoring.threshold.pass must be numeric",
    )
    _require(
        isinstance(threshold["excellent"], (int, float)),
        label,
        "scoring.threshold.excellent must be numeric",
    )
    _require(
        threshold["pass"] < threshold["excellent"],
        label,
        f"scoring.threshold.pass ({threshold['pass']}) must be < threshold.excellent ({threshold['excellent']})",
    )

    # Numerical scoring functions must resolve against the catalog;
    # rubric/composite scoring is shape-validated only (T4+ resolves).
    if scoring["type"] == "numerical":
        # Import here to avoid a circular import at module load time;
        # scoring.py imports from fixture.py for the ScoringResult type.
        from forest_soul_forge.benchmarks.scoring import SCORING_FUNCTIONS

        _require(
            scoring["function"] in SCORING_FUNCTIONS,
            label,
            f"scoring.function {scoring['function']!r} not registered in SCORING_FUNCTIONS",
        )

    # baseline + provenance are optional but if present must be mappings.
    for opt in ("baseline", "provenance"):
        if opt in data:
            _require(isinstance(data[opt], dict), label, f"{opt} must be a mapping if present")


def _build_fixture(data: dict[str, Any]) -> Fixture:
    """Construct the Fixture dataclass from validated YAML data.
    Assumes ``validate_fixture`` has already run on this payload."""
    inputs = tuple(
        FixtureInput(type=i["type"], source=i["source"]) for i in data["inputs"]
    )
    scoring_data = data["scoring"]
    scoring = FixtureScoring(
        type=scoring_data["type"],
        function=scoring_data["function"],
        threshold_pass=float(scoring_data["threshold"]["pass"]),
        threshold_excellent=float(scoring_data["threshold"]["excellent"]),
    )
    return Fixture(
        fixture_id=data["fixture_id"],
        genre=data["genre"],
        name=data["name"],
        version=str(data["version"]),
        description=data["description"],
        inputs=inputs,
        scoring=scoring,
        baseline=dict(data.get("baseline") or {}),
        provenance=dict(data.get("provenance") or {}),
    )


def load_fixture(path: Path | str) -> Fixture:
    """Load + validate a single fixture YAML file. Raises
    ``FixtureValidationError`` on any contract break."""
    path = Path(path)
    try:
        with path.open() as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise FixtureValidationError(f"{path}: YAML parse failure: {e}") from e

    validate_fixture(data, fixture_label=str(path))
    return _build_fixture(data)


def score_fixture(fixture: Fixture, scoring_inputs: dict[str, Any]) -> ScoringResult:
    """Run the fixture's scoring function over ``scoring_inputs`` and
    return ``(score, pass_flag)``. ``scoring_inputs`` is the dict
    that an executor builds during fixture-run; structure is per
    scoring-function contract.

    Only ``type="numerical"`` is executable in T1. Rubric + composite
    raise NotImplementedError pointing at the future tranche.
    """
    if fixture.scoring.type == "numerical":
        from forest_soul_forge.benchmarks.scoring import SCORING_FUNCTIONS

        fn = SCORING_FUNCTIONS[fixture.scoring.function]
        score = float(fn(scoring_inputs))
        if score >= fixture.scoring.threshold_excellent:
            flag = PASS_FLAG_EXCELLENT
        elif score >= fixture.scoring.threshold_pass:
            flag = PASS_FLAG_PASS
        else:
            flag = PASS_FLAG_FAIL
        return ScoringResult(score=score, pass_flag=flag)

    raise NotImplementedError(
        f"scoring.type={fixture.scoring.type!r} executor lands in ADR-0023 T4+; "
        f"only 'numerical' is executable in T1."
    )
