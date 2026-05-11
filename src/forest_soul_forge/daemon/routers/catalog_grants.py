"""``/agents/{instance_id}/tools/grant(s)`` — ADR-0060 T3 operator
surface (Burst 220).

Runtime grants of catalog-tool access to a born agent. Sister of
``plugin_grants.py`` (ADR-0043 follow-up #2) but keyed on
``(tool_name, tool_version)`` instead of ``plugin_name``.

GET surface (ungated, same posture as /plugin-grants GET):
  GET /agents/{instance_id}/tools/grants?history=false
      — list active grants. ``?history=true`` includes revoked.

Mutating surface (require_writes_enabled + require_api_token):
  POST /agents/{instance_id}/tools/grant
       body: {tool_name, tool_version, trust_tier?, reason?}
       — issue (or re-issue) a grant. trust_tier defaults to
         'yellow'; operator must pass 'green' explicitly per
         ADR-0060 D4.
  DELETE /agents/{instance_id}/tools/grant/{tool_name}/{tool_version}
         optional body: {reason?}
         — revoke. Idempotent: revoking an already-revoked grant
         returns 200 {ok:true, no_op:true} rather than 404 (per
         ADR-0060 D3). 404 only when the agent doesn't exist.

Both mutations hold ``app.state.write_lock`` (single-writer SQLite
discipline). They emit ``agent_tool_granted`` /
``agent_tool_revoked`` audit events with the trust_tier and the
operator-supplied reason in the payload.

The POST validates that the (tool_name, tool_version) exists in
``app.state.tool_catalog`` per ADR-0060 D5: "not a way to grant
tools that don't exist." Unknown tool refs return 400.
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
from forest_soul_forge.registry.tables.catalog_grants import CatalogGrant


router = APIRouter(tags=["catalog-grants"])


# ---- request / response models -----------------------------------------

class CatalogGrantRequest(BaseModel):
    tool_name: str = Field(..., min_length=1, max_length=80)
    tool_version: str = Field(..., min_length=1, max_length=16)
    trust_tier: str = Field("yellow", pattern="^(green|yellow|red)$")
    reason: str | None = Field(None, max_length=500)


class CatalogRevokeRequest(BaseModel):
    reason: str | None = Field(None, max_length=500)


def _serialize_grant(g: CatalogGrant) -> dict[str, Any]:
    return {
        "instance_id": g.instance_id,
        "tool_name": g.tool_name,
        "tool_version": g.tool_version,
        "tool_key": g.tool_key,
        "trust_tier": g.trust_tier,
        "granted_at_seq": g.granted_at_seq,
        "granted_by": g.granted_by,
        "granted_at": g.granted_at,
        "revoked_at_seq": g.revoked_at_seq,
        "revoked_at": g.revoked_at,
        "revoked_by": g.revoked_by,
        "reason": g.reason,
        "is_active": g.is_active,
    }


# ---- helpers -----------------------------------------------------------

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


def _agent_dna(reg: Registry, instance_id: str) -> str | None:
    row = reg._conn.execute(
        "SELECT dna FROM agents WHERE instance_id = ?;",
        (instance_id,),
    ).fetchone()
    return row[0] if row else None


def _validate_tool_in_catalog(request: Request, name: str, version: str) -> None:
    """ADR-0060 D5: grants only exist for catalog tools. Refuse 400
    if the (name, version) doesn't resolve. None catalog (test
    context) is treated as permissive — no validation possible."""
    catalog = getattr(request.app.state, "tool_catalog", None)
    if catalog is None:
        return  # test context — skip validation
    key = f"{name}.v{version}"
    tools = getattr(catalog, "tools", None) or {}
    if key not in tools:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"tool {key} not in catalog — grants can only reference "
                "tools registered at lifespan. If you just forged this "
                "tool, restart the daemon so the catalog picks it up."
            ),
        )


# ---- routes ------------------------------------------------------------

@router.get("/agents/{instance_id}/tools/grants")
def list_catalog_grants(
    instance_id: str, request: Request, history: bool = False,
) -> dict[str, Any]:
    """List grants for an agent. Default active-only;
    ``?history=true`` includes revoked rows for audit views."""
    reg = _registry(request)
    if _agent_dna(reg, instance_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no agent named {instance_id!r}",
        )
    grants = (
        reg.catalog_grants.list_all(instance_id)
        if history else reg.catalog_grants.list_active(instance_id)
    )
    return {
        "instance_id": instance_id,
        "count": len(grants),
        "grants": [_serialize_grant(g) for g in grants],
    }


@router.post(
    "/agents/{instance_id}/tools/grant",
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
def grant_catalog_tool(
    instance_id: str, body: CatalogGrantRequest, request: Request,
) -> dict[str, Any]:
    """Issue (or re-issue) a catalog-tool grant for the agent.

    Holds ``app.state.write_lock``. Emits ``agent_tool_granted``.
    Validates that ``(tool_name, tool_version)`` is in the live tool
    catalog (ADR-0060 D5) before the chain append — refuses 400
    for unknown refs so a hallucinated grant can't sit in the chain.
    """
    reg = _registry(request)
    chain = _audit_chain(request)
    write_lock = getattr(request.app.state, "write_lock", None)
    if write_lock is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="write lock not initialized",
        )

    dna = _agent_dna(reg, instance_id)
    if dna is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no agent named {instance_id!r}",
        )

    # ADR-0060 D5: validate the tool exists in the catalog.
    _validate_tool_in_catalog(request, body.tool_name, body.tool_version)

    operator_id = getattr(request.state, "operator_id", None)

    with write_lock:
        # Append-then-record discipline. The chain entry's seq is
        # what the table row references for the granted_at_seq
        # back-link — operator querying "what was granted at seq N?"
        # uses this seq.
        entry = chain.append(
            "agent_tool_granted",
            {
                "instance_id": instance_id,
                "tool_name": body.tool_name,
                "tool_version": body.tool_version,
                "trust_tier": body.trust_tier,
                "granted_by": operator_id,
                "reason": body.reason,
            },
            agent_dna=dna,
        )
        reg.catalog_grants.grant(
            instance_id=instance_id,
            tool_name=body.tool_name,
            tool_version=body.tool_version,
            trust_tier=body.trust_tier,
            granted_at_seq=entry.seq,
            granted_by=operator_id,
            reason=body.reason,
            when=entry.timestamp,
        )

    grant = reg.catalog_grants.get_active(
        instance_id, body.tool_name, body.tool_version,
    )
    return {"ok": True, "grant": _serialize_grant(grant)}


@router.delete(
    "/agents/{instance_id}/tools/grant/{tool_name}/{tool_version}",
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
def revoke_catalog_tool(
    instance_id: str,
    tool_name: str,
    tool_version: str,
    request: Request,
    body: CatalogRevokeRequest | None = None,
) -> dict[str, Any]:
    """Revoke a grant. Per ADR-0060 D3, this is IDEMPOTENT — revoking
    an already-revoked grant returns 200 ``{ok:true, no_op:true}``
    rather than 404. 404 only when the agent itself doesn't exist.
    """
    reg = _registry(request)
    chain = _audit_chain(request)
    write_lock = getattr(request.app.state, "write_lock", None)
    if write_lock is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="write lock not initialized",
        )

    dna = _agent_dna(reg, instance_id)
    if dna is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no agent named {instance_id!r}",
        )

    operator_id = getattr(request.state, "operator_id", None)
    reason = body.reason if body else None

    with write_lock:
        # Take the existing grant first so we can record its original
        # granted_at_seq in the revoked event (lineage). If there's
        # no active grant, the chain emission is still informative —
        # "operator tried to revoke at seq N; no active row" — but
        # we don't emit on the no-op path to keep the chain quieter.
        existing = reg.catalog_grants.get_active(
            instance_id, tool_name, tool_version,
        )
        if existing is None:
            return {"ok": True, "no_op": True, "reason": "no active grant"}

        entry = chain.append(
            "agent_tool_revoked",
            {
                "instance_id": instance_id,
                "tool_name": tool_name,
                "tool_version": tool_version,
                "revoked_by": operator_id,
                "reason": reason,
                "granted_at_seq": existing.granted_at_seq,
                "trust_tier": existing.trust_tier,
            },
            agent_dna=dna,
        )
        reg.catalog_grants.revoke(
            instance_id=instance_id,
            tool_name=tool_name,
            tool_version=tool_version,
            revoked_at_seq=entry.seq,
            revoked_by=operator_id,
            reason=reason,
            when=entry.timestamp,
        )

    return {
        "ok": True,
        "revoked": f"{tool_name}.v{tool_version}",
        "revoked_at_seq": entry.seq,
    }
