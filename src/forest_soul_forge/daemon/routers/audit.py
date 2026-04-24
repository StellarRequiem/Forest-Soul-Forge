"""``/audit`` — read-only audit chain mirror.

Source of truth is ``audit/chain.jsonl`` on disk. The registry mirrors
it; this router serves the mirror so callers don't have to walk the file
themselves. For tamper verification, use ``scripts/verify_audit_chain.py``
or the upcoming ``/audit/verify`` endpoint (Phase 3 write tier).
"""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query, status

from forest_soul_forge.daemon.deps import get_registry
from forest_soul_forge.daemon.schemas import AuditEventOut, AuditListOut
from forest_soul_forge.registry import Registry
from forest_soul_forge.registry.registry import UnknownAgentError

router = APIRouter(prefix="/audit", tags=["audit"])


def _to_out(row) -> AuditEventOut:  # noqa: ANN001 — AuditRow is a frozen dataclass
    return AuditEventOut(**asdict(row))


@router.get("/tail", response_model=AuditListOut)
async def audit_tail(
    n: int = Query(default=100, ge=1, le=10_000),
    registry: Registry = Depends(get_registry),
) -> AuditListOut:
    rows = registry.audit_tail(n)
    return AuditListOut(count=len(rows), events=[_to_out(r) for r in rows])


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
