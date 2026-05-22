"""B457 (ADR-0066 Phase D) — purple_pete wiring smoke.

Mirror of test_b456 (playbook_pilot wiring). Confirms the new role
+ manifests + skill load through every substrate gate. The
substrate behaviour (scenario DSL + ScenarioRunner) is covered by
test_b457_purple_team; this file covers the static role surface.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]


def test_trait_tree_has_purple_pete():
    from forest_soul_forge.core.trait_engine import TraitEngine
    engine = TraitEngine(REPO / "config" / "trait_tree.yaml")
    assert "purple_pete" in engine.roles
    role = engine.roles["purple_pete"]
    assert role.domain_weights["security"] >= 2.0
    assert role.domain_weights["cognitive"] >= 2.0  # scenario design


def test_purple_pete_is_in_researcher_genre():
    """ADR-0066 §3 — purple_pete is researcher genre. It has no real
    action surface (writes only the simulation store), so researcher
    not actuator — the same correction B386 made for
    threat_intel_curator. ADR-0078's table said actuator; ADR-0066
    §3 is the controlling, more specific Phase D spec."""
    from forest_soul_forge.core.genre_engine import load_genres
    genres = load_genres(REPO / "config" / "genres.yaml")
    researcher = genres.genres["researcher"]
    assert "purple_pete" in researcher.roles
    claims = [
        name for name, g in genres.genres.items()
        if "purple_pete" in g.roles
    ]
    assert claims == ["researcher"], f"purple_pete claimed by {claims}"


def test_constitution_template_has_purple_pete():
    doc = yaml.safe_load(
        (REPO / "config" / "constitution_templates.yaml").read_text(encoding="utf-8")
    )
    assert "purple_pete" in doc["role_base"]
    block = doc["role_base"]["purple_pete"]
    for required in ("risk_thresholds", "out_of_scope",
                     "operator_duties", "drift_monitoring"):
        assert required in block, (
            f"purple_pete template missing block: {required!r}"
        )
    # The three ADR-0066 §3 policies.
    policy_ids = {p["id"] for p in block.get("policies", [])}
    must_have_policies = {
        "forbid_production_telemetry_emit",  # sim store only
        "forbid_real_response_dispatch",     # no real response
        "require_scenario_provenance",       # synthetic always marked
    }
    assert must_have_policies.issubset(policy_ids), (
        f"missing policies: {must_have_policies - policy_ids}"
    )


def test_tool_catalog_purple_pete_archetype():
    doc = yaml.safe_load(
        (REPO / "config" / "tool_catalog.yaml").read_text(encoding="utf-8")
    )
    archetypes = doc.get("archetypes") or {}
    assert "purple_pete" in archetypes
    tools = set(archetypes["purple_pete"]["standard_tools"])
    must_have = {
        "llm_think.v1",
        "memory_recall.v1",
        "memory_write.v1",
        "audit_chain_verify.v1",
        "text_summarize.v1",
    }
    assert must_have.issubset(tools)
    # No real action surface in the kit.
    forbidden = {"shell_exec.v1", "code_edit.v1", "isolate_process.v1"} & tools
    assert not forbidden, f"forbidden tools in purple_pete kit: {forbidden}"


def test_handoffs_adversary_emulation_routes_to_brief_skill():
    doc = yaml.safe_load(
        (REPO / "config" / "handoffs.yaml").read_text(encoding="utf-8")
    )
    mappings = doc.get("default_skill_per_capability") or []
    match = next(
        (m for m in mappings
         if m.get("domain") == "d3_local_soc"
         and m.get("capability") == "adversary_emulation"),
        None,
    )
    assert match is not None
    assert match["skill_name"] == "purple_team_brief"
    assert match["skill_version"] == "1"


def test_d3_domain_lists_purple_pete_entry():
    doc = yaml.safe_load(
        (REPO / "config" / "domains" / "d3_local_soc.yaml").read_text(encoding="utf-8")
    )
    entries = doc.get("entry_agents") or []
    match = next(
        (e for e in entries if e.get("role") == "purple_pete"),
        None,
    )
    assert match is not None
    assert match["capability"] == "adversary_emulation"
    assert "adversary_emulation" in (doc.get("capabilities") or [])


def test_purple_team_brief_skill_loads():
    skill_path = REPO / "examples" / "skills" / "purple_team_brief.v1.yaml"
    assert skill_path.exists()
    doc = yaml.safe_load(skill_path.read_text(encoding="utf-8"))
    assert doc["name"] == "purple_team_brief"
    assert doc["version"] == "1"
    required = set(doc["requires"])
    must_have = {
        "llm_think.v1",
        "memory_recall.v1",
        "memory_write.v1",
        "audit_chain_verify.v1",
        "text_summarize.v1",
    }
    assert must_have.issubset(required)
    step_ids = {s["id"] for s in doc["steps"]}
    for step in ("prior_briefs", "verify_chain_integrity",
                 "summarize_coverage", "write_brief"):
        assert step in step_ids, f"skill missing step: {step}"


def test_d3_all_entry_agents_resolve():
    """Every d3_local_soc entry_agent must resolve in trait_engine —
    the 11 entry roles after Phase D (9 baseline blue team minus
    net_ninja/zero_zero which are registry-only, plus the 6 advanced
    roles)."""
    from forest_soul_forge.core.trait_engine import TraitEngine
    engine = TraitEngine(REPO / "config" / "trait_tree.yaml")
    d3 = yaml.safe_load(
        (REPO / "config" / "domains" / "d3_local_soc.yaml").read_text(encoding="utf-8")
    )
    roles = [ea.get("role") for ea in d3.get("entry_agents") or []]
    for role in roles:
        assert role in engine.roles, (
            f"d3_local_soc entry_agent {role!r} not in trait_engine"
        )
    # Phase D adds both new roles to the manifest.
    assert "playbook_pilot" in roles
    assert "purple_pete" in roles
