"""``/agents`` — read-only agent queries.

All endpoints here are read-only per the Phase 3 v1 scope. Write
endpoints (/birth, /spawn, /archive) land in a separate router when
their tests are ready.
"""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query, status

from forest_soul_forge.daemon.deps import get_registry
from forest_soul_forge.daemon.schemas import AgentListOut, AgentOut
from forest_soul_forge.registry import Registry, RegistryError
from forest_soul_forge.registry.registry import UnknownAgentError

router = APIRouter(prefix="/agents", tags=["agents"])


def _to_out(row) -> AgentOut:  # noqa: ANN001 — AgentRow is a frozen dataclass
    return AgentOut(**asdict(row))


@router.get("", response_model=AgentListOut)
async def list_agents(
    role: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    registry: Registry = Depends(get_registry),
) -> AgentListOut:
    rows = registry.list_agents(role=role, status=status_filter)
    return AgentListOut(count=len(rows), agents=[_to_out(r) for r in rows])


@router.get("/by-dna/{dna}", response_model=AgentListOut)
async def list_by_dna(
    dna: str,
    registry: Registry = Depends(get_registry),
) -> AgentListOut:
    rows = registry.get_agent_by_dna(dna)
    return AgentListOut(count=len(rows), agents=[_to_out(r) for r in rows])


@router.get("/{instance_id}", response_model=AgentOut)
async def get_agent(
    instance_id: str,
    registry: Registry = Depends(get_registry),
) -> AgentOut:
    try:
        return _to_out(registry.get_agent(instance_id))
    except UnknownAgentError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown agent: {e}",
        ) from e


@router.get("/{instance_id}/ancestors", response_model=AgentListOut)
async def get_ancestors(
    instance_id: str,
    registry: Registry = Depends(get_registry),
) -> AgentListOut:
    # get_ancestors doesn't raise on unknown id — it returns []. To
    # distinguish "no ancestors" from "unknown agent", probe get_agent
    # first.
    try:
        registry.get_agent(instance_id)
    except UnknownAgentError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
        ) from e
    rows = registry.get_ancestors(instance_id)
    return AgentListOut(count=len(rows), agents=[_to_out(r) for r in rows])


@router.get("/{instance_id}/descendants", response_model=AgentListOut)
async def get_descendants(
    instance_id: str,
    registry: Registry = Depends(get_registry),
) -> AgentListOut:
    try:
        registry.get_agent(instance_id)
    except UnknownAgentError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
        ) from e
    rows = registry.get_descendants(instance_id)
    return AgentListOut(count=len(rows), agents=[_to_out(r) for r in rows])
