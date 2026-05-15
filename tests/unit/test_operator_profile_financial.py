"""ADR-0068 T6 (B316) — financial + jurisdiction tests.

Coverage:
  - FinancialContext dataclass surface
  - OperatorProfile.financial defaults to None
  - Round-trip preservation through save -> load
  - YAML omits when None; preferred_tooling omits when empty
  - Loader refuses malformed entries (bad currency, tax_residence,
    fiscal_year, missing required, non-list preferred_tooling)
  - Reality Anchor seeds emit currency + tax_residence at HIGH +
    fiscal_year at MEDIUM
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from forest_soul_forge.core.operator_profile import (
    FinancialContext,
    OperatorProfile,
    OperatorProfileError,
    WorkHours,
    load_operator_profile,
    profile_to_ground_truth_seeds,
    save_operator_profile,
)


def _base_profile(**overrides) -> OperatorProfile:
    defaults = dict(
        schema_version=1,
        operator_id="op_1",
        name="Alex Price",
        preferred_name="Alex",
        email="alex@example.com",
        timezone="America/Los_Angeles",
        locale="en-US",
        work_hours=WorkHours(start="09:00", end="17:00"),
        created_at="2026-05-01T00:00:00+00:00",
        updated_at="2026-05-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    return OperatorProfile(**defaults)


# ---------------------------------------------------------------------------
# Dataclass surface
# ---------------------------------------------------------------------------

def test_financial_context_required_fields():
    f = FinancialContext(
        currency="USD", tax_residence="US-CA", fiscal_year_start="01-01",
    )
    assert f.currency == "USD"
    assert f.tax_residence == "US-CA"
    assert f.fiscal_year_start == "01-01"
    assert f.preferred_tooling == ()


def test_financial_context_with_tooling():
    f = FinancialContext(
        currency="USD", tax_residence="US-CA", fiscal_year_start="01-01",
        preferred_tooling=("Quicken", "YNAB"),
    )
    assert f.preferred_tooling == ("Quicken", "YNAB")


def test_profile_defaults_financial_to_none():
    profile = _base_profile()
    assert profile.financial is None


# ---------------------------------------------------------------------------
# Round-trip + YAML shape
# ---------------------------------------------------------------------------

def test_roundtrip_preserves_financial(tmp_path):
    profile = _base_profile(financial=FinancialContext(
        currency="USD", tax_residence="US-CA",
        fiscal_year_start="01-01",
        preferred_tooling=("Quicken", "YNAB"),
    ))
    path = tmp_path / "profile.yaml"
    save_operator_profile(profile, path)
    reloaded = load_operator_profile(path)
    assert reloaded.financial is not None
    assert reloaded.financial.currency == "USD"
    assert reloaded.financial.tax_residence == "US-CA"
    assert reloaded.financial.fiscal_year_start == "01-01"
    assert reloaded.financial.preferred_tooling == ("Quicken", "YNAB")


def test_yaml_omits_financial_when_none(tmp_path):
    profile = _base_profile()
    path = tmp_path / "profile.yaml"
    save_operator_profile(profile, path)
    assert "financial" not in path.read_text(encoding="utf-8")


def test_yaml_omits_preferred_tooling_when_empty(tmp_path):
    """Operator with no tooling preferences shouldn't see
    preferred_tooling: [] in the YAML — keeps the file minimal."""
    profile = _base_profile(financial=FinancialContext(
        currency="EUR", tax_residence="DE",
        fiscal_year_start="01-01",
    ))
    path = tmp_path / "profile.yaml"
    save_operator_profile(profile, path)
    raw = yaml.safe_load(path.read_text())
    assert "preferred_tooling" not in raw["operator"]["financial"]


# ---------------------------------------------------------------------------
# Loader refusals
# ---------------------------------------------------------------------------

def _write_with_financial(path: Path, financial_value) -> None:
    """Write a minimal profile YAML with the given financial value."""
    raw = {
        "schema_version": 1,
        "operator": {
            "operator_id": "op_1",
            "name": "Alex",
            "preferred_name": "Alex",
            "email": "a@b.com",
            "timezone": "UTC",
            "locale": "en-US",
            "work_hours": {"start": "09:00", "end": "17:00"},
            "financial": financial_value,
        },
        "created_at": "2026-05-01T00:00:00+00:00",
        "updated_at": "2026-05-01T00:00:00+00:00",
    }
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")


def _valid_financial() -> dict:
    return {
        "currency": "USD",
        "tax_residence": "US-CA",
        "fiscal_year_start": "01-01",
    }


def test_loader_refuses_non_dict_financial(tmp_path):
    p = tmp_path / "bad.yaml"
    _write_with_financial(p, "not-a-dict")
    with pytest.raises(OperatorProfileError, match="must be a mapping"):
        load_operator_profile(p)


@pytest.mark.parametrize("missing", [
    "currency", "tax_residence", "fiscal_year_start",
])
def test_loader_refuses_missing_required_field(tmp_path, missing):
    p = tmp_path / "bad.yaml"
    bad = _valid_financial()
    del bad[missing]
    _write_with_financial(p, bad)
    with pytest.raises(
        OperatorProfileError, match=f"missing required field '{missing}'",
    ):
        load_operator_profile(p)


@pytest.mark.parametrize("bad_currency", [
    "usd_lower", "US", "USDX", "12A", "", "$",
])
def test_loader_refuses_bad_currency(tmp_path, bad_currency):
    p = tmp_path / "bad.yaml"
    bad = _valid_financial() | {"currency": bad_currency}
    _write_with_financial(p, bad)
    with pytest.raises(OperatorProfileError, match="ISO 4217"):
        load_operator_profile(p)


@pytest.mark.parametrize("bad_tax", [
    "X", "USA", "us-CA", "12-CA",
])
def test_loader_refuses_bad_tax_residence(tmp_path, bad_tax):
    p = tmp_path / "bad.yaml"
    bad = _valid_financial() | {"tax_residence": bad_tax}
    _write_with_financial(p, bad)
    with pytest.raises(OperatorProfileError, match="3166-1"):
        load_operator_profile(p)


@pytest.mark.parametrize("bad_fy", [
    "13-01",     # bad month
    "01-32",     # bad day
    "00-15",     # zero month
    "01-00",     # zero day
    "1-1",       # not zero-padded
    "January 1", # not MM-DD
])
def test_loader_refuses_bad_fiscal_year_start(tmp_path, bad_fy):
    p = tmp_path / "bad.yaml"
    bad = _valid_financial() | {"fiscal_year_start": bad_fy}
    _write_with_financial(p, bad)
    with pytest.raises(OperatorProfileError, match="MM-DD"):
        load_operator_profile(p)


def test_loader_refuses_non_list_preferred_tooling(tmp_path):
    p = tmp_path / "bad.yaml"
    bad = _valid_financial() | {"preferred_tooling": "not-a-list"}
    _write_with_financial(p, bad)
    with pytest.raises(
        OperatorProfileError, match="must be a list",
    ):
        load_operator_profile(p)


def test_loader_refuses_non_string_tooling_entry(tmp_path):
    p = tmp_path / "bad.yaml"
    bad = _valid_financial() | {"preferred_tooling": [42]}
    _write_with_financial(p, bad)
    with pytest.raises(OperatorProfileError, match="non-empty string"):
        load_operator_profile(p)


# ---------------------------------------------------------------------------
# Reality Anchor seeds
# ---------------------------------------------------------------------------

def test_financial_seeds_emit_when_present():
    profile = _base_profile(financial=FinancialContext(
        currency="USD", tax_residence="US-CA",
        fiscal_year_start="01-01",
    ))
    seeds = profile_to_ground_truth_seeds(profile)
    ids = {s["id"] for s in seeds}
    assert "operator_currency" in ids
    assert "operator_tax_residence" in ids
    assert "operator_fiscal_year" in ids


def test_financial_seeds_severity_levels():
    profile = _base_profile(financial=FinancialContext(
        currency="USD", tax_residence="US-CA",
        fiscal_year_start="01-01",
    ))
    seeds = profile_to_ground_truth_seeds(profile)
    by_id = {s["id"]: s for s in seeds}
    assert by_id["operator_currency"]["severity"] == "HIGH"
    assert by_id["operator_tax_residence"]["severity"] == "HIGH"
    assert by_id["operator_fiscal_year"]["severity"] == "MEDIUM"


def test_financial_seeds_canonical_terms_include_values():
    profile = _base_profile(financial=FinancialContext(
        currency="GBP", tax_residence="GB-ENG",
        fiscal_year_start="04-06",
    ))
    seeds = profile_to_ground_truth_seeds(profile)
    by_id = {s["id"]: s for s in seeds}
    assert "GBP" in by_id["operator_currency"]["canonical_terms"]
    assert "GB-ENG" in by_id["operator_tax_residence"]["canonical_terms"]
    assert "04-06" in by_id["operator_fiscal_year"]["canonical_terms"]


def test_no_financial_seeds_when_financial_absent():
    profile = _base_profile()
    seeds = profile_to_ground_truth_seeds(profile)
    for seed in seeds:
        sid = seed["id"]
        assert sid != "operator_currency"
        assert sid != "operator_tax_residence"
        assert sid != "operator_fiscal_year"
