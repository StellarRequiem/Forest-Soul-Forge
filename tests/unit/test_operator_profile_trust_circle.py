"""ADR-0068 T4 (B314) — trust circle extension tests.

Covers:
  - TrustCirclePerson dataclass + required vs optional fields
  - OperatorProfile.trust_circle defaults to empty tuple
  - Round-trip preservation through save -> load
  - YAML omits trust_circle when empty
  - YAML omits per-person optional fields when None
  - Loader refuses malformed entries (missing required, wrong types)
  - profile_to_ground_truth_seeds emits one HIGH-severity seed per
    person, with email surfaced when present
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from forest_soul_forge.core.operator_profile import (
    OperatorProfile,
    OperatorProfileError,
    TrustCirclePerson,
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

def test_person_dataclass_required_fields_only():
    p = TrustCirclePerson(name="A", relationship="friend")
    assert p.name == "A"
    assert p.relationship == "friend"
    assert p.email is None
    assert p.notes is None


def test_person_dataclass_optional_fields():
    p = TrustCirclePerson(
        name="B", relationship="colleague",
        email="b@x.com", notes="met at conf",
    )
    assert p.email == "b@x.com"
    assert p.notes == "met at conf"


def test_operator_profile_defaults_empty_trust_circle():
    """A profile constructed without trust_circle gets () by default —
    backward-compat with pre-T4 yamls."""
    profile = _base_profile()
    assert profile.trust_circle == ()


# ---------------------------------------------------------------------------
# Round-trip + YAML shape
# ---------------------------------------------------------------------------

def test_roundtrip_preserves_trust_circle(tmp_path):
    profile = _base_profile(trust_circle=(
        TrustCirclePerson(name="Mira", relationship="spouse"),
        TrustCirclePerson(
            name="Sara", relationship="accountant",
            email="sara@firm.example",
        ),
        TrustCirclePerson(
            name="Dr. Kim", relationship="physician",
            notes="annual physical in March",
        ),
    ))
    path = tmp_path / "profile.yaml"
    save_operator_profile(profile, path)

    reloaded = load_operator_profile(path)
    assert len(reloaded.trust_circle) == 3
    assert reloaded.trust_circle[0].name == "Mira"
    assert reloaded.trust_circle[0].email is None  # not set
    assert reloaded.trust_circle[1].email == "sara@firm.example"
    assert reloaded.trust_circle[2].notes == "annual physical in March"


def test_yaml_omits_empty_trust_circle(tmp_path):
    """Empty trust_circle should NOT appear in the YAML — keeps the
    minimum-disclosure principle for operators who haven't filled it in."""
    profile = _base_profile()
    path = tmp_path / "profile.yaml"
    save_operator_profile(profile, path)
    text = path.read_text(encoding="utf-8")
    assert "trust_circle" not in text


def test_yaml_omits_per_person_optional_fields(tmp_path):
    """Person with only required fields shouldn't get email: null
    in the YAML — keeps the file diff-stable."""
    profile = _base_profile(trust_circle=(
        TrustCirclePerson(name="Mira", relationship="spouse"),
    ))
    path = tmp_path / "profile.yaml"
    save_operator_profile(profile, path)
    raw = yaml.safe_load(path.read_text())
    person_yaml = raw["operator"]["trust_circle"][0]
    assert "email" not in person_yaml
    assert "notes" not in person_yaml


# ---------------------------------------------------------------------------
# Loader refusals
# ---------------------------------------------------------------------------

def _write_yaml(path: Path, trust_circle_value):
    """Build a minimal valid profile YAML with the given
    trust_circle value (which may be malformed)."""
    profile = _base_profile()
    raw = {
        "schema_version": 1,
        "operator": {
            "operator_id": profile.operator_id,
            "name": profile.name,
            "preferred_name": profile.preferred_name,
            "email": profile.email,
            "timezone": profile.timezone,
            "locale": profile.locale,
            "work_hours": {
                "start": profile.work_hours.start,
                "end": profile.work_hours.end,
            },
            "trust_circle": trust_circle_value,
        },
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")


def test_loader_refuses_non_list_trust_circle(tmp_path):
    p = tmp_path / "bad.yaml"
    _write_yaml(p, "not_a_list")
    with pytest.raises(OperatorProfileError, match="must be a list"):
        load_operator_profile(p)


def test_loader_refuses_entry_missing_name(tmp_path):
    p = tmp_path / "bad.yaml"
    _write_yaml(p, [{"relationship": "spouse"}])
    with pytest.raises(
        OperatorProfileError, match="missing required field 'name'",
    ):
        load_operator_profile(p)


def test_loader_refuses_entry_missing_relationship(tmp_path):
    p = tmp_path / "bad.yaml"
    _write_yaml(p, [{"name": "Mira"}])
    with pytest.raises(
        OperatorProfileError, match="missing required field 'relationship'",
    ):
        load_operator_profile(p)


def test_loader_refuses_non_string_required_field(tmp_path):
    p = tmp_path / "bad.yaml"
    _write_yaml(p, [{"name": 42, "relationship": "spouse"}])
    with pytest.raises(OperatorProfileError, match="must be a non-empty string"):
        load_operator_profile(p)


def test_loader_refuses_non_dict_entry(tmp_path):
    p = tmp_path / "bad.yaml"
    _write_yaml(p, ["just-a-string"])
    with pytest.raises(OperatorProfileError, match="must be a mapping"):
        load_operator_profile(p)


# ---------------------------------------------------------------------------
# Reality Anchor seeds
# ---------------------------------------------------------------------------

def test_seeds_emit_one_per_person():
    profile = _base_profile(trust_circle=(
        TrustCirclePerson(name="Mira", relationship="spouse"),
        TrustCirclePerson(name="Sara", relationship="accountant"),
    ))
    seeds = profile_to_ground_truth_seeds(profile)
    trust_seeds = [s for s in seeds if s["id"].startswith("operator_trust_")]
    assert len(trust_seeds) == 2
    ids = sorted(s["id"] for s in trust_seeds)
    assert ids == ["operator_trust_accountant", "operator_trust_spouse"]


def test_seeds_severity_is_high():
    """Mis-identifying a trust-circle person is high-stakes — every
    person-anchored seed lands at HIGH severity so a contradiction
    surfaces as a refusal, not a flag."""
    profile = _base_profile(trust_circle=(
        TrustCirclePerson(name="Mira", relationship="spouse"),
    ))
    seeds = profile_to_ground_truth_seeds(profile)
    trust_seed = next(s for s in seeds if s["id"].startswith("operator_trust_"))
    assert trust_seed["severity"] == "HIGH"


def test_seeds_include_email_in_statement_when_present():
    profile = _base_profile(trust_circle=(
        TrustCirclePerson(
            name="Sara", relationship="accountant",
            email="sara@firm.example",
        ),
    ))
    seeds = profile_to_ground_truth_seeds(profile)
    accountant = next(
        s for s in seeds if s["id"] == "operator_trust_accountant"
    )
    assert "sara@firm.example" in accountant["statement"]


def test_seeds_omit_email_when_not_present():
    profile = _base_profile(trust_circle=(
        TrustCirclePerson(name="Mira", relationship="spouse"),
    ))
    seeds = profile_to_ground_truth_seeds(profile)
    spouse = next(
        s for s in seeds if s["id"] == "operator_trust_spouse"
    )
    # No email present means the statement shouldn't carry one.
    assert "@" not in spouse["statement"]


def test_seeds_canonical_terms_include_name_and_relationship():
    """The Reality Anchor pattern-match needs both terms in canonical_terms
    so a claim like 'your spouse is X' (wrong name) AND 'Mira is your
    coworker' (wrong relationship) both surface."""
    profile = _base_profile(trust_circle=(
        TrustCirclePerson(name="Mira", relationship="spouse"),
    ))
    seeds = profile_to_ground_truth_seeds(profile)
    spouse = next(
        s for s in seeds if s["id"] == "operator_trust_spouse"
    )
    assert "Mira" in spouse["canonical_terms"]
    assert "spouse" in spouse["canonical_terms"]
