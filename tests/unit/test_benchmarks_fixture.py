"""Tests for ADR-0023 T1 fixture loader + validator.

Pins the contract on:
  * happy-path load from YAML
  * validation rejection on every required-field absence
  * fixture_id ↔ name/version invariant
  * genre allowlist
  * scoring threshold ordering
  * scoring.function-must-resolve check (numerical type)
  * fixture vs directory-name mismatch (registry path)
  * duplicate fixture_id detection (registry path)
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from forest_soul_forge.benchmarks import (
    Fixture,
    FixtureValidationError,
    PASS_FLAG_EXCELLENT,
    PASS_FLAG_FAIL,
    PASS_FLAG_PASS,
    load_fixture,
    load_fixtures_from_dir,
    score_fixture,
    validate_fixture,
)


GOOD_FIXTURE_YAML = textwrap.dedent(
    """\
    fixture_id: signal_detection.v1
    genre: observer
    name: signal_detection
    version: "1"
    description: |
      Replay synthetic traffic with N labeled anomalies.
    inputs:
      - type: traffic_replay
        source: fixtures/observer/traffic_50min.pcap
      - type: labels
        source: fixtures/observer/traffic_50min_labels.json
    scoring:
      type: numerical
      function: detection_rate
      threshold:
        pass: 0.7
        excellent: 0.9
    baseline:
      random_agent_score: 0.05
      templated_agent_score: 0.40
    provenance:
      fixture_authored_at: "2026-05-20"
    """
)


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content)
    return p


# ──────────────────────────────────────────────────────────────────────
# Happy path
# ──────────────────────────────────────────────────────────────────────

def test_load_fixture_happy_path(tmp_path: Path) -> None:
    path = _write(tmp_path, "f.yaml", GOOD_FIXTURE_YAML)
    fixture = load_fixture(path)
    assert isinstance(fixture, Fixture)
    assert fixture.fixture_id == "signal_detection.v1"
    assert fixture.genre == "observer"
    assert fixture.name == "signal_detection"
    assert fixture.version == "1"
    assert len(fixture.inputs) == 2
    assert fixture.inputs[0].type == "traffic_replay"
    assert fixture.scoring.type == "numerical"
    assert fixture.scoring.function == "detection_rate"
    assert fixture.scoring.threshold_pass == pytest.approx(0.7)
    assert fixture.scoring.threshold_excellent == pytest.approx(0.9)
    assert fixture.baseline["random_agent_score"] == pytest.approx(0.05)
    assert fixture.provenance["fixture_authored_at"] == "2026-05-20"


# ──────────────────────────────────────────────────────────────────────
# Required-field validation
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "missing_field",
    ["fixture_id", "genre", "name", "version", "description", "inputs", "scoring"],
)
def test_validate_rejects_missing_required_field(missing_field: str) -> None:
    import yaml as _yaml
    data = _yaml.safe_load(GOOD_FIXTURE_YAML)
    del data[missing_field]
    with pytest.raises(FixtureValidationError, match=missing_field):
        validate_fixture(data)


# ──────────────────────────────────────────────────────────────────────
# fixture_id ↔ name/version
# ──────────────────────────────────────────────────────────────────────

def test_fixture_id_must_match_name_and_version() -> None:
    import yaml as _yaml
    data = _yaml.safe_load(GOOD_FIXTURE_YAML)
    data["fixture_id"] = "signal_detection.v2"   # mismatched
    with pytest.raises(FixtureValidationError, match="must equal"):
        validate_fixture(data)


def test_fixture_id_shape_enforced() -> None:
    import yaml as _yaml
    data = _yaml.safe_load(GOOD_FIXTURE_YAML)
    data["fixture_id"] = "Bad-ID v1"
    with pytest.raises(FixtureValidationError, match="must match"):
        validate_fixture(data)


# ──────────────────────────────────────────────────────────────────────
# Genre allowlist
# ──────────────────────────────────────────────────────────────────────

def test_validate_rejects_unknown_genre() -> None:
    import yaml as _yaml
    data = _yaml.safe_load(GOOD_FIXTURE_YAML)
    data["genre"] = "alchemist"   # not in KNOWN_GENRES
    with pytest.raises(FixtureValidationError, match="alchemist"):
        validate_fixture(data)


# ──────────────────────────────────────────────────────────────────────
# Threshold ordering
# ──────────────────────────────────────────────────────────────────────

def test_validate_rejects_pass_geq_excellent() -> None:
    import yaml as _yaml
    data = _yaml.safe_load(GOOD_FIXTURE_YAML)
    data["scoring"]["threshold"]["pass"] = 0.95
    data["scoring"]["threshold"]["excellent"] = 0.9
    with pytest.raises(FixtureValidationError, match="must be <"):
        validate_fixture(data)


# ──────────────────────────────────────────────────────────────────────
# Numerical-function existence
# ──────────────────────────────────────────────────────────────────────

def test_validate_rejects_unknown_numerical_function() -> None:
    import yaml as _yaml
    data = _yaml.safe_load(GOOD_FIXTURE_YAML)
    data["scoring"]["function"] = "does_not_exist"
    with pytest.raises(FixtureValidationError, match="not registered"):
        validate_fixture(data)


def test_validate_accepts_rubric_function_without_resolution() -> None:
    """Rubric-typed scoring doesn't get resolved at T1 — T4 will. The
    loader only checks shape for non-numerical scoring types."""
    import yaml as _yaml
    data = _yaml.safe_load(GOOD_FIXTURE_YAML)
    data["scoring"]["type"] = "rubric"
    data["scoring"]["function"] = "some_future_rubric_judge"  # not in catalog yet
    validate_fixture(data)  # should NOT raise


# ──────────────────────────────────────────────────────────────────────
# score_fixture pass-flag logic
# ──────────────────────────────────────────────────────────────────────

def test_score_fixture_fail_below_pass(tmp_path: Path) -> None:
    fixture = load_fixture(_write(tmp_path, "f.yaml", GOOD_FIXTURE_YAML))
    res = score_fixture(fixture, {"true_positives": 50, "total_positives": 100})
    assert res.score == pytest.approx(0.5)
    assert res.pass_flag == PASS_FLAG_FAIL


def test_score_fixture_pass_between_pass_and_excellent(tmp_path: Path) -> None:
    fixture = load_fixture(_write(tmp_path, "f.yaml", GOOD_FIXTURE_YAML))
    res = score_fixture(fixture, {"true_positives": 80, "total_positives": 100})
    assert res.score == pytest.approx(0.8)
    assert res.pass_flag == PASS_FLAG_PASS


def test_score_fixture_excellent_at_or_above_excellent(tmp_path: Path) -> None:
    fixture = load_fixture(_write(tmp_path, "f.yaml", GOOD_FIXTURE_YAML))
    res = score_fixture(fixture, {"true_positives": 95, "total_positives": 100})
    assert res.score == pytest.approx(0.95)
    assert res.pass_flag == PASS_FLAG_EXCELLENT


def test_score_fixture_rubric_raises_not_implemented(tmp_path: Path) -> None:
    """Rubric scoring is T4+; T1 must raise NotImplementedError so
    callers know to defer rather than silently produce a score."""
    import yaml as _yaml
    data = _yaml.safe_load(GOOD_FIXTURE_YAML)
    data["scoring"]["type"] = "rubric"
    data["scoring"]["function"] = "future_judge"
    import yaml
    path = _write(tmp_path, "f.yaml", yaml.dump(data))
    fixture = load_fixture(path)
    with pytest.raises(NotImplementedError, match="T4"):
        score_fixture(fixture, {})


# ──────────────────────────────────────────────────────────────────────
# Registry: load_fixtures_from_dir
# ──────────────────────────────────────────────────────────────────────

def test_load_fixtures_from_dir_discovers_per_genre(tmp_path: Path) -> None:
    (tmp_path / "observer").mkdir()
    (tmp_path / "observer" / "signal_detection.v1.yaml").write_text(GOOD_FIXTURE_YAML)
    fixtures = load_fixtures_from_dir(tmp_path)
    assert set(fixtures.keys()) == {"signal_detection.v1"}
    assert fixtures["signal_detection.v1"].genre == "observer"


def test_load_fixtures_from_dir_rejects_genre_dir_mismatch(tmp_path: Path) -> None:
    """A fixture declaring genre=observer must live under observer/.
    Misfiled fixtures are silent landmines; the loader catches them."""
    (tmp_path / "guardian").mkdir()
    (tmp_path / "guardian" / "signal_detection.v1.yaml").write_text(GOOD_FIXTURE_YAML)
    with pytest.raises(FixtureValidationError, match="doesn't match directory name"):
        load_fixtures_from_dir(tmp_path)


def test_load_fixtures_from_dir_rejects_duplicate_fixture_id(tmp_path: Path) -> None:
    (tmp_path / "observer").mkdir()
    (tmp_path / "observer" / "signal_detection.v1.yaml").write_text(GOOD_FIXTURE_YAML)
    (tmp_path / "investigator").mkdir()
    # Rewrite the fixture to declare investigator genre + same id;
    # this MUST fail because fixture_id uniqueness is global.
    import yaml as _yaml
    data = _yaml.safe_load(GOOD_FIXTURE_YAML)
    data["genre"] = "investigator"
    (tmp_path / "investigator" / "signal_detection.v1.yaml").write_text(_yaml.dump(data))
    with pytest.raises(FixtureValidationError, match="duplicate fixture_id"):
        load_fixtures_from_dir(tmp_path)


def test_load_fixtures_from_dir_missing_root_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_fixtures_from_dir(tmp_path / "does-not-exist")


def test_load_fixtures_from_dir_strict_false_skips_bad_files(tmp_path: Path) -> None:
    (tmp_path / "observer").mkdir()
    (tmp_path / "observer" / "good.v1.yaml").write_text(GOOD_FIXTURE_YAML)
    (tmp_path / "observer" / "bad.v1.yaml").write_text("not valid yaml: {[")
    fixtures = load_fixtures_from_dir(tmp_path, strict=False)
    # bad file silently skipped; good file loaded
    assert "signal_detection.v1" in fixtures
    assert len(fixtures) == 1


# ──────────────────────────────────────────────────────────────────────
# T1.1 (B446): higher_is_better=False support
# ──────────────────────────────────────────────────────────────────────

LOWER_IS_BETTER_YAML = textwrap.dedent(
    """\
    fixture_id: false_positive_rate.v1
    genre: observer
    name: false_positive_rate
    version: "1"
    description: |
      Lower false-positive rate is better.
    inputs:
      - type: traffic_replay
        source: fixtures/observer/clean.pcap
    scoring:
      type: numerical
      function: false_positive_rate
      higher_is_better: false
      threshold:
        excellent: 0.05
        pass: 0.20
    """
)


def test_higher_is_better_default_true(tmp_path: Path) -> None:
    """Fixtures that don't specify higher_is_better default to True."""
    fixture = load_fixture(_write(tmp_path, "f.yaml", GOOD_FIXTURE_YAML))
    assert fixture.scoring.higher_is_better is True


def test_higher_is_better_false_loads_with_inverted_thresholds(tmp_path: Path) -> None:
    fixture = load_fixture(_write(tmp_path, "f.yaml", LOWER_IS_BETTER_YAML))
    assert fixture.scoring.higher_is_better is False
    assert fixture.scoring.threshold_pass == pytest.approx(0.20)
    assert fixture.scoring.threshold_excellent == pytest.approx(0.05)


def test_higher_is_better_false_requires_excellent_lt_pass() -> None:
    """When direction is False, pass must be > excellent (tighter is
    excellent). Validator must reject ordering that's correct under
    True but wrong under False."""
    import yaml as _yaml
    data = _yaml.safe_load(LOWER_IS_BETTER_YAML)
    # Restore the True-direction ordering (pass < excellent); validator
    # should reject when higher_is_better is False.
    data["scoring"]["threshold"]["pass"] = 0.05
    data["scoring"]["threshold"]["excellent"] = 0.20
    with pytest.raises(FixtureValidationError, match="when higher_is_better=False"):
        validate_fixture(data)


def test_higher_is_better_true_still_requires_pass_lt_excellent() -> None:
    """When direction is True (default), pass must be < excellent.
    Flipped ordering is rejected — same as before T1.1."""
    import yaml as _yaml
    data = _yaml.safe_load(GOOD_FIXTURE_YAML)
    data["scoring"]["threshold"]["pass"] = 0.9
    data["scoring"]["threshold"]["excellent"] = 0.7
    with pytest.raises(FixtureValidationError, match="when higher_is_better=True"):
        validate_fixture(data)


def test_higher_is_better_must_be_bool() -> None:
    import yaml as _yaml
    data = _yaml.safe_load(LOWER_IS_BETTER_YAML)
    data["scoring"]["higher_is_better"] = "false"  # string, not bool
    with pytest.raises(FixtureValidationError, match="must be bool"):
        validate_fixture(data)


def test_score_fixture_lower_is_better_excellent(tmp_path: Path) -> None:
    fixture = load_fixture(_write(tmp_path, "f.yaml", LOWER_IS_BETTER_YAML))
    # 3% fpr is BELOW the 5% excellent cutoff -> excellent
    res = score_fixture(fixture, {"false_positives": 3, "total_negatives": 100})
    assert res.score == pytest.approx(0.03)
    assert res.pass_flag == PASS_FLAG_EXCELLENT


def test_score_fixture_lower_is_better_pass(tmp_path: Path) -> None:
    fixture = load_fixture(_write(tmp_path, "f.yaml", LOWER_IS_BETTER_YAML))
    # 15% fpr is above excellent (5%) but at-or-below pass (20%) -> pass
    res = score_fixture(fixture, {"false_positives": 15, "total_negatives": 100})
    assert res.score == pytest.approx(0.15)
    assert res.pass_flag == PASS_FLAG_PASS


def test_score_fixture_lower_is_better_fail(tmp_path: Path) -> None:
    fixture = load_fixture(_write(tmp_path, "f.yaml", LOWER_IS_BETTER_YAML))
    # 40% fpr is above pass (20%) -> fail
    res = score_fixture(fixture, {"false_positives": 40, "total_negatives": 100})
    assert res.score == pytest.approx(0.40)
    assert res.pass_flag == PASS_FLAG_FAIL
