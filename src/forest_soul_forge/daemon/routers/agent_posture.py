"""``/agents/{instance_id}/posture`` — ADR-0045 T2 (Burst 114b)
operator surface for the per-agent traffic-light dial.

GET surface (ungated, same posture as /audit + /healthz):
  GET /agents/{instance_id}/posture
       — return current posture (the agents.posture column).

Mutating surface (require_writes_enabled + require_api_token):
  POST /agents/{instance_id}/posture
       body: {posture, reason?}
       — flip the agent's posture. Emits agent_posture_changed.

Why this lives outside writes/: archive / birth / spawn / voice
are all artifact-mutating endpoints that route through the
artifact-authoritative discipline (ADR-0006). Posture changes are
NOT artifact mutations — they only touch the agents.posture column.
The posture lives alongside agents.status as runtime state, not
constitution / soul artifact state.

Audit emit: agent_posture_changed event payload includes
prior_posture so forensic queries answer 'what trust did this agent
have at time T' without needing to walk the whole chain.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from forest_soul_forge.daemon.deps import (
    require_api_token,
    require_writes_enabled,
)
from forest_soul_forge.registry import Registry


router = APIRouter(tags=["agent-posture"])


class PostureRequest(BaseModel):
    posture: str = Field(..., pattern="^(green|yellow|red)$")
    reason: str | None = Field(None, max_length=500)


def _registry(request: Request) -> Registry:
    reg = getattr(request.app.state, "registry", None)
    if reg is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="registry not initialized",
        )
    return reg


def _audit_chain(request: Request):
    chain = getattr(request.app.state, "audit_chain", None)
    if chain is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="audit chain not initialized",
        )
    return chain


def _get_agent_row(reg: Registry, instance_id: str):
    return reg._conn.execute(
        "SELECT dna, posture FROM agents WHERE instance_id = ?;",
        (instance_id,),
    ).fetchone()


@router.get("/agents/{instance_id}/posture")
def get_posture(instance_id: str, request: Request) -> dict[str, Any]:
    reg = _registry(request)
    row = _get_agent_row(reg, instance_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no agent named {instance_id!r}",
        )
    return {"instance_id": instance_id, "posture": row[1]}


@router.post(
    "/agents/{instance_id}/posture",
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
def set_posture(
    instance_id: str, body: PostureRequest, request: Request,
) -> dict[str, Any]:
    """Flip the agent's posture. Idempotent — setting the current
    value emits the audit event but doesn't change the column."""
    reg = _registry(request)
    chain = _audit_chain(request)
    write_lock = getattr(request.app.state, "write_lock", None)
    if write_lock is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="write lock not initialized",
        )

    row = _get_agent_row(reg, instance_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no agent named {instance_id!r}",
        )
    dna, prior_posture = row[0], row[1]
    operator_id = getattr(request.state, "operator_id", None)

    with write_lock:
        chain.append(
            "agent_posture_changed",
            {
                "instance_id": instance_id,
                "prior_posture": prior_posture,
                "new_posture": body.posture,
                "set_by": operator_id,
                "reason": body.reason,
            },
            agent_dna=dna,
        )
        reg._conn.execute(
            "UPDATE agents SET posture = ? WHERE instance_id = ?;",
            (body.posture, instance_id),
        )
        reg._conn.commit()

    return {
        "ok": True,
        "instance_id": instance_id,
        "prior_posture": prior_posture,
        "posture": body.posture,
    }
