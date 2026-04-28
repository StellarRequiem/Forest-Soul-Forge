"""``/audit`` — read-only audit chain mirror.

Source of truth is the canonical JSONL at ``data/audit_chain.jsonl``.
Per ADR-0006 the registry is a derived index over that file. This router
reflects the split:

* ``/audit/tail`` reads the canonical JSONL directly via :meth:`AuditChain.tail`.
  Runtime events (tool dispatch, agent delegation, skill steps) appear
  immediately — the registry's ``audit_events`` table only mirrors what's
  ingested at lifespan, so a registry-only tail would silently drop every
  live event between process boots.
* ``/audit/agent/{instance_id}`` and ``/audit/by-dna/{dna}`` keep using
  the registry — they need indexed lookup, which is exactly what the
  derived index is for.

For tamper verification, use ``scripts/verify_audit_chain.py`` or the
upcoming ``/audit/verify`` endpoint (Phase 3 write tier).
"""
from __future__ import annotations

import json
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query, status

from forest_soul_forge.core.audit_chain import AuditChain, ChainEntry
from forest_soul_forge.daemon.deps import get_audit_chain, get_registry
from forest_soul_forge.daemon.schemas import AuditEventOut, AuditListOut
from forest_soul_forge.registry import Registry
from forest_soul_forge.registry.registry import UnknownAgentError

router = APIRouter(prefix="/audit", tags=["audit"])


def _to_out(row) -> AuditEventOut:  # noqa: ANN001 — AuditRow is a frozen dataclass
    return AuditEventOut(**asdict(row))


def _chain_entry_to_out(entry: ChainEntry) -> AuditEventOut:
    """Map a canonical ChainEntry to the API's AuditEventOut shape.

    ``instance_id`` is conventionally embedded inside ``event_data`` by
    runtime emitters (dispatcher, delegator, skill runtime) — pulling it
    up here gives the same field shape callers see from the registry
    mirror, so the response schema doesn't fork between the two paths.

    ``event_json`` matches the registry's canonical-ish dump (sorted keys,
    no whitespace) so two events compared across the two endpoints
    serialize identically.
    """
    instance_id = entry.event_data.get("instance_id")
    return AuditEventOut(
        seq=entry.seq,
        timestamp=entry.timestamp,
        agent_dna=entry.agent_dna,
        instance_id=str(instance_id) if instance_id is not None else None,
        event_type=entry.event_type,
        event_json=json.dumps(entry.event_data, sort_keys=True, separators=(",", ":")),
        entry_hash=entry.entry_hash,
    )


@router.get("/tail", response_model=AuditListOut)
async def audit_tail(
    n: int = Query(default=100, ge=1, le=10_000),
    chain: AuditChain = Depends(get_audit_chain),
) -> AuditListOut:
    """Return the most recent ``n`` audit events, newest first.

    Reads the canonical JSONL directly so runtime events are visible
    without waiting for a lifespan restart to re-ingest them.
    """
    entries = chain.tail(n)
    return AuditListOut(
        count=len(entries),
        events=[_chain_entry_to_out(e) for e in entries],
    )


@router.get("/agent/{instance_id}", response_model=AuditListOut)
async def audit_for_agent(
    instance_id: str,
    registry: Registry = Depends(get_registry),
) -> AuditListOut:
    # Confirm the agent exists so a typo produces a clean 404 rather than
    # an empty list that could be misread as "nothing happened".
    try:
        registry.get_agent(instance_id)
    except UnknownAgentError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
        ) from e
    rows = registry.audit_for_agent(instance_id=instance_id)
    return AuditListOut(count=len(rows), events=[_to_out(r) for r in rows])


@router.get("/by-dna/{dna}", response_model=AuditListOut)
async def audit_by_dna(
    dna: str,
    registry: Registry = Depends(get_registry),
) -> AuditListOut:
    rows = registry.audit_for_agent(dna=dna)
    return AuditListOut(count=len(rows), events=[_to_out(r) for r in rows])
