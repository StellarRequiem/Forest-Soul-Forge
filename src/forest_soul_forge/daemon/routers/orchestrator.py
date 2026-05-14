"""``/orchestrator/*`` — ADR-0067 T8 operator-facing read surface.

The orchestrator's monitoring surface. Read-only endpoints that
drive the frontend Orchestrator pane (T7, queued):

  - **GET /orchestrator/status**
    Single combined summary: domain count + dispatchable count,
    handoffs loaded (skill-mapping count + cascade-rule count),
    recent domain_routed count (last 24h), top domains by routes
    received. Drives the pane's hero card.

  - **GET /orchestrator/domains**
    Full domain manifest list: each domain's id, name, status,
    description, entry agents, capabilities, example intents,
    dependencies, handoff targets. Drives the domain table.

  - **GET /orchestrator/handoffs**
    Loaded handoff config: skill mappings + cascade rules. Read-
    only; operator edits config/handoffs.yaml directly + reloads.

  - **GET /orchestrator/recent-routes**
    Last N domain_routed audit events. Drives the routing
    timeline. Same pattern as reality_anchor/recent-events.

  - **POST /orchestrator/reload**
    Hot-reload the domain registry + handoffs from disk. Mirrors
    /reality-anchor/reload — operator edits YAML, posts reload,
    no daemon restart needed.

All endpoints are read-only EXCEPT /reload (which mutates
process-level cache state but not on-disk artifacts). All require
the API token.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from forest_soul_forge.core.domain_registry import (
    Domain,
    DomainRegistry,
    DomainRegistryError,
    load_domain_registry,
)
from forest_soul_forge.core.routing_engine import (
    HandoffsConfig,
    HandoffsError,
    load_handoffs,
)
from forest_soul_forge.daemon.deps import (
    get_audit_chain,
    require_api_token,
)


router = APIRouter(prefix="/orchestrator", tags=["orchestrator"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _domain_to_dict(d: Domain) -> dict[str, Any]:
    return {
        "domain_id":              d.domain_id,
        "name":                   d.name,
        "status":                 d.status,
        "description":            d.description,
        "entry_agents":           [
            {"role": ea.role, "capability": ea.capability}
            for ea in d.entry_agents
        ],
        "capabilities":           list(d.capabilities),
        "example_intents":        list(d.example_intents),
        "depends_on_substrate":   list(d.depends_on_substrate),
        "depends_on_connectors":  list(d.depends_on_connectors),
        "handoff_targets":        list(d.handoff_targets),
        "notes":                  d.notes,
        "is_dispatchable":        d.is_dispatchable,
    }


def _load_registry_or_502() -> tuple[DomainRegistry, list[str]]:
    """Helper that turns DomainRegistryError into a clean 502 so
    operators see the misconfig immediately rather than a 500."""
    try:
        registry, errors = load_domain_registry()
        return registry, errors
    except DomainRegistryError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"domain registry not loadable: {e}",
        )


def _load_handoffs_or_502() -> tuple[HandoffsConfig, list[str]]:
    try:
        cfg, errors = load_handoffs()
        return cfg, errors
    except HandoffsError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"handoffs config not loadable: {e}",
        )


def _is_routing_event(event_type: str) -> bool:
    """ADR-0067 T3 event types. Currently single — domain_routed."""
    return event_type == "domain_routed"


def _read_recent_routes(
    chain, *, limit: int = 100, search_window: int = 5000,
) -> list[dict[str, Any]]:
    """Last ``limit`` domain_routed events from the chain. Same
    tail-window strategy as /reality-anchor/recent-events (B256 lesson:
    streaming reader, not full file scan, to avoid threadpool saturation
    on the operator-facing pane).
    """
    if limit <= 0 or search_window <= 0:
        return []
    try:
        entries = chain.tail(search_window)  # newest-first
    except Exception:
        return []
    matches: list[dict[str, Any]] = []
    for e in entries:
        if _is_routing_event(getattr(e, "event_type", "")):
            matches.append({
                "seq":        getattr(e, "seq", None),
                "timestamp":  getattr(e, "timestamp", None),
                "event_type": getattr(e, "event_type", None),
                "event_data": getattr(e, "event_data", {}),
                "agent_dna":  getattr(e, "agent_dna", None),
                "entry_hash": getattr(e, "entry_hash", None),
            })
            if len(matches) >= limit:
                break
    return matches


def _count_recent_routes(
    chain, *, window_hours: int = 24,
) -> tuple[int, dict[str, int]]:
    """Return (total_count, count_per_target_domain) for the
    last ``window_hours`` of domain_routed events."""
    routes = _read_recent_routes(chain, limit=10_000, search_window=20_000)
    if not routes:
        return 0, {}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    by_domain: dict[str, int] = {}
    total = 0
    for r in routes:
        ts_str = r.get("timestamp")
        if not ts_str:
            continue
        try:
            # ISO 8601 with Z suffix or +00:00 — both common.
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if ts < cutoff:
            continue
        total += 1
        target = (r.get("event_data") or {}).get("target_domain")
        if target:
            by_domain[target] = by_domain.get(target, 0) + 1
    return total, by_domain


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/status",
    dependencies=[Depends(require_api_token)],
)
async def status_endpoint(audit=Depends(get_audit_chain)):
    """Single combined status card for the orchestrator."""
    registry, registry_errors = _load_registry_or_502()
    handoffs, handoffs_errors = _load_handoffs_or_502()

    routes_24h, by_domain_24h = _count_recent_routes(
        audit, window_hours=24,
    )
    # Top 5 domains by route count in the last 24h.
    top_domains = sorted(
        by_domain_24h.items(),
        key=lambda kv: kv[1],
        reverse=True,
    )[:5]

    return {
        "schema_version": 1,
        "registry": {
            "total_domains":         len(registry.domains),
            "dispatchable_domains":  len(registry.dispatchable_ids()),
            "planned_domains":       len([
                d for d in registry.domains if d.status == "planned"
            ]),
            "domain_ids":            list(registry.domain_ids()),
            "errors":                registry_errors,
        },
        "handoffs": {
            "skill_mapping_count":   len(handoffs.default_skill_per_capability),
            "cascade_rule_count":    len(handoffs.cascade_rules),
            "errors":                handoffs_errors,
        },
        "routing_activity_24h": {
            "total_routes":   routes_24h,
            "by_target_domain": dict(top_domains),
        },
    }


@router.get(
    "/domains",
    dependencies=[Depends(require_api_token)],
)
async def domains_endpoint():
    """Full domain manifest list. Drives the operator's domain table."""
    registry, errors = _load_registry_or_502()
    return {
        "schema_version": 1,
        "domains":         [_domain_to_dict(d) for d in registry.domains],
        "errors":          errors,
    }


@router.get(
    "/handoffs",
    dependencies=[Depends(require_api_token)],
)
async def handoffs_endpoint():
    """Loaded handoff config: skill mappings + cascade rules."""
    handoffs, errors = _load_handoffs_or_502()
    return {
        "schema_version": 1,
        "default_skill_per_capability": [
            {
                "domain":         dom,
                "capability":     cap,
                "skill_name":     ref.skill_name,
                "skill_version":  ref.skill_version,
            }
            for (dom, cap), ref in handoffs.default_skill_per_capability.items()
        ],
        "cascade_rules": [
            {
                "source_domain":      r.source_domain,
                "source_capability":  r.source_capability,
                "target_domain":      r.target_domain,
                "target_capability":  r.target_capability,
                "reason":             r.reason,
            }
            for r in handoffs.cascade_rules
        ],
        "errors": errors,
    }


@router.get(
    "/recent-routes",
    dependencies=[Depends(require_api_token)],
)
async def recent_routes_endpoint(
    limit: int = Query(100, ge=1, le=1000),
    audit=Depends(get_audit_chain),
):
    """Last N domain_routed events. Drives the routing timeline."""
    routes = _read_recent_routes(audit, limit=limit)
    return {
        "schema_version": 1,
        "count":           len(routes),
        "routes":          routes,
    }


@router.post(
    "/reload",
    dependencies=[Depends(require_api_token)],
)
async def reload_endpoint():
    """Hot-reload domains + handoffs from disk.

    Mirrors /reality-anchor/reload. Operator edits
    config/domains/*.yaml or config/handoffs.yaml + posts reload;
    no daemon restart required.
    """
    registry, reg_errors = _load_registry_or_502()
    handoffs, ho_errors = _load_handoffs_or_502()
    return {
        "schema_version":      1,
        "domains_loaded":      len(registry.domains),
        "skill_mappings":      len(handoffs.default_skill_per_capability),
        "cascade_rules":       len(handoffs.cascade_rules),
        "registry_errors":     reg_errors,
        "handoffs_errors":     ho_errors,
    }
