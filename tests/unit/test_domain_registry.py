"""ADR-0067 T1 (B279) — domain registry loader tests.

Covers:
  - happy-path load of all 10 seed manifests from config/domains/
  - missing directory → hard fail
  - empty directory → soft warning
  - duplicate domain_id across manifests → first kept, error logged
  - dangling handoff_target → soft error
  - invalid status → manifest dropped, error logged
  - registry lookup helpers (by_id, by_capability, dispatchable_ids)
  - Domain.is_dispatchable property
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from forest_soul_forge.core.domain_registry import (
    Domain,
    DomainRegistry,
    DomainRegistryError,
    EntryAgent,
    load_domain_registry,
)


def _write_manifest(dir_: Path, name: str, body: dict) -> Path:
    p = dir_ / f"{name}.yaml"
    p.write_text(yaml.safe_dump(body))
    return p


def _good_manifest_body(
    domain_id: str = "d_test",
    status: str = "live",
    handoff_targets: list[str] | None = None,
) -> dict:
    return {
        "domain_id": domain_id,
        "name": "Test Domain",
        "status": status,
        "description": "A test domain.",
        "entry_agents": [
            {"role": "test_role", "capability": "test_cap"},
        ],
        "capabilities": ["test_cap", "extra_cap"],
        "example_intents": ["do the test thing"],
        "handoff_targets": handoff_targets or [],
    }


# ---------------------------------------------------------------------------
# Hard failures
# ---------------------------------------------------------------------------
def test_load_missing_directory_raises(tmp_path):
    with pytest.raises(DomainRegistryError, match="not found"):
        load_domain_registry(tmp_path / "does-not-exist")


def test_load_path_is_file_not_dir_raises(tmp_path):
    f = tmp_path / "a-file.yaml"
    f.write_text("not a directory")
    with pytest.raises(DomainRegistryError, match="not a directory"):
        load_domain_registry(f)


# ---------------------------------------------------------------------------
# Soft warnings
# ---------------------------------------------------------------------------
def test_empty_directory_is_soft_warning(tmp_path):
    registry, errors = load_domain_registry(tmp_path)
    assert registry.domains == ()
    assert any("no domain manifests" in e for e in errors)


def test_duplicate_domain_id_first_kept(tmp_path):
    _write_manifest(tmp_path, "a", _good_manifest_body("d_x"))
    _write_manifest(tmp_path, "b", _good_manifest_body("d_x"))
    registry, errors = load_domain_registry(tmp_path)
    assert len(registry.domains) == 1
    assert any("duplicate domain_id" in e for e in errors)


def test_dangling_handoff_target_is_soft_error(tmp_path):
    _write_manifest(tmp_path, "a", _good_manifest_body(
        "d_x", handoff_targets=["d_nope"],
    ))
    registry, errors = load_domain_registry(tmp_path)
    assert len(registry.domains) == 1
    assert any("handoff_target" in e and "d_nope" in e for e in errors)


def test_invalid_status_drops_manifest(tmp_path):
    body = _good_manifest_body("d_bad")
    body["status"] = "experimental"  # not in VALID_STATUSES
    _write_manifest(tmp_path, "bad", body)
    registry, errors = load_domain_registry(tmp_path)
    assert registry.domains == ()
    assert any("'experimental'" in e for e in errors)


def test_malformed_yaml_drops_manifest(tmp_path):
    bad = tmp_path / "malformed.yaml"
    bad.write_text("not: valid: yaml: :::")
    registry, errors = load_domain_registry(tmp_path)
    assert registry.domains == ()
    assert any("malformed YAML" in e for e in errors)


def test_missing_required_field_drops_manifest(tmp_path):
    body = _good_manifest_body("d_x")
    del body["entry_agents"]
    _write_manifest(tmp_path, "missing", body)
    registry, errors = load_domain_registry(tmp_path)
    assert registry.domains == ()
    assert any("missing required fields" in e for e in errors)


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------
def test_by_id_and_dispatchable_ids(tmp_path):
    _write_manifest(tmp_path, "live", _good_manifest_body("d_live", "live"))
    _write_manifest(tmp_path, "planned", _good_manifest_body("d_planned", "planned"))
    _write_manifest(tmp_path, "partial", _good_manifest_body("d_partial", "partial"))
    registry, _errors = load_domain_registry(tmp_path)

    assert registry.by_id("d_live").status == "live"
    assert registry.by_id("d_planned").status == "planned"
    assert registry.by_id("d_nonexistent") is None

    dispatchable = set(registry.dispatchable_ids())
    assert "d_live" in dispatchable
    assert "d_partial" in dispatchable
    assert "d_planned" not in dispatchable


def test_by_capability(tmp_path):
    body_a = _good_manifest_body("d_a")
    body_a["capabilities"] = ["alpha", "beta"]
    body_b = _good_manifest_body("d_b")
    body_b["capabilities"] = ["beta", "gamma"]
    _write_manifest(tmp_path, "a", body_a)
    _write_manifest(tmp_path, "b", body_b)
    registry, _errors = load_domain_registry(tmp_path)

    alpha_domains = [d.domain_id for d in registry.by_capability("alpha")]
    beta_domains = [d.domain_id for d in registry.by_capability("beta")]
    gamma_domains = [d.domain_id for d in registry.by_capability("gamma")]
    none_domains = [d.domain_id for d in registry.by_capability("nope")]

    assert alpha_domains == ["d_a"]
    assert set(beta_domains) == {"d_a", "d_b"}
    assert gamma_domains == ["d_b"]
    assert none_domains == []


def test_is_dispatchable_property():
    d_live = Domain(
        domain_id="d", name="t", status="live", description="",
        entry_agents=(), capabilities=(), example_intents=(),
    )
    d_partial = Domain(
        domain_id="d", name="t", status="partial", description="",
        entry_agents=(), capabilities=(), example_intents=(),
    )
    d_planned = Domain(
        domain_id="d", name="t", status="planned", description="",
        entry_agents=(), capabilities=(), example_intents=(),
    )
    assert d_live.is_dispatchable
    assert d_partial.is_dispatchable
    assert not d_planned.is_dispatchable


# ---------------------------------------------------------------------------
# Real seed manifests load cleanly
# ---------------------------------------------------------------------------
def test_seed_manifests_load_without_errors():
    """All 10 ADR-0067 T1 seed manifests must load with zero errors.

    If a seed manifest develops a typo / dangling reference, this
    test catches it at PR time. The manifests live in config/domains/
    relative to repo root; the test runs from there.
    """
    repo_root = Path(__file__).resolve().parents[2]
    domains_dir = repo_root / "config" / "domains"
    if not domains_dir.exists():
        pytest.skip("seed manifests not present (running from extract?)")
    registry, errors = load_domain_registry(domains_dir)
    assert len(registry.domains) == 10, (
        f"expected 10 seed domains, got {len(registry.domains)}: "
        f"{[d.domain_id for d in registry.domains]}"
    )
    assert errors == [], f"seed manifests have config errors: {errors}"
    expected_ids = {
        "d1_knowledge_forge", "d2_daily_life_os", "d3_local_soc",
        "d4_code_review", "d5_smart_home", "d6_finance",
        "d7_content_studio", "d8_compliance",
        "d9_learning_coach", "d10_research_lab",
    }
    assert set(registry.domain_ids()) == expected_ids
