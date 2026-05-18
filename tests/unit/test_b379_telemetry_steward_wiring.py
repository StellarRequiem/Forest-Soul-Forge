"""B379 (ADR-0064 T4) — telemetry_steward wiring smoke.

Confirms the new role + manifests + skill load through every
substrate gate without runtime errors. Doesn't exercise the live
daemon (that's `dev-tools/birth-telemetry-steward.command`'s job);
here we verify the YAML/code surfaces compose correctly.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]


# ---- trait_tree -----------------------------------------------------------

def test_trait_tree_has_telemetry_steward():
    from forest_soul_forge.core.trait_engine import TraitEngine
    engine = TraitEngine(REPO / "config" / "trait_tree.yaml")
    assert "telemetry_steward" in engine.roles, (
        "trait_tree.yaml must define telemetry_steward role"
    )
    role = engine.roles["telemetry_steward"]
    # audit is the load-bearing trait per the role's purpose.
    assert role.domain_weights["audit"] >= 2.0, (
        f"telemetry_steward should weight audit highly; got "
        f"{role.domain_weights['audit']}"
    )


# ---- genre membership -----------------------------------------------------

def test_telemetry_steward_is_in_guardian_genre():
    from forest_soul_forge.core.genre_engine import load_genres
    genres = load_genres(REPO / "config" / "genres.yaml")
    guardian = genres.genres["guardian"]
    assert "telemetry_steward" in guardian.roles, (
        "telemetry_steward must be claimed by the guardian genre"
    )


# ---- constitution template ------------------------------------------------

def test_constitution_template_has_telemetry_steward():
    text = (REPO / "config" / "constitution_templates.yaml").read_text(
        encoding="utf-8"
    )
    doc = yaml.safe_load(text)
    assert "role_base" in doc
    assert "telemetry_steward" in doc["role_base"]
    block = doc["role_base"]["telemetry_steward"]
    # The four common blocks must all be present (section-01
    # static-config check enforces this).
    for required in ("risk_thresholds", "out_of_scope",
                     "operator_duties", "drift_monitoring"):
        assert required in block, (
            f"telemetry_steward template missing block: {required!r}"
        )


# ---- tool kit (catalog) ---------------------------------------------------

def test_tool_catalog_telemetry_steward_archetype():
    text = (REPO / "config" / "tool_catalog.yaml").read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    archetypes = doc.get("archetypes") or {}
    assert "telemetry_steward" in archetypes, (
        "tool_catalog.yaml archetypes must include telemetry_steward"
    )
    tools = archetypes["telemetry_steward"]["standard_tools"]
    # The kit must include the load-bearing tools per the
    # constitution policy require_batch_anchor_verification.
    must_have = {
        "audit_chain_verify.v1",   # chain integrity gate
        "memory_recall.v1",        # prior briefs
        "memory_write.v1",         # write the brief
        "llm_think.v1",            # summarize
    }
    assert must_have.issubset(set(tools)), (
        f"telemetry_steward kit missing required tools: "
        f"{must_have - set(tools)}"
    )
    # Must NOT include any external/filesystem tool — the role's
    # constitution forbids action; the kit must match.
    must_not = {"shell_exec.v1", "code_edit.v1", "browser_action.v1"}
    forbidden_present = must_not & set(tools)
    assert not forbidden_present, (
        f"telemetry_steward kit must be read-only; found forbidden: "
        f"{forbidden_present}"
    )


# ---- handoff routing ------------------------------------------------------

def test_handoffs_telemetry_oversight_routes_to_brief_skill():
    text = (REPO / "config" / "handoffs.yaml").read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    mappings = doc.get("default_skill_per_capability") or []
    match = next(
        (m for m in mappings
         if m.get("domain") == "d3_local_soc"
         and m.get("capability") == "telemetry_oversight"),
        None,
    )
    assert match is not None, (
        "handoffs.yaml must map (d3_local_soc, telemetry_oversight) "
        "to a skill"
    )
    assert match["skill_name"] == "telemetry_steward_brief"
    assert match["skill_version"] == "1"


# ---- domain manifest ------------------------------------------------------

def test_d3_domain_lists_telemetry_steward_entry():
    text = (REPO / "config" / "domains" / "d3_local_soc.yaml").read_text(
        encoding="utf-8"
    )
    doc = yaml.safe_load(text)
    entries = doc.get("entry_agents") or []
    match = next(
        (e for e in entries if e.get("role") == "telemetry_steward"),
        None,
    )
    assert match is not None, (
        "d3_local_soc.yaml entry_agents must include telemetry_steward"
    )
    assert match["capability"] == "telemetry_oversight"
    assert "telemetry_oversight" in (doc.get("capabilities") or [])


# ---- skill manifest -------------------------------------------------------

def test_telemetry_steward_brief_skill_loads():
    """The skill YAML must parse, declare the right tools in
    requires, and reference inputs the prompt template uses."""
    skill_path = REPO / "examples" / "skills" / "telemetry_steward_brief.v1.yaml"
    assert skill_path.exists(), "telemetry_steward_brief.v1.yaml not found"
    doc = yaml.safe_load(skill_path.read_text(encoding="utf-8"))
    assert doc["name"] == "telemetry_steward_brief"
    assert doc["version"] == "1"
    required_tools = set(doc["requires"])
    must_have = {
        "audit_chain_verify.v1",
        "memory_recall.v1",
        "memory_write.v1",
        "llm_think.v1",
    }
    assert must_have.issubset(required_tools), (
        f"skill requires missing: {must_have - required_tools}"
    )
    # Inputs schema sanity.
    inputs = doc["inputs"]
    assert "recent_batches" in inputs["required"]
    # Steps must include verify_chain_integrity (constitution
    # policy require_batch_anchor_verification needs it before any
    # summarization step lands).
    step_ids = {s["id"] for s in doc["steps"]}
    assert "verify_chain_integrity" in step_ids


# ---- section-01 static-config check passes against the new role ---------

def test_static_config_check_passes_with_new_role():
    """Run section-01's domain-manifest check shape against the
    config files; the addition of telemetry_steward should not
    break the 'every entry_agent references a real role' rule."""
    from forest_soul_forge.core.trait_engine import TraitEngine
    engine = TraitEngine(REPO / "config" / "trait_tree.yaml")
    d3 = yaml.safe_load(
        (REPO / "config" / "domains" / "d3_local_soc.yaml").read_text(
            encoding="utf-8"
        )
    )
    # d3 is status: partial — so the strict check runs against
    # its entry_agents.
    assert (d3.get("status") or "").lower() != "planned"
    for ea in d3.get("entry_agents") or []:
        role = ea.get("role")
        assert role in engine.roles, (
            f"d3_local_soc.yaml entry_agent {role!r} must exist in trait_engine"
        )
