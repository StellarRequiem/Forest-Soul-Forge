"""Routing engine — ADR-0067 T4 (B282).

Combines the three things needed to turn a sub-intent (output of
decompose_intent.v1) into an actionable :class:`ResolvedRoute`:

  1. **Domain registry** — what domains exist and their entry agents
     (loaded from config/domains/*.yaml via ADR-0067 T1).
  2. **Handoffs catalog** — operator-edited config/handoffs.yaml.
     Two pieces:
       a. ``default_skill_per_capability`` — mapping from
          (domain_id, capability) → (skill_name, skill_version).
          Says "when routing to capability X in domain Y, use
          skill Z." T3's route_to_domain.v1 needs skill_name +
          skill_version; this is where they come from.
       b. ``cascade_rules`` — hardcoded follow-on routes. A rule
          like "every successful d4_code_review route also fires
          a d8_compliance route" goes here. Cascades are engineer-
          edited via PR (per ADR-0072 — code-reviewed before merge).
  3. **Agent inventory** — alive agents from the registry. Resolver
     picks the right instance for the chosen (domain, capability).

## Surface

  - :class:`Handoff` — one cascade rule from handoffs.yaml
  - :class:`SkillRef` — (skill_name, skill_version) pair
  - :class:`ResolvedRoute` — successful resolution: domain_id,
    capability, target_instance_id, skill_ref, intent, confidence
  - :class:`UnroutableSubIntent` — could not resolve (reason +
    enum'd code)
  - :func:`load_handoffs(path)` — read + validate handoffs.yaml
  - :func:`resolve_route(subintent, registry, handoffs, agent_inventory)`
    — main entry; returns a single RoutingDecision OR Unroutable
  - :func:`apply_cascade_rules(decision, handoffs, registry, agent_inventory)`
    — given an initial successful decision, generate follow-on routes

## Pure-function discipline

Every function here takes its inputs explicitly. No global state.
The orchestrator agent (T5) is responsible for assembling the
inputs (load registry, load handoffs, query alive agents from the
Forest registry) and feeding them to resolve_route. Keeps T4
testable in isolation.

## Hardcoded vs learned discipline (ADR-0072 reminder)

This module ships the HARDCODED rail. Cascade rules are
engineer-edited via PR and code-reviewed. The LEARNED rail
(operator-preference adaptation) lives in T4b / a future tranche;
when it ships, learned routes are applied AFTER hardcoded ones,
and hardcoded always wins on conflict.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml


DEFAULT_HANDOFFS_PATH = Path("config/handoffs.yaml")

ENV_VAR = "FSF_HANDOFFS_PATH"


class HandoffsError(RuntimeError):
    """Raised when handoffs.yaml is malformed at the structural level
    (top-level not a mapping, schema_version missing, etc.). Per-rule
    problems surface as soft errors in the load tuple."""


@dataclass(frozen=True)
class SkillRef:
    """Reference to a specific skill on a target agent."""
    skill_name: str
    skill_version: str


@dataclass(frozen=True)
class Handoff:
    """One hardcoded cascade rule.

    When a routing decision fires for ``source_domain`` +
    ``source_capability``, a follow-on routing decision is generated
    for ``target_domain`` + ``target_capability``. The follow-on
    inherits the original intent text and confidence (but the
    follow-on's intent_hash will differ in the audit chain).

    Use case: every successful PR review in d4_code_review fires
    a d8_compliance scan. Operator authoring handoffs.yaml writes:

      cascade_rules:
        - source_domain: d4_code_review
          source_capability: review_signoff
          target_domain: d8_compliance
          target_capability: compliance_scan
          reason: "every PR triggers compliance pass (ADR-0072)"
    """
    source_domain: str
    source_capability: str
    target_domain: str
    target_capability: str
    reason: str


@dataclass(frozen=True)
class HandoffsConfig:
    """All routing config loaded from handoffs.yaml."""
    # (domain_id, capability) → SkillRef
    default_skill_per_capability: dict[tuple[str, str], SkillRef]
    cascade_rules: tuple[Handoff, ...]


@dataclass(frozen=True)
class ResolvedRoute:
    """Successful routing decision — every field route_to_domain.v1
    needs to dispatch."""
    target_domain: str
    target_capability: str
    target_instance_id: str
    skill_ref: SkillRef
    intent: str
    confidence: float
    reason: str
    is_cascade: bool = False
    cascade_source_domain: Optional[str] = None
    cascade_source_capability: Optional[str] = None


@dataclass(frozen=True)
class UnroutableSubIntent:
    """A sub-intent that resolve_route could not turn into a
    ResolvedRoute. The orchestrator surfaces this back to the
    operator rather than guessing."""
    intent: str
    domain: str
    capability: str
    confidence: float
    code: str
    detail: str


# Reason codes for UnroutableSubIntent.code — operator-readable enum.
UNROUTABLE_DOMAIN_NOT_FOUND = "domain_not_found"
UNROUTABLE_DOMAIN_PLANNED = "domain_planned"
UNROUTABLE_LOW_CONFIDENCE = "low_confidence"
UNROUTABLE_NO_SKILL_MAPPING = "no_skill_mapping"
UNROUTABLE_NO_ALIVE_AGENT = "no_alive_agent"


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_handoffs(
    path: Optional[Path] = None,
) -> tuple[HandoffsConfig, list[str]]:
    """Read + validate handoffs.yaml.

    Returns ``(config, errors)``. Errors are non-fatal — one bad rule
    doesn't kill the whole load. Missing file is benign: returns an
    empty HandoffsConfig + a single info note.

    Structural failures (top-level not a mapping, schema_version
    mismatch) raise :class:`HandoffsError` since they make the
    whole catalog unloadable.
    """
    import os as _os
    resolved = (
        path if path is not None
        else Path(_os.environ.get(ENV_VAR, str(DEFAULT_HANDOFFS_PATH)))
    )

    errors: list[str] = []

    if not resolved.exists():
        errors.append(
            f"handoffs config not found at {resolved}; "
            f"orchestrator will route without hardcoded handoffs"
        )
        return HandoffsConfig(
            default_skill_per_capability={},
            cascade_rules=(),
        ), errors

    try:
        text = resolved.read_text(encoding="utf-8")
    except OSError as e:
        raise HandoffsError(f"could not read {resolved}: {e}") from e

    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise HandoffsError(f"{resolved}: malformed YAML: {e}") from e

    if not isinstance(raw, dict):
        raise HandoffsError(
            f"{resolved}: top-level must be a YAML mapping"
        )

    sv = raw.get("schema_version")
    if sv != 1:
        raise HandoffsError(
            f"{resolved}: schema_version {sv!r} not supported "
            f"(expected 1)"
        )

    # Parse default_skill_per_capability.
    default_skills: dict[tuple[str, str], SkillRef] = {}
    raw_defaults = raw.get("default_skill_per_capability") or []
    if not isinstance(raw_defaults, list):
        errors.append(
            "default_skill_per_capability must be a list; ignoring"
        )
    else:
        for idx, raw_entry in enumerate(raw_defaults):
            if not isinstance(raw_entry, dict):
                errors.append(
                    f"default_skill_per_capability[{idx}] must be a mapping"
                )
                continue
            missing = (
                {"domain", "capability", "skill_name", "skill_version"}
                - set(raw_entry.keys())
            )
            if missing:
                errors.append(
                    f"default_skill_per_capability[{idx}] missing "
                    f"fields: {sorted(missing)}"
                )
                continue
            key = (str(raw_entry["domain"]), str(raw_entry["capability"]))
            if key in default_skills:
                errors.append(
                    f"default_skill_per_capability has duplicate "
                    f"(domain, capability) entry {key!r}; first kept"
                )
                continue
            default_skills[key] = SkillRef(
                skill_name=str(raw_entry["skill_name"]),
                skill_version=str(raw_entry["skill_version"]),
            )

    # Parse cascade_rules.
    cascade_rules: list[Handoff] = []
    raw_cascades = raw.get("cascade_rules") or []
    if not isinstance(raw_cascades, list):
        errors.append("cascade_rules must be a list; ignoring")
    else:
        for idx, raw_rule in enumerate(raw_cascades):
            if not isinstance(raw_rule, dict):
                errors.append(f"cascade_rules[{idx}] must be a mapping")
                continue
            missing = (
                {"source_domain", "source_capability",
                 "target_domain", "target_capability", "reason"}
                - set(raw_rule.keys())
            )
            if missing:
                errors.append(
                    f"cascade_rules[{idx}] missing fields: {sorted(missing)}"
                )
                continue
            cascade_rules.append(Handoff(
                source_domain=str(raw_rule["source_domain"]),
                source_capability=str(raw_rule["source_capability"]),
                target_domain=str(raw_rule["target_domain"]),
                target_capability=str(raw_rule["target_capability"]),
                reason=str(raw_rule["reason"]),
            ))

    return HandoffsConfig(
        default_skill_per_capability=default_skills,
        cascade_rules=tuple(cascade_rules),
    ), errors


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve_route(
    subintent: dict,
    registry: Any,  # DomainRegistry from domain_registry.py
    handoffs: HandoffsConfig,
    agent_inventory: list[dict],
) -> ResolvedRoute | UnroutableSubIntent:
    """Turn one sub-intent into a ResolvedRoute or UnroutableSubIntent.

    Args:
      subintent: dict with keys {intent, domain, capability,
        confidence, status} — output shape from decompose_intent.v1.
      registry: DomainRegistry loaded from config/domains/.
      handoffs: HandoffsConfig from load_handoffs.
      agent_inventory: list of {instance_id, role, status} dicts.
        Operator supplies via the registry; resolver picks the first
        alive instance whose role matches the domain's entry_agent.

    Returns:
      ResolvedRoute on successful resolution.
      UnroutableSubIntent when any step fails.

    Resolution order:
      1. Sub-intent status must be 'routable' (decompose_intent set this)
      2. Domain must exist + be dispatchable
      3. Skill mapping must exist for (domain, capability)
      4. Agent inventory must contain at least one alive agent with
         the matching role
    """
    intent = subintent.get("intent", "")
    domain_id = subintent.get("domain", "")
    capability = subintent.get("capability", "")
    confidence = float(subintent.get("confidence", 0.0))
    status = subintent.get("status", "unknown")

    # Status gate — only 'routable' from decompose_intent passes.
    # Others (ambiguous, planned_domain, no_match) get surfaced to
    # operator unchanged.
    if status != "routable":
        # Mirror the status into the unroutable code so the operator
        # gets a unified taxonomy.
        code = _status_to_code(status)
        return UnroutableSubIntent(
            intent=intent, domain=domain_id, capability=capability,
            confidence=confidence, code=code,
            detail=f"decompose_intent marked subintent status={status!r}",
        )

    # Domain validation.
    domain = registry.by_id(domain_id)
    if domain is None:
        return UnroutableSubIntent(
            intent=intent, domain=domain_id, capability=capability,
            confidence=confidence,
            code=UNROUTABLE_DOMAIN_NOT_FOUND,
            detail=(
                f"domain {domain_id!r} not in registry; valid: "
                f"{sorted(registry.domain_ids())}"
            ),
        )
    if not domain.is_dispatchable:
        return UnroutableSubIntent(
            intent=intent, domain=domain_id, capability=capability,
            confidence=confidence,
            code=UNROUTABLE_DOMAIN_PLANNED,
            detail=(
                f"domain {domain_id!r} has status={domain.status!r}; "
                f"birth the entry agents first to make it dispatchable"
            ),
        )

    # Skill mapping.
    skill_ref = handoffs.default_skill_per_capability.get(
        (domain_id, capability),
    )
    if skill_ref is None:
        return UnroutableSubIntent(
            intent=intent, domain=domain_id, capability=capability,
            confidence=confidence,
            code=UNROUTABLE_NO_SKILL_MAPPING,
            detail=(
                f"no skill mapping for ({domain_id}, {capability}) "
                f"in handoffs.yaml; add a default_skill_per_capability "
                f"entry to enable routing"
            ),
        )

    # Agent inventory — pick the role that matches the capability.
    matching_role = None
    for ea in domain.entry_agents:
        if ea.capability == capability:
            matching_role = ea.role
            break
    if matching_role is None:
        # Loose fallback: try the first entry agent. Logged in the
        # detail so operators see the looseness.
        if domain.entry_agents:
            matching_role = domain.entry_agents[0].role

    if matching_role is None:
        return UnroutableSubIntent(
            intent=intent, domain=domain_id, capability=capability,
            confidence=confidence,
            code=UNROUTABLE_NO_ALIVE_AGENT,
            detail=(
                f"domain {domain_id!r} has no entry_agents; cannot "
                f"resolve a target instance"
            ),
        )

    target_instance_id = _pick_alive_instance(
        matching_role, agent_inventory,
    )
    if target_instance_id is None:
        return UnroutableSubIntent(
            intent=intent, domain=domain_id, capability=capability,
            confidence=confidence,
            code=UNROUTABLE_NO_ALIVE_AGENT,
            detail=(
                f"no alive agent with role={matching_role!r} in "
                f"the registry; birth one via /birth or fsf birth"
            ),
        )

    return ResolvedRoute(
        target_domain=domain_id,
        target_capability=capability,
        target_instance_id=target_instance_id,
        skill_ref=skill_ref,
        intent=intent,
        confidence=confidence,
        reason=(
            f"decompose_intent → resolve_route: "
            f"{domain_id}/{capability} via {matching_role} "
            f"({target_instance_id})"
        ),
    )


def apply_cascade_rules(
    decision: ResolvedRoute,
    handoffs: HandoffsConfig,
    registry: Any,
    agent_inventory: list[dict],
) -> list[ResolvedRoute | UnroutableSubIntent]:
    """Given an initial successful route, generate follow-on routes
    per the hardcoded cascade_rules in handoffs.yaml.

    Each cascade fires a NEW resolve_route call, so the cascade
    chain enforces the same skill-mapping + agent-inventory gates.
    A cascade that doesn't resolve cleanly returns
    UnroutableSubIntent — never silently dropped.

    Cascades don't recurse: A→B fires, but if B's cascade rules
    say B→C, that doesn't fire. Intentional: cascades are intended
    as one-step "PR → compliance pass" patterns, not
    recursive call graphs. Operators who want recursive cascades
    write them explicitly.
    """
    matched_rules = [
        rule for rule in handoffs.cascade_rules
        if rule.source_domain == decision.target_domain
        and rule.source_capability == decision.target_capability
    ]
    if not matched_rules:
        return []

    follow_ons: list[ResolvedRoute | UnroutableSubIntent] = []
    for rule in matched_rules:
        # Cascades inherit the original intent + confidence; the
        # operator's audit trail can join the cascade follow-on to
        # its source via the source_domain/source_capability fields.
        cascade_subintent = {
            "intent": decision.intent,
            "domain": rule.target_domain,
            "capability": rule.target_capability,
            "confidence": decision.confidence,
            "status": "routable",
        }
        result = resolve_route(
            cascade_subintent, registry, handoffs, agent_inventory,
        )
        # Mark successful cascade follow-ons with their provenance.
        if isinstance(result, ResolvedRoute):
            result = ResolvedRoute(
                target_domain=result.target_domain,
                target_capability=result.target_capability,
                target_instance_id=result.target_instance_id,
                skill_ref=result.skill_ref,
                intent=result.intent,
                confidence=result.confidence,
                reason=(
                    f"cascade from {rule.source_domain}/"
                    f"{rule.source_capability}: {rule.reason}"
                ),
                is_cascade=True,
                cascade_source_domain=rule.source_domain,
                cascade_source_capability=rule.source_capability,
            )
        follow_ons.append(result)
    return follow_ons


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _pick_alive_instance(
    role: str, agent_inventory: list[dict],
) -> Optional[str]:
    """Return the first alive instance_id whose role matches.

    Tie-breaking: takes the first match in agent_inventory order.
    Production callers should sort agent_inventory by creation
    time before calling so the orchestrator routes deterministically
    (oldest-stable agent gets preference) — pure function here
    doesn't impose that policy.
    """
    for entry in agent_inventory:
        if (
            entry.get("role") == role
            and entry.get("status") == "active"
        ):
            return entry.get("instance_id")
    return None


def _status_to_code(status: str) -> str:
    """Map decompose_intent statuses to UnroutableSubIntent codes."""
    if status == "ambiguous":
        return UNROUTABLE_LOW_CONFIDENCE
    if status == "planned_domain":
        return UNROUTABLE_DOMAIN_PLANNED
    if status == "no_match":
        return UNROUTABLE_DOMAIN_NOT_FOUND
    return f"unknown_status:{status}"
