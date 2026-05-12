"""``/agents/{instance_id}/plugin-grants`` — ADR-0043 follow-up #2
operator surface (Burst 113b) + ADR-0053 T3 per-tool extension
(Burst 238).

GET surface (ungated, same posture as /audit + /healthz +
/plugins):
  GET /agents/{instance_id}/plugin-grants
       — list active + historical grants for the agent. Rows
       include ``tool_name`` (null for plugin-level grants,
       string for per-tool grants per ADR-0053 T2).

Mutating surface (require_writes_enabled + require_api_token,
same posture as /plugins write endpoints + the writes routes):
  POST /agents/{instance_id}/plugin-grants
       body: {plugin_name, trust_tier?, tool_name?, reason?}
       — issue (or re-issue) a grant. ``tool_name`` is optional;
       omit (or null) for a plugin-level grant covering all the
       manifest's tools (ADR-0043 original semantic); pass a
       non-null value for a per-tool grant covering only that
       one tool (ADR-0053 D2).
  DELETE /agents/{instance_id}/plugin-grants/{plugin_name}
       optional body: {reason?}
       — revoke the active plugin-level grant for (agent,
       plugin). Returns 404 if no plugin-level active grant
       exists at that pair.
  DELETE /agents/{instance_id}/plugin-grants/{plugin_name}/tools/{tool_name}
       optional body: {reason?}
       — revoke ONLY the per-tool grant at the (agent, plugin,
       tool) triple. Leaves the plugin-level grant (if any)
       intact. Returns 404 if no per-tool active grant exists.

Both mutations hold ``app.state.write_lock`` (single-writer
SQLite discipline). They emit ``agent_plugin_granted`` /
``agent_plugin_revoked`` audit events with the grant's
trust_tier and operator-supplied reason. Per ADR-0053 D4 the
events carry an optional ``tool_name`` field — null for
plugin-level operations, the named tool for per-tool ones.
The event_type stays the same in both cases so an auditor
querying ``event_type = 'agent_plugin_granted'`` gets the
full chronological view; filtering by ``tool_name`` is the
secondary lens.

Why ``agent_plugin_granted`` carries the trust_tier:
ADR-0045 T3 / Burst 115 enforces per-grant trust_tier in
PostureGateStep. The audit chain is the source of truth for
the grant's trust at the moment it was issued — operators
querying "was this agent allowed to call X without gating at
time T?" need the tier in the event payload, not just in the
table. Storing in both places (table = current state, chain =
history) is the same posture as every other grant-shaped
event.
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
    # ADR-0053 T3 (B238): null = plugin-level grant (ADR-0043
    # original semantic, covers all manifest tools); non-null =
    # per-tool grant covering only this tool. The constraint
    # matches the tool_name shape used in mcp_call.v1's tool
    # identifiers — versioned dotted strings ("foo_bar.v1") are
    # the common case, but plain names are also valid.
    tool_name: str | None = Field(
        None,
        min_length=1,
        max_length=120,
        description=(
            "Optional. Pass to issue a per-tool grant (ADR-0053). "
            "Omit or null for a plugin-level grant (ADR-0043 default)."
        ),
    )
    reason: str | None = Field(None, max_length=500)


class RevokeRequest(BaseModel):
    reason: str | None = Field(None, max_length=500)


def _serialize_grant(g: PluginGrant) -> dict[str, Any]:
    return {
        "instance_id": g.instance_id,
        "plugin_name": g.plugin_name,
        # ADR-0053 T3 (B238): expose tool_name on the wire.
        # Null for plugin-level grants; string for per-tool grants.
        "tool_name": g.tool_name,
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
        # ADR-0053 D4: event_data gains optional tool_name (null for
        # plugin-level grants, string for per-tool). Event type
        # stays the same so chronological queries still cover both
        # grant shapes.
        entry = chain.append(
            "agent_plugin_granted",
            {
                "instance_id": instance_id,
                "plugin_name": body.plugin_name,
                "tool_name": body.tool_name,
                "trust_tier": body.trust_tier,
                "granted_by": operator_id,
                "reason": body.reason,
            },
            agent_dna=dna,
        )
        reg.plugin_grants.grant(
            instance_id=instance_id,
            plugin_name=body.plugin_name,
            tool_name=body.tool_name,
            trust_tier=body.trust_tier,
            granted_at_seq=entry.seq,
            granted_by=operator_id,
            reason=body.reason,
            when=entry.timestamp,
        )

    grant = reg.plugin_grants.get_active(
        instance_id, body.plugin_name, tool_name=body.tool_name,
    )
    return {"ok": True, "grant": _serialize_grant(grant)}


def _do_revoke(
    *,
    instance_id: str,
    plugin_name: str,
    tool_name: str | None,
    request: Request,
    reason: str | None,
) -> dict[str, Any]:
    """Shared revoke implementation for both plugin-level and per-tool
    DELETE routes. Looks up the exact (agent, plugin, tool) triple —
    no fallback. ``tool_name=None`` targets the plugin-level row;
    non-None targets only the per-tool row.

    Per ADR-0053 D4 the ``agent_plugin_revoked`` audit event gains an
    optional ``tool_name`` field — null for plugin-level operations,
    the named tool for per-tool ones. event_type stays the same in
    both cases.
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

    # Pre-check the grant exists at this exact triple. We need the
    # table info for the audit event payload (record the trust_tier
    # the grant was at when revoked; operators querying the chain
    # later won't know what it was). For per-tool revokes the
    # plugin-level grant — if any — is NOT touched; this method
    # looks up only the exact triple.
    existing = reg.plugin_grants.get_active(
        instance_id, plugin_name, tool_name=tool_name,
    )
    if existing is None:
        scope = (
            f"tool {tool_name!r} on plugin {plugin_name!r}"
            if tool_name is not None
            else f"plugin {plugin_name!r}"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"no active grant for {scope} on agent {instance_id!r}"
            ),
        )

    with write_lock:
        entry = chain.append(
            "agent_plugin_revoked",
            {
                "instance_id": instance_id,
                "plugin_name": plugin_name,
                "tool_name": tool_name,
                "prior_trust_tier": existing.trust_tier,
                "revoked_by": operator_id,
                "reason": reason,
            },
            agent_dna=dna,
        )
        affected = reg.plugin_grants.revoke(
            instance_id=instance_id,
            plugin_name=plugin_name,
            tool_name=tool_name,
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

    return {
        "ok": True,
        "instance_id": instance_id,
        "plugin_name": plugin_name,
        "tool_name": tool_name,
    }


@router.delete(
    "/agents/{instance_id}/plugin-grants/{plugin_name}",
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
def revoke_plugin(
    instance_id: str, plugin_name: str, request: Request,
    body: RevokeRequest | None = None,
) -> dict[str, Any]:
    """Revoke the active plugin-level grant for (agent, plugin).
    Returns 404 if no plugin-level active grant exists. Does NOT
    touch per-tool grants on the same plugin — those have their
    own DELETE route at ``.../tools/{tool_name}`` (ADR-0053 T3).
    Holds the write lock. Emits ``agent_plugin_revoked``."""
    return _do_revoke(
        instance_id=instance_id,
        plugin_name=plugin_name,
        tool_name=None,
        request=request,
        reason=body.reason if body else None,
    )


@router.delete(
    "/agents/{instance_id}/plugin-grants/{plugin_name}/tools/{tool_name}",
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
def revoke_per_tool(
    instance_id: str, plugin_name: str, tool_name: str,
    request: Request, body: RevokeRequest | None = None,
) -> dict[str, Any]:
    """ADR-0053 T3 — revoke a per-tool grant. Targets only the
    (agent, plugin, tool) triple; the plugin-level grant (if any)
    on the same (agent, plugin) is untouched. Returns 404 if no
    per-tool active grant exists at the triple. Holds the write
    lock. Emits ``agent_plugin_revoked`` with ``tool_name`` set."""
    return _do_revoke(
        instance_id=instance_id,
        plugin_name=plugin_name,
        tool_name=tool_name,
        request=request,
        reason=body.reason if body else None,
    )
