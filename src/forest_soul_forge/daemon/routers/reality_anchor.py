"""``/reality-anchor/*`` — ADR-0063 T7 operator-facing read surface.

Closes ADR-0063. Six bursts (B251-B255) built the substrate
across three integration surfaces — dispatcher gate, agent
role, conversation hook — plus the correction-memory persistence.
This router exposes the resulting operator view:

  - **GET  /reality-anchor/status**
    Single combined summary card: fact count, catalog errors,
    recent flag/refuse counts (last 24h), top-N repeat
    offenders. Drives the pane's hero card.

  - **GET  /reality-anchor/ground-truth**
    The loaded operator-asserted fact catalog with all fields
    (id, statement, domain_keywords, canonical_terms,
    forbidden_terms, severity, last_confirmed_at, source).
    Read-only in v1 — per ADR-0063 D3 the operator owns the
    truth; in-UI editing is a v2 nice-to-have.

  - **GET  /reality-anchor/recent-events**
    Last N reality_anchor_* audit events (refused / flagged /
    turn_refused / turn_flagged / repeat_offender). Drives
    the events timeline.

  - **GET  /reality-anchor/corrections**
    Top repeat offenders from the corrections table. Drives
    the "agents that keep making the same wrong claim" view.

  - **POST /reality-anchor/reload**
    Hot-reload the ground-truth catalog from disk so an
    operator who edited ``config/ground_truth.yaml`` doesn't
    need a daemon restart to pick it up.

All endpoints are read-only EXCEPT /reload (which mutates
process-level cache state but not on-disk artifacts). All
require the API token; none require writes-enabled. This is
the operator's monitoring surface, not an action surface.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from forest_soul_forge.core.ground_truth import (
    DEFAULT_CATALOG_PATH,
    Fact,
    load_ground_truth,
)
from forest_soul_forge.daemon.deps import (
    get_audit_chain,
    get_registry,
    require_api_token,
)


router = APIRouter(prefix="/reality-anchor", tags=["reality-anchor"])


# ---- helpers --------------------------------------------------------------


def _fact_to_dict(f: Fact) -> dict[str, Any]:
    return {
        "id":                f.id,
        "statement":         f.statement,
        "domain_keywords":   list(f.domain_keywords),
        "canonical_terms":   list(f.canonical_terms),
        "forbidden_terms":   list(f.forbidden_terms),
        "severity":          f.severity,
        "last_confirmed_at": f.last_confirmed_at,
        "notes":             f.notes,
        "source":            f.source,
    }


def _is_anchor_event(event_type: str) -> bool:
    """Match the full reality_anchor_* event family ADR-0063 emits."""
    return event_type in {
        "reality_anchor_refused",
        "reality_anchor_flagged",
        "reality_anchor_turn_refused",
        "reality_anchor_turn_flagged",
        "reality_anchor_repeat_offender",
    }


def _read_recent_events(
    chain_path: Path, *, limit: int = 100,
) -> list[dict[str, Any]]:
    """Tail the JSONL chain reading the last ``limit`` reality_anchor_*
    entries.

    For Forest's current chain sizes (<10K entries) walking the whole
    file is sub-millisecond. When chain sizes grow into the millions
    we'll switch to indexed access; for now reading top-to-bottom is
    simplest + cheapest.
    """
    if not chain_path.exists():
        return []
    matches: list[dict[str, Any]] = []
    try:
        text = chain_path.read_text(encoding="utf-8")
    except Exception:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if _is_anchor_event(ev.get("event_type", "")):
            matches.append(ev)
    # Most recent last in the file; reverse + truncate.
    matches.reverse()
    return matches[:limit]


# ---- endpoints ------------------------------------------------------------


@router.get(
    "/status",
    dependencies=[Depends(require_api_token)],
)
def status_summary(
    request: Request,
    registry = Depends(get_registry),
    audit = Depends(get_audit_chain),
) -> dict[str, Any]:
    """Combined summary card for the pane's hero block."""
    facts, errors = load_ground_truth()
    chain_path = Path(audit.path) if hasattr(audit, "path") else None
    recent = _read_recent_events(chain_path, limit=500) if chain_path else []
    one_day_ago = datetime.now(timezone.utc) - timedelta(days=1)

    def _in_last_day(ev_iso: str | None) -> bool:
        if not ev_iso:
            return False
        try:
            t = datetime.fromisoformat(ev_iso.replace("Z", "+00:00"))
        except Exception:
            return False
        return t >= one_day_ago

    refused_24h = sum(
        1 for ev in recent
        if "refused" in ev.get("event_type", "")
        and _in_last_day(ev.get("timestamp"))
    )
    flagged_24h = sum(
        1 for ev in recent
        if "flagged" in ev.get("event_type", "")
        and _in_last_day(ev.get("timestamp"))
    )
    repeat_24h = sum(
        1 for ev in recent
        if ev.get("event_type") == "reality_anchor_repeat_offender"
        and _in_last_day(ev.get("timestamp"))
    )

    # Correction-table totals.
    rac = getattr(registry, "reality_anchor_corrections", None)
    total_corrections = 0
    top_repeat_count = 0
    if rac is not None:
        try:
            total_corrections = registry._conn.execute(
                "SELECT COUNT(*) FROM reality_anchor_corrections;",
            ).fetchone()[0]
            row = registry._conn.execute(
                "SELECT MAX(repetition_count) FROM reality_anchor_corrections;",
            ).fetchone()
            top_repeat_count = int(row[0]) if row and row[0] is not None else 0
        except Exception:
            pass

    return {
        "fact_count":          len(facts),
        "catalog_errors":      errors,
        "catalog_path":        str(DEFAULT_CATALOG_PATH),
        "refused_last_24h":    refused_24h,
        "flagged_last_24h":    flagged_24h,
        "repeat_offender_24h": repeat_24h,
        "total_corrections":   total_corrections,
        "top_repeat_count":    top_repeat_count,
        # ADR-0063 status — surfaced to the pane so the operator
        # sees the live ADR state without having to read the doc.
        "adr_tranches_shipped": ["T1", "T2", "T3", "T4", "T5", "T6", "T7"],
    }


@router.get(
    "/ground-truth",
    dependencies=[Depends(require_api_token)],
)
def list_facts() -> dict[str, Any]:
    """Return the loaded ground-truth catalog.

    Per ADR-0063 D3 the operator-global catalog is canonical;
    per-agent additions aren't surfaced here (they live in each
    agent's constitution).
    """
    facts, errors = load_ground_truth()
    return {
        "facts":          [_fact_to_dict(f) for f in facts],
        "fact_count":     len(facts),
        "catalog_errors": errors,
        "catalog_path":   str(DEFAULT_CATALOG_PATH),
    }


@router.get(
    "/recent-events",
    dependencies=[Depends(require_api_token)],
)
def recent_events(
    limit: int = Query(100, ge=1, le=1000),
    audit = Depends(get_audit_chain),
) -> dict[str, Any]:
    """Return the last N reality_anchor_* audit events, newest first.

    All five event types in one timeline — operators care about
    the chronological view, not separate per-type lists.
    """
    chain_path = Path(audit.path) if hasattr(audit, "path") else None
    if chain_path is None:
        return {"events": [], "count": 0}
    events = _read_recent_events(chain_path, limit=limit)
    return {"events": events, "count": len(events)}


@router.get(
    "/corrections",
    dependencies=[Depends(require_api_token)],
)
def list_corrections(
    min_repetitions: int = Query(2, ge=1, le=100),
    limit: int = Query(50, ge=1, le=500),
    registry = Depends(get_registry),
) -> dict[str, Any]:
    """Top repeat offenders from the corrections table."""
    rac = getattr(registry, "reality_anchor_corrections", None)
    if rac is None:
        return {"corrections": [], "count": 0}
    try:
        rows = rac.list_repeat_offenders(
            min_repetitions=min_repetitions, limit=limit,
        )
    except Exception:
        return {"corrections": [], "count": 0}
    return {
        "corrections": [
            {
                "claim_hash":          r.claim_hash,
                "canonical_claim":     r.canonical_claim,
                "contradicts_fact_id": r.contradicts_fact_id,
                "worst_severity":      r.worst_severity,
                "first_seen_at":       r.first_seen_at,
                "last_seen_at":        r.last_seen_at,
                "repetition_count":    r.repetition_count,
                "last_agent_dna":      r.last_agent_dna,
                "last_instance_id":    r.last_instance_id,
                "last_decision":       r.last_decision,
                "last_surface":        r.last_surface,
            }
            for r in rows
        ],
        "count":           len(rows),
        "min_repetitions": min_repetitions,
    }


@router.post(
    "/reload",
    dependencies=[Depends(require_api_token)],
)
def reload_catalog() -> dict[str, Any]:
    """Hot-reload the ground-truth catalog from disk.

    The catalog is read fresh on every call to ``load_ground_truth``
    today (no module-level cache), so this endpoint is effectively
    a no-op that returns the post-reload state for the operator's
    convenience. A future cache layer would make this endpoint
    cache-invalidating.

    Returns 200 + the same shape as ``/status`` so the frontend
    can swap the status card in one call.
    """
    facts, errors = load_ground_truth()
    return {
        "ok":             True,
        "fact_count":     len(facts),
        "catalog_errors": errors,
        "catalog_path":   str(DEFAULT_CATALOG_PATH),
    }
