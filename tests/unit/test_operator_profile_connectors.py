"""ADR-0068 T7a (B317) — connector consent substrate tests.

Coverage:
  - ConnectorConsent dataclass surface
  - OperatorProfile.connectors defaults to ()
  - Round-trip + YAML omit-when-empty + per-entry optional fields
  - Loader refusals (non-list, missing required, bad status,
    decided_at required for non-pending, duplicate keys)
  - upsert_connector_consent: replaces existing, appends new,
    auto-stamps decided_at for non-pending, refuses bad status
  - Audit event 'operator_connector_consent_changed' registered
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from forest_soul_forge.core.audit_chain import KNOWN_EVENT_TYPES
from forest_soul_forge.core.operator_profile import (
    ConnectorConsent,
    OperatorProfile,
    OperatorProfileError,
    WorkHours,
    load_operator_profile,
    save_operator_profile,
    upsert_connector_consent,
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
# Dataclass + defaults
# ---------------------------------------------------------------------------

def test_connector_consent_required_fields():
    c = ConnectorConsent(
        domain_id="d2", connector_name="gmail", status="pending",
    )
    assert c.domain_id == "d2"
    assert c.status == "pending"
    assert c.decided_at is None
    assert c.notes is None


def test_connector_consent_full_fields():
    c = ConnectorConsent(
        domain_id="d2", connector_name="gmail",
        status="granted", decided_at="2026-05-10T00:00:00+00:00",
        notes="needed for inbox triage",
    )
    assert c.notes == "needed for inbox triage"


def test_profile_defaults_empty_connectors():
    assert _base_profile().connectors == ()


# ---------------------------------------------------------------------------
# Round-trip + YAML shape
# ---------------------------------------------------------------------------

def test_roundtrip_preserves_connectors(tmp_path):
    profile = _base_profile(connectors=(
        ConnectorConsent(
            domain_id="d2", connector_name="gmail",
            status="granted",
            decided_at="2026-05-10T00:00:00+00:00",
        ),
        ConnectorConsent(
            domain_id="d2", connector_name="gcal",
            status="pending",
        ),
    ))
    path = tmp_path / "profile.yaml"
    save_operator_profile(profile, path)
    reloaded = load_operator_profile(path)
    assert len(reloaded.connectors) == 2
    assert reloaded.connectors[0].status == "granted"
    assert reloaded.connectors[1].status == "pending"
    assert reloaded.connectors[1].decided_at is None


def test_yaml_omits_empty_connectors(tmp_path):
    path = tmp_path / "profile.yaml"
    save_operator_profile(_base_profile(), path)
    assert "connectors" not in path.read_text()


def test_yaml_omits_per_entry_optional_fields(tmp_path):
    profile = _base_profile(connectors=(
        ConnectorConsent(
            domain_id="d", connector_name="c", status="pending",
        ),
    ))
    path = tmp_path / "profile.yaml"
    save_operator_profile(profile, path)
    raw = yaml.safe_load(path.read_text())
    entry = raw["operator"]["connectors"][0]
    assert "decided_at" not in entry
    assert "notes" not in entry


# ---------------------------------------------------------------------------
# Loader refusals
# ---------------------------------------------------------------------------

def _write_with_connectors(path: Path, value) -> None:
    raw = {
        "schema_version": 1,
        "operator": {
            "operator_id": "op_1", "name": "X", "preferred_name": "X",
            "email": "x@y.com", "timezone": "UTC", "locale": "en-US",
            "work_hours": {"start": "09:00", "end": "17:00"},
            "connectors": value,
        },
        "created_at": "2026-05-01T00:00:00+00:00",
        "updated_at": "2026-05-01T00:00:00+00:00",
    }
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")


def test_loader_refuses_non_list(tmp_path):
    p = tmp_path / "bad.yaml"
    _write_with_connectors(p, "not-a-list")
    with pytest.raises(OperatorProfileError, match="must be a list"):
        load_operator_profile(p)


def test_loader_refuses_non_dict_entry(tmp_path):
    p = tmp_path / "bad.yaml"
    _write_with_connectors(p, ["string-entry"])
    with pytest.raises(OperatorProfileError, match="must be a mapping"):
        load_operator_profile(p)


@pytest.mark.parametrize("missing", [
    "domain_id", "connector_name", "status",
])
def test_loader_refuses_missing_required(tmp_path, missing):
    full = {
        "domain_id": "d", "connector_name": "c", "status": "pending",
    }
    del full[missing]
    p = tmp_path / "bad.yaml"
    _write_with_connectors(p, [full])
    with pytest.raises(
        OperatorProfileError, match=f"missing required field '{missing}'",
    ):
        load_operator_profile(p)


def test_loader_refuses_bad_status(tmp_path):
    p = tmp_path / "bad.yaml"
    _write_with_connectors(p, [{
        "domain_id": "d", "connector_name": "c", "status": "bogus",
    }])
    with pytest.raises(OperatorProfileError, match="must be one of"):
        load_operator_profile(p)


def test_loader_requires_decided_at_for_non_pending(tmp_path):
    p = tmp_path / "bad.yaml"
    _write_with_connectors(p, [{
        "domain_id": "d", "connector_name": "c", "status": "granted",
    }])
    with pytest.raises(OperatorProfileError, match="decided_at"):
        load_operator_profile(p)


def test_loader_refuses_duplicate_pair(tmp_path):
    p = tmp_path / "bad.yaml"
    _write_with_connectors(p, [
        {"domain_id": "d", "connector_name": "c", "status": "pending"},
        {"domain_id": "d", "connector_name": "c", "status": "pending"},
    ])
    with pytest.raises(OperatorProfileError, match="duplicate"):
        load_operator_profile(p)


# ---------------------------------------------------------------------------
# upsert_connector_consent
# ---------------------------------------------------------------------------

def test_upsert_replaces_existing_entry():
    profile = _base_profile(connectors=(
        ConnectorConsent(
            domain_id="d2", connector_name="gmail",
            status="granted",
            decided_at="2026-05-10T00:00:00+00:00",
        ),
    ))
    new_profile = upsert_connector_consent(
        profile,
        domain_id="d2", connector_name="gmail",
        status="denied", notes="changed my mind",
    )
    assert len(new_profile.connectors) == 1
    entry = new_profile.connectors[0]
    assert entry.status == "denied"
    assert entry.notes == "changed my mind"
    assert entry.decided_at is not None


def test_upsert_appends_new_entry():
    profile = _base_profile(connectors=(
        ConnectorConsent(
            domain_id="d2", connector_name="gmail",
            status="pending",
        ),
    ))
    new_profile = upsert_connector_consent(
        profile,
        domain_id="d3", connector_name="slack",
        status="granted",
    )
    assert len(new_profile.connectors) == 2
    new_entry = next(
        c for c in new_profile.connectors if c.connector_name == "slack"
    )
    assert new_entry.status == "granted"


def test_upsert_auto_stamps_decided_at_for_non_pending():
    profile = _base_profile()
    new_profile = upsert_connector_consent(
        profile,
        domain_id="d", connector_name="c", status="granted",
    )
    assert new_profile.connectors[0].decided_at is not None


def test_upsert_leaves_decided_at_none_for_pending():
    profile = _base_profile()
    new_profile = upsert_connector_consent(
        profile,
        domain_id="d", connector_name="c", status="pending",
    )
    assert new_profile.connectors[0].decided_at is None


def test_upsert_refuses_bad_status():
    profile = _base_profile()
    with pytest.raises(OperatorProfileError, match="must be one of"):
        upsert_connector_consent(
            profile,
            domain_id="d", connector_name="c", status="bogus",
        )


def test_upsert_returns_new_profile_object():
    """Pure function — input profile is unchanged."""
    profile = _base_profile()
    new_profile = upsert_connector_consent(
        profile,
        domain_id="d", connector_name="c", status="granted",
    )
    assert profile is not new_profile
    assert profile.connectors == ()  # original unchanged
    assert len(new_profile.connectors) == 1


# ---------------------------------------------------------------------------
# Audit event registration
# ---------------------------------------------------------------------------

def test_consent_changed_event_registered():
    """The POST endpoint emits this on every consent change.
    Verifier must accept it."""
    assert "operator_connector_consent_changed" in KNOWN_EVENT_TYPES
