"""``/agents/{instance_id}/plugin-grants`` — ADR-0043 follow-up #2
operator surface (Burst 113b).

GET surface (ungated, same posture as /audit + /healthz +
/plugins):
  GET /agents/{instance_id}/plugin-grants
       — list active + historical grants for the agent.

Mutating surface (require_writes_enabled + require_api_token,
same posture as /plugins write endpoints + the writes routes):
  POST /agents/{instance_id}/plugin-grants
       body: {plugin_name, trust_tier?, reason?}
       — issue (or re-issue) a grant. trust_tier defaults to
       'yellow' (current behavior). trust_tier values are
       forward-compat per ADR-0045; T3 / Burst 115 turns on
       enforcement.
  DELETE /agents/{instance_id}/plugin-grants/{plugin_name}
       optional body: {reason?}
       — revoke an active grant. Returns 404 if no active
       grant exists for the (agent, plugin) pair.

Both mutations hold ``app.state.write_lock`` (single-writer
SQLite discipline). They emit ``agent_plugin_granted`` /
``agent_plugin_revoked`` audit events with the grant's
trust_tier and the operator-supplied reason in the payload.

Why ``agent_plugin_granted`` carries the trust_tier:
ADR-0045 T3 / Burst 115 will start enforcing per-grant
trust_tier in PostureGateStep. The audit chain is the source
of truth for the grant's trust at the moment it was issued —
operators querying "was this agent allowed to call X without
gating at time T?" need the tier in the event payload, not
just in the table. Storing in both places (table = current
state, chain = history) is the same posture as every other
grant-shaped event.
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
from forest_soul_forge.registry.tables.plugin_grants import PluginGrant


router = APIRouter(tags=["plugin-grants"])


# ---- request / response models -----------------------------------------

class GrantRequest(BaseModel):
    plugin_name: str = Field(..., min_length=1, max_length=80)
    trust_tier: str = Field("yellow", pattern="^(green|yellow|red)$")
    reason: str | None = Field(None, max_length=500)


class RevokeRequest(BaseModel):
    reason: str | None = Field(None, max_length=500)


def _serialize_grant(g: PluginGrant) -> dict[str, Any]:
    return {
        "instance_id": g.instance_id,
        "plugin_name": g.plugin_name,
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
    """Pull the agent's dna for audit-event tagging. Returns None if
    the agent doesn't exist (caller decides how to handle)."""
    row = reg._conn.execute(
        "SELECT dna FROM agents WHERE instance_id = ?;",
        (instance_id,),
    ).fetchone()
    return row[0] if row else None


# ---- routes ------------------------------------------------------------

@router.get("/agents/{instance_id}/plugin-grants")
def list_plugin_grants(
    instance_id: str, request: Request, history: bool = False,
) -> dict[str, Any]:
    """List grants for an agent. Default returns active only;
    ``?history=true`` includes revoked rows for forensic views.
    Ungated read."""
    reg = _registry(request)
    if _agent_dna(reg, instance_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no agent named {instance_id!r}",
        )
    grants = (
        reg.plugin_grants.list_all(instance_id)
        if history else reg.plugin_grants.list_active(instance_id)
    )
    return {
        "instance_id": instance_id,
        "count": len(grants),
        "grants": [_serialize_grant(g) for g in grants],
    }


@router.post(
    "/agents/{instance_id}/plugin-grants",
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
def grant_plugin(
    instance_id: str, body: GrantRequest, request: Request,
) -> dict[str, Any]:
    """Issue (or re-issue) a plugin grant for the agent.

    Holds ``app.state.write_lock``. Emits ``agent_plugin_granted``.
    The trust_tier is recorded but only enforced once ADR-0045 T3 /
    Burst 115 lands; the field is forward-compat storage today.
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

    # Operator id from the bearer token if available; daemon's auth
    # middleware sets request.state.operator_id when one is configured.
    operator_id = getattr(request.state, "operator_id", None)

    with write_lock:
        # Append-then-record discipline: chain entry is the commit
        # point. seq from the entry is what the table row references.
        entry = chain.append(
            "agent_plugin_granted",
            {
                "instance_id": instance_id,
                "plugin_name": body.plugin_name,
                "trust_tier": body.trust_tier,
                "granted_by": operator_id,
                "reason": body.reason,
            },
            agent_dna=dna,
        )
        reg.plugin_grants.grant(
            instance_id=instance_id,
            plugin_name=body.plugin_name,
            trust_tier=body.trust_tier,
            granted_at_seq=entry.seq,
            granted_by=operator_id,
            reason=body.reason,
            when=entry.timestamp,
        )

    grant = reg.plugin_grants.get_active(instance_id, body.plugin_name)
    return {"ok": True, "grant": _serialize_grant(grant)}


@router.delete(
    "/agents/{instance_id}/plugin-grants/{plugin_name}",
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
def revoke_plugin(
    instance_id: str, plugin_name: str, request: Request,
    body: RevokeRequest | None = None,
) -> dict[str, Any]:
    """Revoke an active grant. 404 when no active grant exists.
    Holds the write lock. Emits ``agent_plugin_revoked``."""
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

    # Pre-check the grant exists. We need the table info for the
    # audit event payload (we want to record the trust_tier the grant
    # was at when it was revoked, since operators querying the chain
    # later won't know what it was).
    existing = reg.plugin_grants.get_active(instance_id, plugin_name)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"no active grant for plugin {plugin_name!r} on agent "
                f"{instance_id!r}"
            ),
        )

    with write_lock:
        entry = chain.append(
            "agent_plugin_revoked",
            {
                "instance_id": instance_id,
                "plugin_name": plugin_name,
                "prior_trust_tier": existing.trust_tier,
                "revoked_by": operator_id,
                "reason": reason,
            },
            agent_dna=dna,
        )
        affected = reg.plugin_grants.revoke(
            instance_id=instance_id,
            plugin_name=plugin_name,
            revoked_at_seq=entry.seq,
            revoked_by=operator_id,
            reason=reason,
            when=entry.timestamp,
        )
        # Should always be True since we pre-checked, but defensive.
        if not affected:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="grant disappeared between pre-check and revoke",
            )

    return {"ok": True, "instance_id": instance_id, "plugin_name": plugin_name}
