"""ADR-0068 T1.1 (B278) — operator profile merged into ground truth.

Verifies that load_ground_truth() pulls in operator-profile-derived
facts so every Reality Anchor consumer (dispatcher, conversation
gate, /reality-anchor router, verify_claim.v1) sees personal facts
without separate plumbing.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from forest_soul_forge.core.ground_truth import (
    ENV_VAR as GT_ENV_VAR,
    load_ground_truth,
)
from forest_soul_forge.core.operator_profile import (
    OperatorProfile,
    WorkHours,
    default_operator_profile_path,
    save_operator_profile,
)


def _seed_profile(data_dir: Path) -> None:
    """Drop a fresh profile.yaml into the given data_dir's operator
    directory for the test scope. Returns the absolute path written."""
    profile_path = data_dir / "operator" / "profile.yaml"
    save_operator_profile(
        OperatorProfile(
            schema_version=1,
            operator_id="merge-test-01",
            name="MergeTest",
            preferred_name="Tester",
            email="merge@example.com",
            timezone="UTC",
            locale="en-US",
            work_hours=WorkHours(start="08:00", end="16:00"),
            created_at="2026-05-14T00:00:00Z",
            updated_at="2026-05-14T00:00:00Z",
        ),
        profile_path,
    )


def _seed_catalog(catalog_path: Path) -> None:
    """Minimal operator-global ground_truth.yaml so load doesn't
    bail on missing file."""
    catalog_path.write_text(yaml.safe_dump({
        "catalog_version": 1,
        "facts": [
            {
                "id": "test_invariant",
                "statement": "This is a test invariant.",
                "domain_keywords": ["test"],
                "canonical_terms": ["invariant"],
                "forbidden_terms": ["mutable"],
                "severity": "LOW",
            },
        ],
    }))


def test_load_ground_truth_merges_operator_profile(
    tmp_path, monkeypatch,
):
    """Profile-derived facts join the catalog in the same return
    list. Reality Anchor consumers get them transparently."""
    # Point both catalog + profile paths into tmp_path.
    catalog = tmp_path / "ground_truth.yaml"
    _seed_catalog(catalog)
    monkeypatch.setenv(GT_ENV_VAR, str(catalog))

    # Profile loader uses default_operator_profile_path() which
    # resolves to "data/operator/profile.yaml" relative to cwd.
    # Run the test from tmp_path so the relative path lands inside.
    monkeypatch.chdir(tmp_path)
    _seed_profile(tmp_path / "data")

    facts, errors = load_ground_truth()
    fact_ids = {f.id for f in facts}

    # The operator-global catalog fact survives.
    assert "test_invariant" in fact_ids
    # All 6 profile seeds (5 unconditional + preferred_name since
    # MergeTest != Tester) land.
    assert "operator_name" in fact_ids
    assert "operator_preferred_name" in fact_ids
    assert "operator_email" in fact_ids
    assert "operator_timezone" in fact_ids
    assert "operator_locale" in fact_ids
    assert "operator_work_hours" in fact_ids
    # No errors on the happy path.
    assert not errors


def test_load_ground_truth_no_profile_is_soft_failure(
    tmp_path, monkeypatch,
):
    """Missing profile = catalog still loads; non-fatal note in
    the errors list. Reality Anchor degrades gracefully."""
    catalog = tmp_path / "ground_truth.yaml"
    _seed_catalog(catalog)
    monkeypatch.setenv(GT_ENV_VAR, str(catalog))
    monkeypatch.chdir(tmp_path)
    # NO profile created.

    facts, errors = load_ground_truth()
    fact_ids = {f.id for f in facts}
    assert "test_invariant" in fact_ids
    # No profile seeds were merged.
    assert not any(fid.startswith("operator_") for fid in fact_ids)
    # An error note is present so /reality-anchor/status surfaces it.
    assert any("operator profile" in e.lower() for e in errors)


def test_operator_profile_fact_source(tmp_path, monkeypatch):
    """Profile-derived facts carry source='operator_profile' so
    auditors can tell them apart from the global catalog."""
    catalog = tmp_path / "ground_truth.yaml"
    _seed_catalog(catalog)
    monkeypatch.setenv(GT_ENV_VAR, str(catalog))
    monkeypatch.chdir(tmp_path)
    _seed_profile(tmp_path / "data")

    facts, _errors = load_ground_truth()
    profile_facts = [f for f in facts if f.source == "operator_profile"]
    catalog_facts = [f for f in facts if f.source == "operator_global"]
    assert len(profile_facts) >= 5  # at least the 5 unconditional seeds
    assert len(catalog_facts) >= 1  # the test_invariant fact


def test_id_collision_catalog_wins(tmp_path, monkeypatch):
    """If the operator-global catalog defines a fact with an ID
    that collides with a profile seed (e.g., 'operator_email'),
    the catalog wins. Operator's explicit catalog edit beats the
    derived seed; the conflict surfaces in errors."""
    catalog = tmp_path / "ground_truth.yaml"
    catalog.write_text(yaml.safe_dump({
        "catalog_version": 1,
        "facts": [
            {
                "id": "operator_email",  # collides with profile seed
                "statement": "Operator email is custom@catalog.local.",
                "domain_keywords": ["email"],
                "canonical_terms": ["custom@catalog.local"],
                "forbidden_terms": [],
                "severity": "HIGH",
            },
        ],
    }))
    monkeypatch.setenv(GT_ENV_VAR, str(catalog))
    monkeypatch.chdir(tmp_path)
    _seed_profile(tmp_path / "data")

    facts, errors = load_ground_truth()
    email_facts = [f for f in facts if f.id == "operator_email"]
    assert len(email_facts) == 1
    # Catalog-sourced fact wins, not the profile-derived one.
    assert email_facts[0].source == "operator_global"
    # The collision is noted in errors.
    assert any("collides" in e.lower() for e in errors)
