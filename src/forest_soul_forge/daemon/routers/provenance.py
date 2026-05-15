"""``/provenance/*`` — ADR-0072 T5 (B330) Provenance pane API.

Read-only surface that powers the frontend Provenance tab.
Exposes the four layers in ADR-0072 D1's precedence ladder so
the operator can see, at a glance, "what's biasing my agents"
across hardcoded handoffs, constitutional policy, operator
preferences, and learned rules (pending + active).

Endpoints:

  GET /provenance/active   — preferences + learned-rule buckets
                              (pending / active / refused).
  GET /provenance/handoffs — hardcoded skill mappings + cascade
                              rules from config/handoffs.yaml.

The fsf provenance CLI (T2 / B303) is the offline-readable
counterpart. This router is the on-daemon online surface.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from forest_soul_forge.core.behavior_provenance import (
    BehaviorProvenanceError,
    LearnedRulesConfig,
    PreferencesConfig,
    load_learned_rules,
    load_preferences,
)
from forest_soul_forge.core.routing_engine import (
    HandoffsError,
    load_handoffs,
)


router = APIRouter(prefix="/provenance", tags=["provenance"])


@router.get("/active")
async def provenance_active() -> dict[str, Any]:
    """Return preferences (tier 400) + learned rules (tier 100),
    bucketed by status. The frontend renders the precedence
    ladder + the per-rule status pill (active / pending /
    refused) from this payload."""
    try:
        prefs, pref_errors = load_preferences()
    except BehaviorProvenanceError as e:
        prefs = PreferencesConfig(schema_version=1, preferences=())
        pref_errors = [str(e)]
    try:
        rules, rule_errors = load_learned_rules()
    except BehaviorProvenanceError as e:
        rules = LearnedRulesConfig(
            schema_version=1, pending_activation=(), active=(),
        )
        rule_errors = [str(e)]

    # Bucket refused vs pending — they live in the same list on
    # disk but the UI surfaces them differently.
    pending = [
        _rule_dict(r) for r in rules.pending_activation
        if r.status == "pending_activation"
    ]
    refused = [
        _rule_dict(r) for r in rules.pending_activation
        if r.status == "refused"
    ]
    active = [_rule_dict(r) for r in rules.active]

    return {
        "schema_version": 1,
        "precedence": [
            {"tier": 1000, "name": "hardcoded_handoff"},
            {"tier": 800,  "name": "constitutional"},
            {"tier": 400,  "name": "preference"},
            {"tier": 100,  "name": "learned"},
        ],
        "preferences": [
            {
                "id":        p.id,
                "statement": p.statement,
                "weight":    p.weight,
                "domain":    p.domain,
                "updated_at": p.updated_at,
            }
            for p in prefs.preferences
        ],
        "learned_rules": {
            "active":               active,
            "pending_activation":   pending,
            "refused":              refused,
        },
        "errors": [*pref_errors, *rule_errors],
    }


@router.get("/handoffs")
async def provenance_handoffs() -> dict[str, Any]:
    """Return the hardcoded routing rail (handoffs.yaml).
    Read-only surface — the cascade rules + skill mappings here
    are engineer-edited via PR per ADR-0072 D1."""
    try:
        cfg, errors = load_handoffs()
    except HandoffsError as e:
        return {
            "schema_version": 1,
            "default_skill_per_capability": [],
            "cascade_rules": [],
            "errors": [str(e)],
        }
    return {
        "schema_version": 1,
        "default_skill_per_capability": [
            {
                "domain":        domain,
                "capability":    capability,
                "skill_name":    skill_ref.skill_name,
                "skill_version": skill_ref.skill_version,
            }
            for (domain, capability), skill_ref in (
                cfg.default_skill_per_capability.items()
            )
        ],
        "cascade_rules": [
            {
                "source_domain":      h.source_domain,
                "source_capability":  h.source_capability,
                "target_domain":      h.target_domain,
                "target_capability":  h.target_capability,
                "reason":             h.reason,
            }
            for h in cfg.cascade_rules
        ],
        "errors": errors,
    }


def _rule_dict(r) -> dict[str, Any]:
    out = {
        "id":         r.id,
        "statement":  r.statement,
        "weight":     r.weight,
        "domain":     r.domain,
        "status":     r.status,
        "proposer_agent_dna": r.proposer_agent_dna,
        "created_at": r.created_at,
    }
    if r.verification_verdict is not None:
        out["verification_verdict"] = r.verification_verdict
    if r.verification_reason is not None:
        out["verification_reason"] = r.verification_reason
    return out
