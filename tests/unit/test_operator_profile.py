"""ADR-0068 T1 (B277) — operator profile substrate tests.

Covers:
  - schema validation (required fields, formats)
  - load round-trip
  - save atomic + updated_at refresh
  - encryption-aware load/save round-trip
  - ground-truth seed generation
  - failure modes (missing file, malformed YAML, bad email, etc.)
"""
from __future__ import annotations

from datetime import datetime, timezone as _tz
from pathlib import Path

import pytest
import yaml

from forest_soul_forge.core.at_rest_encryption import EncryptionConfig
from forest_soul_forge.core.operator_profile import (
    SCHEMA_VERSION,
    OperatorProfile,
    OperatorProfileError,
    WorkHours,
    default_operator_profile_path,
    load_operator_profile,
    profile_to_ground_truth_seeds,
    save_operator_profile,
)


def _sample_profile() -> OperatorProfile:
    now = "2026-05-14T08:00:00Z"
    return OperatorProfile(
        schema_version=SCHEMA_VERSION,
        operator_id="test-op-01",
        name="Test Operator",
        preferred_name="Test",
        email="test@example.com",
        timezone="America/New_York",
        locale="en-US",
        work_hours=WorkHours(start="09:00", end="17:00"),
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# default_operator_profile_path
# ---------------------------------------------------------------------------
def test_default_path_under_data_dir(tmp_path):
    assert default_operator_profile_path(tmp_path) == (
        tmp_path / "operator" / "profile.yaml"
    )


def test_default_path_with_no_arg():
    p = default_operator_profile_path()
    assert p.parts[-2:] == ("operator", "profile.yaml")


# ---------------------------------------------------------------------------
# save + load round-trip
# ---------------------------------------------------------------------------
def test_save_and_load_round_trips(tmp_path):
    path = tmp_path / "profile.yaml"
    profile = _sample_profile()
    written = save_operator_profile(profile, path)
    assert written == path
    assert path.exists()

    loaded = load_operator_profile(path)
    assert loaded.operator_id == profile.operator_id
    assert loaded.name == profile.name
    assert loaded.email == profile.email
    assert loaded.timezone == profile.timezone
    assert loaded.work_hours.start == "09:00"
    assert loaded.work_hours.end == "17:00"


def test_save_updates_updated_at(tmp_path):
    path = tmp_path / "profile.yaml"
    profile = _sample_profile()
    save_operator_profile(profile, path)
    loaded = load_operator_profile(path)
    # updated_at gets refreshed to "now" on save
    assert loaded.updated_at != profile.updated_at
    # created_at preserved
    assert loaded.created_at == profile.created_at


def test_save_atomic_via_tmp(tmp_path):
    """Verify the atomic-write pattern: writes go to .tmp then rename."""
    path = tmp_path / "profile.yaml"
    save_operator_profile(_sample_profile(), path)
    # No .tmp leftover after successful save
    assert not (tmp_path / "profile.yaml.tmp").exists()


# ---------------------------------------------------------------------------
# Encryption-aware load + save
# ---------------------------------------------------------------------------
def test_save_encrypted_lands_at_enc(tmp_path):
    cfg = EncryptionConfig(master_key=b"\x42" * 32, kid="test")
    path = tmp_path / "profile.yaml"
    written = save_operator_profile(
        _sample_profile(), path, encryption_config=cfg,
    )
    assert written == tmp_path / "profile.yaml.enc"
    assert not path.exists()  # plaintext absent
    assert written.exists()


def test_load_decrypts_enc_variant(tmp_path):
    cfg = EncryptionConfig(master_key=b"\x42" * 32, kid="test")
    path = tmp_path / "profile.yaml"
    save_operator_profile(_sample_profile(), path, encryption_config=cfg)
    loaded = load_operator_profile(path, encryption_config=cfg)
    assert loaded.operator_id == "test-op-01"


def test_load_enc_without_config_raises(tmp_path):
    cfg = EncryptionConfig(master_key=b"\x42" * 32, kid="test")
    path = tmp_path / "profile.yaml"
    save_operator_profile(_sample_profile(), path, encryption_config=cfg)
    with pytest.raises(OperatorProfileError, match="encrypted"):
        load_operator_profile(path, encryption_config=None)


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------
def test_load_missing_file_raises(tmp_path):
    with pytest.raises(OperatorProfileError, match="not found"):
        load_operator_profile(tmp_path / "nope.yaml")


def test_load_malformed_yaml_raises(tmp_path):
    path = tmp_path / "profile.yaml"
    path.write_text("not: valid: yaml: :")
    with pytest.raises(OperatorProfileError, match="malformed"):
        load_operator_profile(path)


def test_load_schema_version_mismatch(tmp_path):
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump({"schema_version": 99, "operator": {}}))
    with pytest.raises(OperatorProfileError, match="schema_version"):
        load_operator_profile(path)


def test_load_missing_required_field(tmp_path):
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump({
        "schema_version": SCHEMA_VERSION,
        "operator": {
            "operator_id": "x",
            "name": "Alex",
            # email + timezone + locale + work_hours + preferred_name missing
        },
    }))
    with pytest.raises(OperatorProfileError, match="missing required"):
        load_operator_profile(path)


def test_load_bad_email(tmp_path):
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump({
        "schema_version": SCHEMA_VERSION,
        "operator": {
            "operator_id": "x", "name": "A", "preferred_name": "A",
            "email": "not-an-email",
            "timezone": "America/New_York",
            "locale": "en-US",
            "work_hours": {"start": "09:00", "end": "17:00"},
        },
    }))
    with pytest.raises(OperatorProfileError, match="email"):
        load_operator_profile(path)


def test_load_bad_timezone(tmp_path):
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump({
        "schema_version": SCHEMA_VERSION,
        "operator": {
            "operator_id": "x", "name": "A", "preferred_name": "A",
            "email": "a@b.com",
            "timezone": "Mars/Olympus_Mons",  # not a real IANA zone
            "locale": "en-US",
            "work_hours": {"start": "09:00", "end": "17:00"},
        },
    }))
    with pytest.raises(OperatorProfileError, match="timezone"):
        load_operator_profile(path)


def test_load_bad_work_hours_format(tmp_path):
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump({
        "schema_version": SCHEMA_VERSION,
        "operator": {
            "operator_id": "x", "name": "A", "preferred_name": "A",
            "email": "a@b.com",
            "timezone": "UTC",
            "locale": "en-US",
            "work_hours": {"start": "9am", "end": "5pm"},  # wrong format
        },
    }))
    with pytest.raises(OperatorProfileError, match="work_hours"):
        load_operator_profile(path)


# ---------------------------------------------------------------------------
# Reality Anchor seed generation
# ---------------------------------------------------------------------------
def test_profile_to_ground_truth_seeds_basic(tmp_path):
    seeds = profile_to_ground_truth_seeds(_sample_profile())
    # At least: name, email, timezone, locale, work_hours
    ids = [s["id"] for s in seeds]
    assert "operator_name" in ids
    assert "operator_email" in ids
    assert "operator_timezone" in ids
    assert "operator_locale" in ids
    assert "operator_work_hours" in ids


def test_profile_to_ground_truth_omits_preferred_when_same_as_name():
    """When preferred_name == name, skip the redundant seed."""
    profile = OperatorProfile(
        schema_version=1,
        operator_id="x", name="Sam", preferred_name="Sam",
        email="a@b.com",
        timezone="UTC", locale="en-US",
        work_hours=WorkHours(start="09:00", end="17:00"),
        created_at="2026-05-14T00:00:00Z",
        updated_at="2026-05-14T00:00:00Z",
    )
    seeds = profile_to_ground_truth_seeds(profile)
    assert not any(s["id"] == "operator_preferred_name" for s in seeds)


def test_profile_to_ground_truth_includes_preferred_when_different():
    profile = OperatorProfile(
        schema_version=1,
        operator_id="x", name="Samantha", preferred_name="Sam",
        email="a@b.com",
        timezone="UTC", locale="en-US",
        work_hours=WorkHours(start="09:00", end="17:00"),
        created_at="2026-05-14T00:00:00Z",
        updated_at="2026-05-14T00:00:00Z",
    )
    seeds = profile_to_ground_truth_seeds(profile)
    assert any(s["id"] == "operator_preferred_name" for s in seeds)
