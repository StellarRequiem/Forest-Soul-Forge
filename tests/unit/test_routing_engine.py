"""ADR-0067 T4 (B282) — routing engine tests.

Covers:
  - load_handoffs: happy path, missing file (soft), malformed YAML
    (hard), schema mismatch (hard), per-rule errors (soft)
  - resolve_route: all five failure codes + happy path
  - apply_cascade_rules: cascade fires, cascade fails cleanly,
    no-matching-rule returns empty list, cascade doesn't recurse
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from forest_soul_forge.core.domain_registry import (
    Domain,
    DomainRegistry,
    EntryAgent,
)
from forest_soul_forge.core.routing_engine import (
    DEFAULT_HANDOFFS_PATH,
    UNROUTABLE_DOMAIN_NOT_FOUND,
    UNROUTABLE_DOMAIN_PLANNED,
    UNROUTABLE_LOW_CONFIDENCE,
    UNROUTABLE_NO_ALIVE_AGENT,
    UNROUTABLE_NO_SKILL_MAPPING,
    Handoff,
    HandoffsConfig,
    HandoffsError,
    ResolvedRoute,
    SkillRef,
    UnroutableSubIntent,
    apply_cascade_rules,
    load_handoffs,
    resolve_route,
)


def _registry_two_domains() -> DomainRegistry:
    """Live + planned domains for the happy + planned paths."""
    return DomainRegistry(domains=(
        Domain(
            domain_id="d_live",
            name="Live Domain",
            status="live",
            description="live test domain",
            entry_agents=(
                EntryAgent(role="liver", capability="cap_a"),
                EntryAgent(role="other", capability="cap_b"),
            ),
            capabilities=("cap_a", "cap_b"),
            example_intents=(),
        ),
        Domain(
            domain_id="d_planned",
            name="Planned Domain",
            status="planned",
            description="planned test",
            entry_agents=(),
            capabilities=("cap_x",),
            example_intents=(),
        ),
    ))


def _handoffs_basic() -> HandoffsConfig:
    """Skill mappings for the happy path; one cascade rule."""
    return HandoffsConfig(
        default_skill_per_capability={
            ("d_live", "cap_a"): SkillRef("do_a", "1"),
            ("d_live", "cap_b"): SkillRef("do_b", "1"),
            ("d_followon", "cap_c"): SkillRef("do_c", "1"),
        },
        cascade_rules=(
            Handoff(
                source_domain="d_live",
                source_capability="cap_a",
                target_domain="d_followon",
                target_capability="cap_c",
                reason="every cap_a fires cap_c",
            ),
        ),
    )


def _alive_inventory() -> list[dict]:
    return [
        {"instance_id": "live_inst_1", "role": "liver", "status": "active"},
        {"instance_id": "other_inst_1", "role": "other", "status": "active"},
        {"instance_id": "archived_inst", "role": "liver", "status": "archived"},
    ]


# ---------------------------------------------------------------------------
# load_handoffs
# ---------------------------------------------------------------------------
def test_load_handoffs_missing_file_is_soft(tmp_path):
    cfg, errors = load_handoffs(tmp_path / "nope.yaml")
    assert cfg.default_skill_per_capability == {}
    assert cfg.cascade_rules == ()
    assert any("not found" in e for e in errors)


def test_load_handoffs_malformed_yaml_raises(tmp_path):
    p = tmp_path / "h.yaml"
    p.write_text("not: valid: yaml: :::")
    with pytest.raises(HandoffsError, match="malformed YAML"):
        load_handoffs(p)


def test_load_handoffs_schema_version_mismatch_raises(tmp_path):
    p = tmp_path / "h.yaml"
    p.write_text(yaml.safe_dump({"schema_version": 999}))
    with pytest.raises(HandoffsError, match="schema_version"):
        load_handoffs(p)


def test_load_handoffs_happy_path(tmp_path):
    p = tmp_path / "h.yaml"
    p.write_text(yaml.safe_dump({
        "schema_version": 1,
        "default_skill_per_capability": [
            {"domain": "d1", "capability": "cap1",
             "skill_name": "sk1", "skill_version": "1"},
        ],
        "cascade_rules": [
            {"source_domain": "d1", "source_capability": "cap1",
             "target_domain": "d2", "target_capability": "cap2",
             "reason": "test cascade"},
        ],
    }))
    cfg, errors = load_handoffs(p)
    assert errors == []
    assert cfg.default_skill_per_capability[("d1", "cap1")].skill_name == "sk1"
    assert len(cfg.cascade_rules) == 1
    assert cfg.cascade_rules[0].reason == "test cascade"


def test_load_handoffs_per_rule_error_is_soft(tmp_path):
    p = tmp_path / "h.yaml"
    p.write_text(yaml.safe_dump({
        "schema_version": 1,
        "default_skill_per_capability": [
            {"domain": "d1"},  # missing fields
        ],
        "cascade_rules": [],
    }))
    cfg, errors = load_handoffs(p)
    # Bad rule dropped; non-fatal error reported.
    assert cfg.default_skill_per_capability == {}
    assert any("missing fields" in e for e in errors)


# ---------------------------------------------------------------------------
# resolve_route — failure codes
# ---------------------------------------------------------------------------
def test_resolve_route_non_routable_status_passes_through():
    """status='ambiguous' from decompose_intent → UNROUTABLE_LOW_CONFIDENCE."""
    result = resolve_route(
        {"intent": "x", "domain": "d_live", "capability": "cap_a",
         "confidence": 0.3, "status": "ambiguous"},
        _registry_two_domains(), _handoffs_basic(), _alive_inventory(),
    )
    assert isinstance(result, UnroutableSubIntent)
    assert result.code == UNROUTABLE_LOW_CONFIDENCE


def test_resolve_route_domain_not_found():
    result = resolve_route(
        {"intent": "x", "domain": "d_ghost", "capability": "any",
         "confidence": 0.9, "status": "routable"},
        _registry_two_domains(), _handoffs_basic(), _alive_inventory(),
    )
    assert isinstance(result, UnroutableSubIntent)
    assert result.code == UNROUTABLE_DOMAIN_NOT_FOUND


def test_resolve_route_planned_domain():
    result = resolve_route(
        {"intent": "x", "domain": "d_planned", "capability": "cap_x",
         "confidence": 0.9, "status": "routable"},
        _registry_two_domains(), _handoffs_basic(), _alive_inventory(),
    )
    assert isinstance(result, UnroutableSubIntent)
    assert result.code == UNROUTABLE_DOMAIN_PLANNED


def test_resolve_route_no_skill_mapping():
    """Domain + capability exist in registry but handoffs.yaml has
    no skill_name + skill_version for the pair."""
    handoffs = HandoffsConfig(
        default_skill_per_capability={},  # empty
        cascade_rules=(),
    )
    result = resolve_route(
        {"intent": "x", "domain": "d_live", "capability": "cap_a",
         "confidence": 0.9, "status": "routable"},
        _registry_two_domains(), handoffs, _alive_inventory(),
    )
    assert isinstance(result, UnroutableSubIntent)
    assert result.code == UNROUTABLE_NO_SKILL_MAPPING


def test_resolve_route_no_alive_agent():
    """Registry + handoffs are fine but no alive agent with the
    matching role is in the inventory."""
    result = resolve_route(
        {"intent": "x", "domain": "d_live", "capability": "cap_a",
         "confidence": 0.9, "status": "routable"},
        _registry_two_domains(), _handoffs_basic(),
        agent_inventory=[
            {"instance_id": "i1", "role": "liver", "status": "archived"},
        ],
    )
    assert isinstance(result, UnroutableSubIntent)
    assert result.code == UNROUTABLE_NO_ALIVE_AGENT


# ---------------------------------------------------------------------------
# resolve_route — happy path
# ---------------------------------------------------------------------------
def test_resolve_route_happy_path():
    result = resolve_route(
        {"intent": "do the live thing", "domain": "d_live",
         "capability": "cap_a", "confidence": 0.95, "status": "routable"},
        _registry_two_domains(), _handoffs_basic(), _alive_inventory(),
    )
    assert isinstance(result, ResolvedRoute)
    assert result.target_domain == "d_live"
    assert result.target_capability == "cap_a"
    assert result.target_instance_id == "live_inst_1"
    assert result.skill_ref == SkillRef("do_a", "1")
    assert result.confidence == 0.95
    assert result.is_cascade is False


def test_resolve_route_picks_correct_role_for_capability():
    """Two entry_agents with different capabilities → resolver picks
    the right role."""
    result = resolve_route(
        {"intent": "do cap_b thing", "domain": "d_live",
         "capability": "cap_b", "confidence": 0.9, "status": "routable"},
        _registry_two_domains(), _handoffs_basic(), _alive_inventory(),
    )
    assert isinstance(result, ResolvedRoute)
    assert result.target_instance_id == "other_inst_1"
    assert result.skill_ref == SkillRef("do_b", "1")


# ---------------------------------------------------------------------------
# apply_cascade_rules
# ---------------------------------------------------------------------------
def test_cascade_fires_with_followon():
    """Live d_live/cap_a route → cascade fires to d_followon/cap_c.
    The follow-on resolves successfully when the inventory has an
    appropriate agent + the registry has d_followon."""
    registry = DomainRegistry(domains=(
        Domain(
            domain_id="d_live", name="L", status="live", description="",
            entry_agents=(EntryAgent("liver", "cap_a"),),
            capabilities=("cap_a",), example_intents=(),
        ),
        Domain(
            domain_id="d_followon", name="F", status="live", description="",
            entry_agents=(EntryAgent("follower", "cap_c"),),
            capabilities=("cap_c",), example_intents=(),
        ),
    ))
    inventory = [
        {"instance_id": "liver_1", "role": "liver", "status": "active"},
        {"instance_id": "follower_1", "role": "follower", "status": "active"},
    ]
    initial = ResolvedRoute(
        target_domain="d_live", target_capability="cap_a",
        target_instance_id="liver_1",
        skill_ref=SkillRef("do_a", "1"),
        intent="do a", confidence=0.9, reason="...",
    )
    follow_ons = apply_cascade_rules(
        initial, _handoffs_basic(), registry, inventory,
    )
    assert len(follow_ons) == 1
    assert isinstance(follow_ons[0], ResolvedRoute)
    assert follow_ons[0].target_domain == "d_followon"
    assert follow_ons[0].is_cascade is True
    assert follow_ons[0].cascade_source_domain == "d_live"
    assert follow_ons[0].cascade_source_capability == "cap_a"


def test_cascade_unresolvable_returns_unroutable():
    """Cascade rule's target is in the rules but the resolver fails
    (no agent for the target) — get UnroutableSubIntent, not silent drop."""
    initial = ResolvedRoute(
        target_domain="d_live", target_capability="cap_a",
        target_instance_id="liver_1",
        skill_ref=SkillRef("do_a", "1"),
        intent="do a", confidence=0.9, reason="...",
    )
    follow_ons = apply_cascade_rules(
        initial, _handoffs_basic(), _registry_two_domains(),
        agent_inventory=_alive_inventory(),
    )
    # _registry_two_domains doesn't include d_followon → unroutable
    assert len(follow_ons) == 1
    assert isinstance(follow_ons[0], UnroutableSubIntent)


def test_cascade_no_matching_rule_returns_empty():
    """An initial route whose (source_domain, source_capability)
    has no cascade rule → empty follow-ons list."""
    initial = ResolvedRoute(
        target_domain="d_live", target_capability="cap_b",  # no rule
        target_instance_id="other_inst_1",
        skill_ref=SkillRef("do_b", "1"),
        intent="do b", confidence=0.9, reason="...",
    )
    follow_ons = apply_cascade_rules(
        initial, _handoffs_basic(), _registry_two_domains(),
        _alive_inventory(),
    )
    assert follow_ons == []


def test_seed_handoffs_yaml_loads_clean():
    """The shipped config/handoffs.yaml seed loads with zero errors.
    Catches typos / dangling references at PR time."""
    repo_root = Path(__file__).resolve().parents[2]
    seed_path = repo_root / "config" / "handoffs.yaml"
    if not seed_path.exists():
        pytest.skip("seed handoffs not present")
    cfg, errors = load_handoffs(seed_path)
    assert errors == [], (
        f"shipped handoffs.yaml has config errors: {errors}"
    )
    # At least the cascades we documented in the seed are present.
    assert len(cfg.cascade_rules) >= 2
    pr_to_compliance = [
        r for r in cfg.cascade_rules
        if r.source_domain == "d4_code_review"
        and r.target_domain == "d8_compliance"
    ]
    assert len(pr_to_compliance) == 1
