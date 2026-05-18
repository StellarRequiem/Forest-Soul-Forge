"""B385 (ADR-0064 T6) — threat_intel_curator wiring smoke.

Confirms the new role + manifests + skill load through every
substrate gate without runtime errors. Mirror of B379's test
shape for telemetry_steward; the two roles are siblings.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]


def test_trait_tree_has_threat_intel_curator():
    from forest_soul_forge.core.trait_engine import TraitEngine
    engine = TraitEngine(REPO / "config" / "trait_tree.yaml")
    assert "threat_intel_curator" in engine.roles
    role = engine.roles["threat_intel_curator"]
    assert role.domain_weights["security"] >= 2.0
    assert role.domain_weights["audit"] >= 2.0


def test_threat_intel_curator_is_in_researcher_genre():
    """B386 — moved from guardian to researcher because kit needs
    web_fetch (network), which exceeds guardian's read_only
    ceiling. Researcher is the natural fit: literature-scan +
    data-synthesis with allowlisted network reach. Advisory
    stance enforced via constitution policies, not genre."""
    from forest_soul_forge.core.genre_engine import load_genres
    genres = load_genres(REPO / "config" / "genres.yaml")
    researcher = genres.genres["researcher"]
    assert "threat_intel_curator" in researcher.roles
    # And NOT in guardian anymore.
    guardian = genres.genres["guardian"]
    assert "threat_intel_curator" not in guardian.roles


def test_constitution_template_has_threat_intel_curator():
    doc = yaml.safe_load(
        (REPO / "config" / "constitution_templates.yaml").read_text(encoding="utf-8")
    )
    assert "role_base" in doc
    assert "threat_intel_curator" in doc["role_base"]
    block = doc["role_base"]["threat_intel_curator"]
    for required in ("risk_thresholds", "out_of_scope",
                     "operator_duties", "drift_monitoring"):
        assert required in block, (
            f"threat_intel_curator template missing block: {required!r}"
        )


def test_tool_catalog_threat_intel_curator_archetype():
    doc = yaml.safe_load(
        (REPO / "config" / "tool_catalog.yaml").read_text(encoding="utf-8")
    )
    archetypes = doc.get("archetypes") or {}
    assert "threat_intel_curator" in archetypes
    tools = archetypes["threat_intel_curator"]["standard_tools"]
    must_have = {
        "audit_chain_verify.v1",
        "memory_recall.v1",
        "memory_write.v1",
        "llm_think.v1",
        "web_fetch.v1",  # external feed pull
    }
    assert must_have.issubset(set(tools)), (
        f"threat_intel_curator kit missing required: {must_have - set(tools)}"
    )
    # Must NOT include action/state-mutating tools.
    must_not = {"shell_exec.v1", "code_edit.v1", "browser_action.v1"}
    forbidden = must_not & set(tools)
    assert not forbidden, f"forbidden tools in curator kit: {forbidden}"


def test_handoffs_threat_intel_curation_routes_to_refresh_skill():
    doc = yaml.safe_load(
        (REPO / "config" / "handoffs.yaml").read_text(encoding="utf-8")
    )
    mappings = doc.get("default_skill_per_capability") or []
    match = next(
        (m for m in mappings
         if m.get("domain") == "d3_local_soc"
         and m.get("capability") == "threat_intel_curation"),
        None,
    )
    assert match is not None
    assert match["skill_name"] == "threat_intel_refresh"
    assert match["skill_version"] == "1"


def test_d3_domain_lists_threat_intel_curator_entry():
    doc = yaml.safe_load(
        (REPO / "config" / "domains" / "d3_local_soc.yaml").read_text(encoding="utf-8")
    )
    entries = doc.get("entry_agents") or []
    match = next(
        (e for e in entries if e.get("role") == "threat_intel_curator"),
        None,
    )
    assert match is not None
    assert match["capability"] == "threat_intel_curation"
    assert "threat_intel_curation" in (doc.get("capabilities") or [])


def test_threat_intel_refresh_skill_loads():
    skill_path = REPO / "examples" / "skills" / "threat_intel_refresh.v1.yaml"
    assert skill_path.exists()
    doc = yaml.safe_load(skill_path.read_text(encoding="utf-8"))
    assert doc["name"] == "threat_intel_refresh"
    assert doc["version"] == "1"
    required = set(doc["requires"])
    must_have = {
        "web_fetch.v1",
        "memory_write.v1",
        "memory_recall.v1",
        "audit_chain_verify.v1",
    }
    assert must_have.issubset(required), (
        f"skill missing required tools: {must_have - required}"
    )
    step_ids = {s["id"] for s in doc["steps"]}
    assert "verify_chain_integrity" in step_ids
    assert "fetch_feed" in step_ids
    assert "write_intel" in step_ids


def test_static_config_check_passes_with_curator():
    """section-01-shape: every d3_local_soc entry_agent must
    resolve in trait_engine."""
    from forest_soul_forge.core.trait_engine import TraitEngine
    engine = TraitEngine(REPO / "config" / "trait_tree.yaml")
    d3 = yaml.safe_load(
        (REPO / "config" / "domains" / "d3_local_soc.yaml").read_text(encoding="utf-8")
    )
    for ea in d3.get("entry_agents") or []:
        role = ea.get("role")
        assert role in engine.roles, (
            f"d3_local_soc entry_agent {role!r} not in trait_engine"
        )
