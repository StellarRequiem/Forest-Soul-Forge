"""``/agents`` — read-only agent queries.

All endpoints here are read-only per the Phase 3 v1 scope. Write
endpoints (/birth, /spawn, /archive) land in a separate router when
their tests are ready.
"""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from forest_soul_forge.daemon.deps import get_registry
from forest_soul_forge.daemon.schemas import AgentListOut, AgentOut
from forest_soul_forge.registry import Registry, RegistryError
from forest_soul_forge.registry.registry import UnknownAgentError

router = APIRouter(prefix="/agents", tags=["agents"])


def _role_to_genre(request: Request, role: str) -> str | None:
    """Resolve the role's owning genre via app.state.genre_engine when
    one's loaded. Returns None if the engine isn't on (read-only
    daemon) or the role is unclaimed — the spec's ``genre`` field is
    Optional, so None is a clean shape."""
    engine = getattr(request.app.state, "genre_engine", None)
    if engine is None:
        return None
    try:
        return engine.genre_for(role).name
    except Exception:  # noqa: BLE001 — unclaimed role / unknown role both clean
        return None


def _to_out(request: Request, row) -> AgentOut:  # noqa: ANN001 — AgentRow is a frozen dataclass
    data = asdict(row)
    data["genre"] = _role_to_genre(request, data.get("role", ""))
    return AgentOut(**data)


@router.get("", response_model=AgentListOut)
async def list_agents(
    request: Request,
    role: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    registry: Registry = Depends(get_registry),
) -> AgentListOut:
    rows = registry.list_agents(role=role, status=status_filter)
    return AgentListOut(
        count=len(rows), agents=[_to_out(request, r) for r in rows],
    )


@router.get("/by-dna/{dna}", response_model=AgentListOut)
async def list_by_dna(
    dna: str,
    request: Request,
    registry: Registry = Depends(get_registry),
) -> AgentListOut:
    rows = registry.get_agent_by_dna(dna)
    return AgentListOut(
        count=len(rows), agents=[_to_out(request, r) for r in rows],
    )


@router.get("/{instance_id}", response_model=AgentOut)
async def get_agent(
    instance_id: str,
    request: Request,
    registry: Registry = Depends(get_registry),
) -> AgentOut:
    try:
        return _to_out(request, registry.get_agent(instance_id))
    except UnknownAgentError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown agent: {e}",
        ) from e


@router.get("/{instance_id}/ancestors", response_model=AgentListOut)
async def get_ancestors(
    instance_id: str,
    request: Request,
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
    return AgentListOut(
        count=len(rows), agents=[_to_out(request, r) for r in rows],
    )


@router.get("/{instance_id}/descendants", response_model=AgentListOut)
async def get_descendants(
    instance_id: str,
    request: Request,
    registry: Registry = Depends(get_registry),
) -> AgentListOut:
    try:
        registry.get_agent(instance_id)
    except UnknownAgentError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
        ) from e
    rows = registry.get_descendants(instance_id)
    return AgentListOut(
        count=len(rows), agents=[_to_out(request, r) for r in rows],
    )
