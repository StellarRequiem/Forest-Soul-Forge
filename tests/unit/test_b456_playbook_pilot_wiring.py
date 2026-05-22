"""B456 (ADR-0066 Phase D) — playbook_pilot wiring smoke.

Mirror of test_b391 (detection_engineer wiring). Confirms the new
role + manifests + skill load through every substrate gate without
runtime errors. The live-side checks (birth + dispatch) live in the
birth script + the Phase D end-to-end smoke; this file covers the
static surface.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]


def test_trait_tree_has_playbook_pilot():
    from forest_soul_forge.core.trait_engine import TraitEngine
    engine = TraitEngine(REPO / "config" / "trait_tree.yaml")
    assert "playbook_pilot" in engine.roles
    role = engine.roles["playbook_pilot"]
    assert role.domain_weights["security"] >= 2.0
    assert role.domain_weights["audit"] >= 2.0   # every step → a chain entry


def test_playbook_pilot_is_in_actuator_genre():
    """ADR-0066 §2 — playbook_pilot is actuator genre (external
    ceiling, same as response_rogue's). The role's discipline is
    enforced via constitution policy, not genre."""
    from forest_soul_forge.core.genre_engine import load_genres
    genres = load_genres(REPO / "config" / "genres.yaml")
    actuator = genres.genres["actuator"]
    assert "playbook_pilot" in actuator.roles
    # And claimed by exactly one genre.
    claims = [
        name for name, g in genres.genres.items()
        if "playbook_pilot" in g.roles
    ]
    assert claims == ["actuator"], f"playbook_pilot claimed by {claims}"


def test_constitution_template_has_playbook_pilot():
    doc = yaml.safe_load(
        (REPO / "config" / "constitution_templates.yaml").read_text(encoding="utf-8")
    )
    assert "playbook_pilot" in doc["role_base"]
    block = doc["role_base"]["playbook_pilot"]
    for required in ("risk_thresholds", "out_of_scope",
                     "operator_duties", "drift_monitoring"):
        assert required in block, (
            f"playbook_pilot template missing block: {required!r}"
        )
    # The four ADR-0066 §2 policies.
    policy_ids = {p["id"] for p in block.get("policies", [])}
    must_have_policies = {
        "forbid_unscheduled_action",       # acts only on a fired detection
        "require_playbook_signature_match",  # no runtime tool substitution
        "forbid_playbook_authorship",      # operator authors playbooks
        "require_cooldown_respect",        # ADR-0066 D4
    }
    assert must_have_policies.issubset(policy_ids), (
        f"missing policies: {must_have_policies - policy_ids}"
    )


def test_tool_catalog_playbook_pilot_archetype():
    doc = yaml.safe_load(
        (REPO / "config" / "tool_catalog.yaml").read_text(encoding="utf-8")
    )
    archetypes = doc.get("archetypes") or {}
    assert "playbook_pilot" in archetypes
    tools = set(archetypes["playbook_pilot"]["standard_tools"])
    must_have = {
        "llm_think.v1",
        "memory_recall.v1",
        "memory_write.v1",
        "audit_chain_verify.v1",
        "text_summarize.v1",
    }
    assert must_have.issubset(tools)
    # forbid_playbook_authorship — no source-editing surface in the kit.
    forbidden = {"shell_exec.v1", "code_edit.v1"} & tools
    assert not forbidden, f"forbidden tools in pilot kit: {forbidden}"


def test_handoffs_playbook_orchestration_routes_to_review_skill():
    doc = yaml.safe_load(
        (REPO / "config" / "handoffs.yaml").read_text(encoding="utf-8")
    )
    mappings = doc.get("default_skill_per_capability") or []
    match = next(
        (m for m in mappings
         if m.get("domain") == "d3_local_soc"
         and m.get("capability") == "playbook_orchestration"),
        None,
    )
    assert match is not None
    assert match["skill_name"] == "playbook_run_review"
    assert match["skill_version"] == "1"


def test_d3_domain_lists_playbook_pilot_entry():
    doc = yaml.safe_load(
        (REPO / "config" / "domains" / "d3_local_soc.yaml").read_text(encoding="utf-8")
    )
    entries = doc.get("entry_agents") or []
    match = next(
        (e for e in entries if e.get("role") == "playbook_pilot"),
        None,
    )
    assert match is not None
    assert match["capability"] == "playbook_orchestration"
    assert "playbook_orchestration" in (doc.get("capabilities") or [])


def test_playbook_run_review_skill_loads():
    skill_path = REPO / "examples" / "skills" / "playbook_run_review.v1.yaml"
    assert skill_path.exists()
    doc = yaml.safe_load(skill_path.read_text(encoding="utf-8"))
    assert doc["name"] == "playbook_run_review"
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
    for step in ("prior_reviews", "verify_chain_integrity",
                 "summarize_runs", "write_review"):
        assert step in step_ids, f"skill missing step: {step}"


def test_static_config_check_passes_with_pilot():
    """Every d3_local_soc entry_agent must resolve in trait_engine."""
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
