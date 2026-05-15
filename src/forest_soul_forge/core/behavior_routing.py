"""Routing-side bias from operator preferences + learned rules.

ADR-0072 T4 (B329). Wraps the hardcoded routing rail (ADR-0067 T4
:func:`resolve_route`) with the preference + learned-rule bias
that the operator and the agents have accumulated since birth.

## Precedence (ADR-0072 D1)

Higher tier ALWAYS wins:

  1000 hardcoded_handoff   — engineer via PR; immutable at runtime
   800 constitutional       — operator at birth; immutable after birth
   400 preference           — operator-edited preferences.yaml
   100 learned              — agent-emitted, RA-gated learned_rules.yaml

The hardcoded rail is checked first by :func:`resolve_route`
itself. T4's job is preferences + learned rules — both kick in
ONLY when decompose_intent flagged the sub-intent as ``ambiguous``
(decompose_intent couldn't decide which domain to route to with
high confidence). They never override a hardcoded handoff or an
already-routable sub-intent.

When BOTH a preference and a learned rule match the same
ambiguous sub-intent, the preference wins (its tier is 400 vs the
rule's 100). The losing rule is dropped from the bias output and
not even consulted.

## What gets recorded

Every applied bias surfaces in the :class:`ResolvedRoute.reason`
text as ``"via preference <id>"`` or ``"via learned rule <id>"``
so the audit chain captures which non-hardcoded bias drove the
routing decision. Operators can use this to find rules that
consistently route incorrectly (and tighten them via the
preference path).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

from forest_soul_forge.core.behavior_provenance import (
    LearnedRule,
    Preference,
)
from forest_soul_forge.core.routing_engine import (
    ResolvedRoute,
    UnroutableSubIntent,
)


@dataclass(frozen=True)
class BiasApplication:
    """Trace of what bias the routing layer applied.

    Carried alongside the post-bias sub-intent so the eventual
    ResolvedRoute can include it in ``reason`` for audit chain
    surfacing.
    """
    layer: str           # "preference" or "learned"
    rule_id: str         # preference.id or learned_rule.id
    rule_statement: str  # for operator-readable logging
    target_domain: str   # the domain the bias picked


def apply_behavior_bias(
    subintent: dict,
    *,
    preferences: Optional[tuple[Preference, ...]] = None,
    learned_rules: Optional[tuple[LearnedRule, ...]] = None,
) -> tuple[dict, Optional[BiasApplication]]:
    """Maybe modify the sub-intent based on operator bias.

    Returns ``(biased_subintent, applied)``:

      - When the input is already ``routable`` (decompose_intent
        was confident) or ``not_routable`` for a structural reason
        unrelated to ambiguity, returns the sub-intent unchanged +
        applied=None. Hardcoded routing takes over.

      - When the input is ``ambiguous`` (decompose_intent low
        confidence), preferences are walked first (highest weight
        wins); if a match is found, the sub-intent's domain field
        is replaced with the preference's domain + status flips
        to ``routable``. The applied bias is returned for audit.

      - When no preference matches, active learned rules are walked
        in weight-descending order. Only ``status='active'`` rules
        are considered — pending rules (awaiting RA verification
        via the B325 cron) are ignored on principle.

      - When neither layer offers a match, the sub-intent is
        returned unchanged + applied=None.

    The function never mutates its inputs.
    """
    status = subintent.get("status", "unknown")
    if status != "ambiguous":
        return subintent, None

    # Walk preferences (tier 400) first. Highest weight wins; ties
    # break on id for deterministic behavior.
    if preferences:
        pref = _pick_top_weighted_preference(preferences)
        if pref is not None and pref.domain:
            biased = dict(subintent)
            biased["domain"] = pref.domain
            biased["status"] = "routable"
            return biased, BiasApplication(
                layer="preference",
                rule_id=pref.id,
                rule_statement=pref.statement,
                target_domain=pref.domain,
            )

    # Walk active learned rules (tier 100) if no preference fired.
    if learned_rules:
        rule = _pick_top_weighted_active_rule(learned_rules)
        if rule is not None and rule.domain:
            biased = dict(subintent)
            biased["domain"] = rule.domain
            biased["status"] = "routable"
            return biased, BiasApplication(
                layer="learned",
                rule_id=rule.id,
                rule_statement=rule.statement,
                target_domain=rule.domain,
            )

    return subintent, None


def annotate_route_with_bias(
    route: ResolvedRoute | UnroutableSubIntent,
    bias: Optional[BiasApplication],
) -> ResolvedRoute | UnroutableSubIntent:
    """Stamp a bias trace onto a ResolvedRoute's ``reason`` field.

    When the route is Unroutable OR no bias applied, returns
    the route unchanged. For successful ResolvedRoutes with an
    applied bias, prepends a one-line provenance string so the
    audit chain captures the bias source.
    """
    if bias is None or isinstance(route, UnroutableSubIntent):
        return route
    new_reason = (
        f"via {bias.layer} {bias.rule_id!r}: {bias.rule_statement} "
        f"| {route.reason}"
    )
    return replace(route, reason=new_reason)


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _pick_top_weighted_preference(
    prefs: tuple[Preference, ...],
) -> Optional[Preference]:
    """Highest-weight preference; ties break on id for stability.

    Preferences with weight=0 are treated as "off"; we skip them
    even if they're the only entry, so an operator who wrote
    weight: 0 in preferences.yaml gets the don't-bias semantic
    they probably intended.
    """
    candidates = [p for p in prefs if p.weight > 0.0 and p.domain]
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda p: (-p.weight, p.id),
    )[0]


def _pick_top_weighted_active_rule(
    rules: tuple[LearnedRule, ...],
) -> Optional[LearnedRule]:
    """Highest-weight ACTIVE rule. Pending + refused rules are
    deliberately ignored — only RA-verified active rules apply."""
    candidates = [
        r for r in rules
        if r.status == "active" and r.weight > 0.0 and r.domain
    ]
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda r: (-r.weight, r.id),
    )[0]
