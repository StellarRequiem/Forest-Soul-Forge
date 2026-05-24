"""ADR-0092 Phase D — D6 Personal Finance Guardian handoffs.yaml wiring.

Coverage:
  handoffs.yaml structural integrity:
    - file loads cleanly
    - all D6 (domain, capability) mappings are present + point
      at the right skill
    - the 3 ACTIVE D6-touching cascades are present
    - the 3 INERT cascades stay un-codified (documented in
      comments only, NOT in cascade_rules)
    - pre-existing cascades (d4→d8, d8→d1, d1→d7, d2→d7,
      d9→d2, d10→d1, d2→d5, d5→d3) survive the Phase D edit
      (regression guard — same append-only discipline as
      ADR-0091 Phase D)

  d6 domain manifest:
    - status flipped to 'live'
    - entry_agents lists the five D6 roles (Phase A+B+C)
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from forest_soul_forge.core.routing_engine import load_handoffs


REPO_ROOT = Path(__file__).resolve().parents[2]
HANDOFFS_PATH = REPO_ROOT / "config" / "handoffs.yaml"
D6_MANIFEST_PATH = REPO_ROOT / "config" / "domains" / "d6_finance.yaml"


@pytest.fixture(scope="module")
def handoffs_config():
    cfg, errors = load_handoffs(HANDOFFS_PATH)
    assert errors == [], f"handoffs.yaml load errors: {errors}"
    return cfg


@pytest.fixture(scope="module")
def d6_manifest():
    return yaml.safe_load(D6_MANIFEST_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Capability mappings — 9 entries (5 primary + 4 aliases)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("capability,skill_name", [
    ("budget_analysis",        "budget_analysis"),
    ("risk_analysis",          "risk_analysis"),
    ("transaction_monitoring", "transaction_monitoring"),
    ("bill_management",        "bill_management"),
    ("investment_research",    "investment_research"),
    # ADR-0092 Decision 5 — aliases.
    # burn_rate_forecast + tax_season_summary alias to budget_analysis
    ("burn_rate_forecast",     "budget_analysis"),
    ("tax_season_summary",     "budget_analysis"),
    # receipt_ocr aliases to transaction_monitoring (forest-finance
    # connector ingests OCR'd receipts as transactions)
    ("receipt_ocr",            "transaction_monitoring"),
    # finance_brain routes to the umbrella skill
    ("finance_brain",          "finance_brain"),
])
def test_d6_capability_mapping_present(
    handoffs_config, capability, skill_name,
):
    key = ("d6_finance", capability)
    assert key in handoffs_config.default_skill_per_capability, (
        f"missing mapping for {key} — ADR-0092 Phase D didn't land"
    )
    skill = handoffs_config.default_skill_per_capability[key]
    assert skill.skill_name == skill_name
    assert skill.skill_version == "1"


# ---------------------------------------------------------------------------
# Active cascades — 3 rails touch D6
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("src_dom,src_cap,tgt_dom,tgt_cap", [
    # ADR-0091 Phase D declared INERT — ADR-0092 Phase D activates.
    ("d5_smart_home", "energy_optimization",
     "d6_finance", "transaction_monitoring"),
    # ADR-0092 Phase D new rail — bill-due attestation seeds D2 reminder.
    ("d6_finance", "bill_management",
     "d2_daily_life_os", "reminder"),
    # ADR-0092 Phase D new rail — tax-season summary seeds D8 compliance.
    ("d6_finance", "tax_season_summary",
     "d8_compliance", "compliance_scan"),
])
def test_d6_active_cascade_present(
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
    # d2→d6 (bill_reminder direction is reversed — d6→d2 IS active)
    ("d2_daily_life_os", "reminder", "d6_finance"),
    # d6→d1 (no "you asked about X" surface on d1)
    ("d6_finance", "transaction_monitoring", "d1_knowledge_forge"),
    # d6→d3 (recursive with d5→d6 — d3 already sees the upstream signal)
    ("d6_finance", "transaction_monitoring", "d3_local_soc"),
])
def test_d6_inert_cascades_not_codified(
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
        f"accidentally codified; per ADR-0092 Phase D it must "
        f"remain documented-only until the prerequisite shape "
        f"changes"
    )


# ---------------------------------------------------------------------------
# Regression: pre-existing cascades survive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("src_dom,src_cap,tgt_dom,tgt_cap", [
    # ADR-0067 T4 — d4 → d8 PR-to-compliance rail
    ("d4_code_review", "review_signoff",
     "d8_compliance", "compliance_scan"),
    # ADR-0086 Phase D — d8 → d1
    ("d8_compliance", "compliance_scan",
     "d1_knowledge_forge", "knowledge_curation"),
    # ADR-0088 Phase D — d1 → d7
    ("d1_knowledge_forge", "knowledge_curation",
     "d7_content_studio", "content_drafting"),
    # ADR-0089 Phase D — d9 → d2 spaced repetition
    ("d9_learning_coach", "spaced_repetition",
     "d2_daily_life_os", "reminder"),
    # ADR-0090 Phase D — d10 → d1
    ("d10_research_lab", "research_synthesis",
     "d1_knowledge_forge", "knowledge_curation"),
    # ADR-0091 Phase D — d2 → d5 morning briefing
    ("d2_daily_life_os", "morning_briefing",
     "d5_smart_home", "home_orchestration"),
    # ADR-0091 Phase D — d5 → d3 home security
    ("d5_smart_home", "home_security",
     "d3_local_soc", "incident_response"),
    # ADR-0091 Phase D — d5 → d2 routine reminder
    ("d5_smart_home", "routine_management",
     "d2_daily_life_os", "reminder"),
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
        f"{tgt_dom}.{tgt_cap} was removed during D6 Phase D edit"
    )


# ---------------------------------------------------------------------------
# D6 domain manifest
# ---------------------------------------------------------------------------


def test_d6_manifest_status_is_live(d6_manifest):
    assert d6_manifest["status"] == "live", (
        "ADR-0092 Phase D — d6_finance.yaml status must be "
        "'live' after the rollout closes"
    )


@pytest.mark.parametrize("role,capability", [
    ("budget_analyst",        "budget_analysis"),
    ("transaction_tracker",   "transaction_monitoring"),
    ("investment_researcher", "investment_research"),
    ("risk_advisor",          "risk_analysis"),
    ("bill_steward",          "bill_management"),
])
def test_d6_manifest_entry_agents_present(d6_manifest, role, capability):
    pairs = [
        (e["role"], e["capability"])
        for e in d6_manifest["entry_agents"]
    ]
    assert (role, capability) in pairs, (
        f"d6_finance.yaml entry_agents missing ({role}, {capability})"
    )
