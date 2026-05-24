"""ADR-0091 Phase D — D5 Smart Home Brain handoffs.yaml wiring.

Coverage:
  handoffs.yaml structural integrity:
    - file loads cleanly
    - all 8 D5 (domain, capability) mappings are present + point
      at the right skill
    - the 4 ACTIVE D5 cascades are present
    - the 2 INERT cascades stay un-codified (documented in
      comments only, NOT in cascade_rules)
    - pre-existing cascades (d4→d8, d8→d1, d1→d7, d2→d7,
      d9→d2, d10→d1) survive the Phase D edit (regression
      guard — same append-only discipline as ADR-0090 Phase D)

  d5 domain manifest:
    - status flipped to 'live'
    - entry_agents lists the five D5 roles (Phase A+B+C)
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from forest_soul_forge.core.routing_engine import load_handoffs


REPO_ROOT = Path(__file__).resolve().parents[2]
HANDOFFS_PATH = REPO_ROOT / "config" / "handoffs.yaml"
D5_MANIFEST_PATH = REPO_ROOT / "config" / "domains" / "d5_smart_home.yaml"


@pytest.fixture(scope="module")
def handoffs_config():
    cfg, errors = load_handoffs(HANDOFFS_PATH)
    assert errors == [], f"handoffs.yaml load errors: {errors}"
    return cfg


@pytest.fixture(scope="module")
def d5_manifest():
    return yaml.safe_load(D5_MANIFEST_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Capability mappings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("capability,skill_name", [
    ("home_orchestration", "home_orchestration"),
    ("home_security",      "home_security"),
    ("energy_optimization", "energy_optimization"),
    ("comfort_tuning",     "comfort_tuning"),
    ("routine_management", "routine_management"),
    ("vacation_mode",      "vacation_mode"),
    # device_control is an alias for routine_management
    # (the routine envelope is the device-touching surface)
    ("device_control",     "routine_management"),
    # smart_home routes to the umbrella skill
    ("smart_home",         "smart_home"),
])
def test_d5_capability_mapping_present(
    handoffs_config, capability, skill_name,
):
    key = ("d5_smart_home", capability)
    assert key in handoffs_config.default_skill_per_capability, (
        f"missing mapping for {key} — ADR-0091 Phase D didn't land"
    )
    skill = handoffs_config.default_skill_per_capability[key]
    assert skill.skill_name == skill_name
    assert skill.skill_version == "1"


# ---------------------------------------------------------------------------
# Active cascades
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("src_dom,src_cap,tgt_dom,tgt_cap", [
    ("d2_daily_life_os", "morning_briefing",
     "d5_smart_home", "home_orchestration"),
    ("d2_daily_life_os", "task_prioritization",
     "d5_smart_home", "routine_management"),
    ("d5_smart_home", "home_security",
     "d3_local_soc", "incident_response"),
    ("d5_smart_home", "routine_management",
     "d2_daily_life_os", "reminder"),
])
def test_d5_active_cascade_present(
    handoffs_config, src_dom, src_cap, tgt_dom, tgt_cap,
):
    matched = [
        r for r in handoffs_config.cascade_rules
        if r.source_domain == src_dom
        and r.source_capability == src_cap
        and r.target_domain == tgt_dom
        and r.target_capability == tgt_cap
    ]
    assert len(matched) == 1, (
        f"expected exactly one cascade {src_dom}.{src_cap} → "
        f"{tgt_dom}.{tgt_cap}; matched: {len(matched)}"
    )


# ---------------------------------------------------------------------------
# Inert cascades MUST stay un-codified
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("src_dom,src_cap,tgt_dom", [
    # d5→d6 (power_bill_anomaly): D6 not shipped yet
    ("d5_smart_home", "energy_optimization", "d6_finance"),
    # d5→d1 (routines_index): D1 has no routines_index capability
    ("d5_smart_home", "routine_management", "d1_knowledge_forge"),
])
def test_d5_inert_cascades_not_codified(
    handoffs_config, src_dom, src_cap, tgt_dom,
):
    matched = [
        r for r in handoffs_config.cascade_rules
        if r.source_domain == src_dom
        and r.source_capability == src_cap
        and r.target_domain == tgt_dom
    ]
    assert matched == [], (
        f"INERT cascade {src_dom}.{src_cap} → {tgt_dom} was "
        f"accidentally codified; per ADR-0091 Phase D it must "
        f"remain documented-only until prerequisite ships"
    )


# ---------------------------------------------------------------------------
# Regression: pre-existing cascades survive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("src_dom,src_cap,tgt_dom,tgt_cap", [
    # ADR-0067 T4
    ("d4_code_review", "review_signoff",
     "d8_compliance", "compliance_scan"),
    # ADR-0086 Phase D
    ("d8_compliance", "compliance_scan",
     "d1_knowledge_forge", "knowledge_curation"),
    # ADR-0088 Phase D
    ("d1_knowledge_forge", "knowledge_curation",
     "d7_content_studio", "content_drafting"),
    # ADR-0089 Phase D
    ("d9_learning_coach", "spaced_repetition",
     "d2_daily_life_os", "reminder"),
    # ADR-0090 Phase D
    ("d10_research_lab", "research_synthesis",
     "d1_knowledge_forge", "knowledge_curation"),
])
def test_pre_existing_cascade_still_present(
    handoffs_config, src_dom, src_cap, tgt_dom, tgt_cap,
):
    matched = [
        r for r in handoffs_config.cascade_rules
        if r.source_domain == src_dom
        and r.source_capability == src_cap
        and r.target_domain == tgt_dom
        and r.target_capability == tgt_cap
    ]
    assert len(matched) == 1, (
        f"pre-existing cascade {src_dom}.{src_cap} → "
        f"{tgt_dom}.{tgt_cap} was removed during D5 Phase D edit"
    )


# ---------------------------------------------------------------------------
# D5 domain manifest
# ---------------------------------------------------------------------------


def test_d5_manifest_status_is_live(d5_manifest):
    assert d5_manifest["status"] == "live", (
        "ADR-0091 Phase D — d5_smart_home.yaml status must be "
        "'live' after the rollout closes"
    )


@pytest.mark.parametrize("role,capability", [
    ("home_steward",      "home_orchestration"),
    ("home_sentinel",     "home_security"),
    ("energy_warden",     "energy_optimization"),
    ("comfort_optimizer", "comfort_tuning"),
    ("routine_composer",  "routine_management"),
])
def test_d5_manifest_entry_agents_present(d5_manifest, role, capability):
    pairs = [
        (e["role"], e["capability"])
        for e in d5_manifest["entry_agents"]
    ]
    assert (role, capability) in pairs, (
        f"d5_smart_home.yaml entry_agents missing ({role}, {capability})"
    )
