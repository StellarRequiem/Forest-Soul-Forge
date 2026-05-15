"""ADR-0072 T4 (B329) — orchestrator bias from preferences + rules.

Coverage:
  apply_behavior_bias:
    - routable subintent passes through unchanged (no bias applies)
    - planned_domain / no_match pass through unchanged
    - ambiguous subintent with matching preference is rewritten
    - ambiguous subintent with no preferences but a matching rule
      is rewritten
    - preference wins over learned rule on same ambiguous subintent
    - inactive learned rules (pending/refused) are ignored
    - weight=0 preferences/rules are ignored
    - weight ties break on id (deterministic)
    - empty pools return unchanged + applied=None
    - input subintent is never mutated

  annotate_route_with_bias:
    - bias=None → unchanged
    - Unroutable + bias → unchanged (bias doesn't help unroutable)
    - successful route + preference bias → reason prepended with
      "via preference 'pref-id'"
    - learned rule bias → reason prepended with "via learned"
"""
from __future__ import annotations

import pytest

from forest_soul_forge.core.behavior_provenance import (
    LearnedRule,
    Preference,
)
from forest_soul_forge.core.behavior_routing import (
    BiasApplication,
    annotate_route_with_bias,
    apply_behavior_bias,
)
from forest_soul_forge.core.routing_engine import (
    ResolvedRoute,
    SkillRef,
    UnroutableSubIntent,
)


def _pref(pref_id, domain, weight=0.7):
    return Preference(
        id=pref_id,
        statement=f"prefer {domain} for ambiguous content tasks",
        weight=weight,
        domain=domain,
        created_at="2026-05-15T00:00:00Z",
        updated_at="2026-05-15T00:00:00Z",
    )


def _rule(rule_id, domain, status="active", weight=0.5):
    return LearnedRule(
        id=rule_id,
        statement=f"intents about X route to {domain}",
        weight=weight,
        domain=domain,
        proposer_agent_dna="dna_abc",
        created_at="2026-05-15T00:00:00Z",
        status=status,
    )


# ---------------------------------------------------------------------------
# apply_behavior_bias — pass-through cases
# ---------------------------------------------------------------------------


def test_routable_subintent_passes_through_unchanged():
    si = {"intent": "x", "domain": "d7", "capability": "draft",
          "confidence": 0.9, "status": "routable"}
    out, applied = apply_behavior_bias(
        si, preferences=(_pref("p1", "d10"),),
    )
    assert out == si
    assert applied is None


@pytest.mark.parametrize("status", ["planned_domain", "no_match", "unknown"])
def test_non_ambiguous_passes_through(status):
    si = {"intent": "x", "domain": "d7", "capability": "draft",
          "confidence": 0.4, "status": status}
    out, applied = apply_behavior_bias(
        si, preferences=(_pref("p1", "d10"),),
    )
    assert out == si
    assert applied is None


# ---------------------------------------------------------------------------
# apply_behavior_bias — preferences
# ---------------------------------------------------------------------------


def test_ambiguous_with_matching_preference_rewrites():
    si = {"intent": "draft", "domain": "?", "capability": "draft",
          "confidence": 0.4, "status": "ambiguous"}
    out, applied = apply_behavior_bias(
        si, preferences=(_pref("p1", "d7"),),
    )
    assert out["domain"] == "d7"
    assert out["status"] == "routable"
    # Original intent + capability preserved.
    assert out["intent"] == "draft"
    assert out["capability"] == "draft"
    assert applied is not None
    assert applied.layer == "preference"
    assert applied.rule_id == "p1"
    assert applied.target_domain == "d7"


def test_input_subintent_not_mutated():
    si = {"intent": "x", "domain": "?", "capability": "c",
          "confidence": 0.4, "status": "ambiguous"}
    snapshot = dict(si)
    apply_behavior_bias(si, preferences=(_pref("p1", "d7"),))
    assert si == snapshot


def test_higher_weight_preference_wins():
    si = {"intent": "x", "domain": "?", "capability": "c",
          "confidence": 0.4, "status": "ambiguous"}
    prefs = (
        _pref("p_low",  "d7",  weight=0.3),
        _pref("p_high", "d10", weight=0.9),
    )
    out, applied = apply_behavior_bias(si, preferences=prefs)
    assert out["domain"] == "d10"
    assert applied.rule_id == "p_high"


def test_weight_zero_preference_ignored():
    si = {"intent": "x", "domain": "?", "capability": "c",
          "confidence": 0.4, "status": "ambiguous"}
    out, applied = apply_behavior_bias(
        si, preferences=(_pref("p_off", "d7", weight=0.0),),
    )
    # Weight-0 is "off"; treated as no preference.
    assert out == si
    assert applied is None


def test_weight_ties_break_on_id():
    si = {"intent": "x", "domain": "?", "capability": "c",
          "confidence": 0.4, "status": "ambiguous"}
    prefs = (
        _pref("p_b", "d10", weight=0.7),
        _pref("p_a", "d7",  weight=0.7),
    )
    out, applied = apply_behavior_bias(si, preferences=prefs)
    # Ties broken by id ascending → "p_a" wins.
    assert applied.rule_id == "p_a"
    assert out["domain"] == "d7"


# ---------------------------------------------------------------------------
# apply_behavior_bias — learned rules
# ---------------------------------------------------------------------------


def test_ambiguous_with_active_rule_only():
    si = {"intent": "x", "domain": "?", "capability": "c",
          "confidence": 0.4, "status": "ambiguous"}
    out, applied = apply_behavior_bias(
        si, learned_rules=(_rule("r1", "d7"),),
    )
    assert out["domain"] == "d7"
    assert applied.layer == "learned"
    assert applied.rule_id == "r1"


def test_preference_wins_over_learned_rule():
    si = {"intent": "x", "domain": "?", "capability": "c",
          "confidence": 0.4, "status": "ambiguous"}
    out, applied = apply_behavior_bias(
        si,
        preferences=(_pref("p1", "d10"),),
        learned_rules=(_rule("r1", "d7", weight=0.9),),
    )
    # Even though the rule has higher weight, preferences are
    # tier 400 vs rules tier 100; preference always wins.
    assert out["domain"] == "d10"
    assert applied.layer == "preference"


def test_pending_rules_are_ignored():
    si = {"intent": "x", "domain": "?", "capability": "c",
          "confidence": 0.4, "status": "ambiguous"}
    out, applied = apply_behavior_bias(
        si,
        learned_rules=(_rule("r1", "d7", status="pending_activation"),),
    )
    assert out == si
    assert applied is None


def test_refused_rules_are_ignored():
    si = {"intent": "x", "domain": "?", "capability": "c",
          "confidence": 0.4, "status": "ambiguous"}
    out, applied = apply_behavior_bias(
        si,
        learned_rules=(_rule("r1", "d7", status="refused"),),
    )
    assert out == si
    assert applied is None


def test_empty_pools_returns_unchanged():
    si = {"intent": "x", "domain": "?", "capability": "c",
          "confidence": 0.4, "status": "ambiguous"}
    out, applied = apply_behavior_bias(si)
    assert out == si
    assert applied is None


# ---------------------------------------------------------------------------
# annotate_route_with_bias
# ---------------------------------------------------------------------------


def _ok_route():
    return ResolvedRoute(
        target_domain="d7",
        target_capability="draft",
        target_instance_id="ag1",
        skill_ref=SkillRef("draft_post", "1"),
        intent="x",
        confidence=0.4,
        reason="original reason",
    )


def test_annotate_no_bias_passthrough():
    r = _ok_route()
    assert annotate_route_with_bias(r, None) is r


def test_annotate_unroutable_passthrough():
    u = UnroutableSubIntent(
        intent="x", domain="d7", capability="c",
        confidence=0.4, code="domain_not_found", detail="...",
    )
    bias = BiasApplication(layer="preference", rule_id="p1",
                           rule_statement="prefer d7",
                           target_domain="d7")
    out = annotate_route_with_bias(u, bias)
    # Unroutable + bias: caller can't apply a route anyway, leave alone.
    assert out is u


def test_annotate_preference_bias_prepends_reason():
    r = _ok_route()
    bias = BiasApplication(
        layer="preference", rule_id="p1",
        rule_statement="prefer d7 for draft",
        target_domain="d7",
    )
    out = annotate_route_with_bias(r, bias)
    assert isinstance(out, ResolvedRoute)
    assert "via preference 'p1'" in out.reason
    assert "prefer d7 for draft" in out.reason
    assert "original reason" in out.reason
    # Everything else preserved.
    assert out.target_domain == r.target_domain
    assert out.target_instance_id == r.target_instance_id


def test_annotate_learned_bias_prepends_reason():
    r = _ok_route()
    bias = BiasApplication(
        layer="learned", rule_id="r99",
        rule_statement="blog posts → d7",
        target_domain="d7",
    )
    out = annotate_route_with_bias(r, bias)
    assert "via learned 'r99'" in out.reason
    assert "blog posts → d7" in out.reason
