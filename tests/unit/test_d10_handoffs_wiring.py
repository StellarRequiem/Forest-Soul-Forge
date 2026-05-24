"""ADR-0090 Phase D — D10 Multi-Agent Research Lab handoffs.yaml wiring.

Coverage:
  handoffs.yaml structural integrity:
    - file loads cleanly
    - all 9 D10 (domain, capability) mappings are present + point
      at the right skill
    - the 4 ACTIVE D10 cascades are present
    - the 3 INERT cascades stay un-codified (documented in
      comments only, NOT in cascade_rules)
    - pre-existing cascades (d4→d8, d8→d1, d1→d7, d2→d7, d9→d2)
      survive the Phase D edit (regression guard — same
      append-only discipline as ADR-0077 T3 + ADR-0089 Phase D)

  d10 domain manifest:
    - status flipped to 'live'
    - entry_agents lists the five D10 roles + experimenter
      reference per ADR-0090 Decision 4
    - the manifest names lab_synthesizer (NOT bare synthesizer)
      per ADR-0090 Decision 2
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from forest_soul_forge.core.routing_engine import load_handoffs


REPO_ROOT = Path(__file__).resolve().parents[2]
HANDOFFS_PATH = REPO_ROOT / "config" / "handoffs.yaml"
D10_MANIFEST_PATH = REPO_ROOT / "config" / "domains" / "d10_research_lab.yaml"


@pytest.fixture(scope="module")
def handoffs_config():
    cfg, errors = load_handoffs(HANDOFFS_PATH)
    assert errors == [], f"handoffs.yaml load errors: {errors}"
    return cfg


@pytest.fixture(scope="module")
def d10_manifest():
    return yaml.safe_load(D10_MANIFEST_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Capability mappings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("capability,skill_name", [
    ("source_gathering",     "source_gathering"),
    ("deep_analysis",        "deep_analysis"),
    ("adversarial_critique", "adversarial_critique"),
    ("research_synthesis",   "research_synthesis"),
    ("debate_moderation",    "debate_moderation"),
    ("hypothesis_testing",   "hypothesis_testing"),
    ("citation_graph",       "citation_graph"),
    # confidence_scoring is an alias for research_synthesis (band
    # is embedded inside the synthesis report, not a separate skill)
    ("confidence_scoring",   "research_synthesis"),
    # research is the d9-inert alias used by the cascade documentation
    ("research",             "research_synthesis"),
])
def test_d10_capability_mapping_present(
    handoffs_config, capability, skill_name,
):
    key = ("d10_research_lab", capability)
    assert key in handoffs_config.default_skill_per_capability, (
        f"missing mapping for {key} — ADR-0090 Phase D didn't land"
    )
    skill = handoffs_config.default_skill_per_capability[key]
    assert skill.skill_name == skill_name
    assert skill.skill_version == "1"


# ---------------------------------------------------------------------------
# Active cascades
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("src_dom,src_cap,tgt_dom,tgt_cap", [
    ("d1_knowledge_forge", "knowledge_summarize",
     "d10_research_lab", "source_gathering"),
    ("d10_research_lab", "research_synthesis",
     "d1_knowledge_forge", "knowledge_curation"),
    ("d10_research_lab", "research_synthesis",
     "d9_learning_coach", "curriculum_module"),
    ("d10_research_lab", "research_synthesis",
     "d7_content_studio", "content_drafting"),
])
def test_d10_active_cascade_present(
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
    # d9 deep_research_request → d10.research is inert (d9 side
    # lacks the capability)
    ("d9_learning_coach", "deep_research_request", "d10_research_lab"),
    # d10 → d4 ADR proposal cascade is inert (both sides lack
    # adr_proposal capability)
    ("d10_research_lab", "adr_proposal", "d4_code_review"),
])
def test_d10_inert_cascades_not_codified(
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
        f"accidentally codified; per ADR-0090 Phase D it must "
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
        f"{tgt_dom}.{tgt_cap} was removed during D10 Phase D edit"
    )


# ---------------------------------------------------------------------------
# D10 domain manifest
# ---------------------------------------------------------------------------


def test_d10_manifest_status_is_live(d10_manifest):
    assert d10_manifest["status"] == "live", (
        "ADR-0090 Phase D — d10_research_lab.yaml status must be "
        "'live' after the rollout closes"
    )


def test_d10_manifest_lists_lab_synthesizer_not_bare_synthesizer(d10_manifest):
    """ADR-0090 Decision 2 — D10 role is named lab_synthesizer to
    avoid collision with D1's synthesizer. The manifest must
    reflect the canonical name."""
    roles = [e["role"] for e in d10_manifest["entry_agents"]]
    assert "lab_synthesizer" in roles, (
        "d10_research_lab.yaml entry_agents missing lab_synthesizer "
        "(check ADR-0090 Decision 2)"
    )
    # Bare 'synthesizer' belongs to D1; D10 must NOT claim it.
    assert "synthesizer" not in roles, (
        "d10_research_lab.yaml entry_agents incorrectly lists bare "
        "'synthesizer'; that name belongs to D1's role and would "
        "collide. Use lab_synthesizer per ADR-0090 Decision 2."
    )


@pytest.mark.parametrize("role,capability", [
    ("gatherer",         "source_gathering"),
    ("analyst",          "deep_analysis"),
    ("critic",           "adversarial_critique"),
    ("lab_synthesizer",  "research_synthesis"),
    ("debate_moderator", "debate_moderation"),
    ("experimenter",     "hypothesis_testing"),
])
def test_d10_manifest_entry_agents_present(d10_manifest, role, capability):
    pairs = [
        (e["role"], e["capability"])
        for e in d10_manifest["entry_agents"]
    ]
    assert (role, capability) in pairs, (
        f"d10_research_lab.yaml entry_agents missing ({role}, {capability})"
    )
