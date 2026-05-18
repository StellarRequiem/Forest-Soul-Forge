"""B391 (ADR-0065 T3) — detection_engineer wiring smoke.

Mirror of test_b385 + test_b379. Confirms the new role + manifests
+ skill load through every substrate gate without runtime errors.
The live-side checks (birth + dispatch) live in the birth script
+ a future live-test driver; this file covers the static surface.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]


def test_trait_tree_has_detection_engineer():
    from forest_soul_forge.core.trait_engine import TraitEngine
    engine = TraitEngine(REPO / "config" / "trait_tree.yaml")
    assert "detection_engineer" in engine.roles
    role = engine.roles["detection_engineer"]
    assert role.domain_weights["security"] >= 2.0
    assert role.domain_weights["cognitive"] >= 2.0  # synthesis is cognitive-heavy


def test_detection_engineer_is_in_researcher_genre():
    """Per ADR-0065 T3 + B341 pattern: kit needs web_fetch
    (network) for ATT&CK reference pulls, so researcher genre
    (network ceiling) is the right home. Advisory stance is
    enforced via constitution policies, not genre."""
    from forest_soul_forge.core.genre_engine import load_genres
    genres = load_genres(REPO / "config" / "genres.yaml")
    researcher = genres.genres["researcher"]
    assert "detection_engineer" in researcher.roles
    # And NOT in guardian (would fail kit-tier check at birth).
    guardian = genres.genres["guardian"]
    assert "detection_engineer" not in guardian.roles


def test_constitution_template_has_detection_engineer():
    doc = yaml.safe_load(
        (REPO / "config" / "constitution_templates.yaml").read_text(encoding="utf-8")
    )
    assert "role_base" in doc
    assert "detection_engineer" in doc["role_base"]
    block = doc["role_base"]["detection_engineer"]
    for required in ("risk_thresholds", "out_of_scope",
                     "operator_duties", "drift_monitoring"):
        assert required in block, (
            f"detection_engineer template missing block: {required!r}"
        )
    # Check load-bearing policies exist.
    policy_ids = {p["id"] for p in block.get("policies", [])}
    must_have_policies = {
        "forbid_direct_rule_install",   # operator commits rules, not agent
        "forbid_engine_invocation",     # substrate runs rules
        "forbid_response_action",       # lane discipline
        "require_attack_tag_in_proposals",  # ADR-0065 D3
        "require_evidence_in_proposal",     # no speculative rules
    }
    assert must_have_policies.issubset(policy_ids), (
        f"missing policies: {must_have_policies - policy_ids}"
    )


def test_tool_catalog_detection_engineer_archetype():
    doc = yaml.safe_load(
        (REPO / "config" / "tool_catalog.yaml").read_text(encoding="utf-8")
    )
    archetypes = doc.get("archetypes") or {}
    assert "detection_engineer" in archetypes
    tools = archetypes["detection_engineer"]["standard_tools"]
    must_have = {
        "llm_think.v1",
        "memory_recall.v1",
        "memory_write.v1",
        "audit_chain_verify.v1",
        "web_fetch.v1",  # ATT&CK reference reads
    }
    assert must_have.issubset(set(tools))
    # Must NOT include action surface.
    must_not = {"shell_exec.v1", "code_edit.v1", "browser_action.v1",
                "isolate_process.v1"}
    forbidden = must_not & set(tools)
    assert not forbidden, f"forbidden tools in engineer kit: {forbidden}"


def test_handoffs_detection_authoring_routes_to_propose_skill():
    doc = yaml.safe_load(
        (REPO / "config" / "handoffs.yaml").read_text(encoding="utf-8")
    )
    mappings = doc.get("default_skill_per_capability") or []
    match = next(
        (m for m in mappings
         if m.get("domain") == "d3_local_soc"
         and m.get("capability") == "detection_authoring"),
        None,
    )
    assert match is not None
    assert match["skill_name"] == "propose_detection"
    assert match["skill_version"] == "1"


def test_d3_domain_lists_detection_engineer_entry():
    doc = yaml.safe_load(
        (REPO / "config" / "domains" / "d3_local_soc.yaml").read_text(encoding="utf-8")
    )
    entries = doc.get("entry_agents") or []
    match = next(
        (e for e in entries if e.get("role") == "detection_engineer"),
        None,
    )
    assert match is not None
    assert match["capability"] == "detection_authoring"
    assert "detection_authoring" in (doc.get("capabilities") or [])


def test_propose_detection_skill_loads():
    skill_path = REPO / "examples" / "skills" / "propose_detection.v1.yaml"
    assert skill_path.exists()
    doc = yaml.safe_load(skill_path.read_text(encoding="utf-8"))
    assert doc["name"] == "propose_detection"
    assert doc["version"] == "1"
    required = set(doc["requires"])
    must_have = {
        "llm_think.v1",
        "memory_recall.v1",
        "memory_write.v1",
        "audit_chain_verify.v1",
        "web_fetch.v1",
    }
    assert must_have.issubset(required)
    step_ids = {s["id"] for s in doc["steps"]}
    # Load-bearing steps from ADR-0065's propose_detection design.
    for step in ("verify_chain_integrity", "attack_reference",
                 "synthesize_rule", "record_proposal"):
        assert step in step_ids, f"skill missing step: {step}"


def test_static_config_check_passes_with_engineer():
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
