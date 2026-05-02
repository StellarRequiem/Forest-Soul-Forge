"""writes/archive.py — agent lifecycle terminal trust surface.

ADR-0040 T3.4 extraction (Burst 80, 2026-05-02). Closes T3 — the
final per-endpoint extraction in writes/. After this lands, the
package facade (writes/__init__.py) carries no @router decorators
of its own; it is purely a composition point that declares the
parent APIRouter (with governance deps) and include_router()s
each per-endpoint sub-router.

Trust-surface scope (per ADR-0040 §1):
This file owns lifecycle-terminal governance — marking an existing
agent as archived (status='archived'), emitting the agent_archived
audit event, and idempotency-caching the response. Distinct from
creation (writes/birth.py) and from voice regeneration
(writes/voice.py): no soul.md mutation, no constitution work, no
genre/kit/trait checks. The endpoint deliberately preserves the
agent's identity and artifact state — only the registry status
column flips.

An agent given ``allowed_paths: [".../writes/archive.py"]`` can
extend the lifecycle-terminal logic — alternate archive reasons,
multi-stage tombstoning, archive-with-redaction modes — without
inheriting the ability to create new agents (writes/birth.py) or
regenerate voices (writes/voice.py). That separation is the
file-grained governance value ADR-0040 §1 was filed to deliver.

What lives here:
- ``archive`` route handler.

What lives elsewhere (don't reach for it):
- ``_maybe_replay_cached`` / ``_cache_response`` — idempotency
  helpers, in ``writes/_shared.py`` because every write endpoint
  routes through them.
- DTO + chain adapters (``to_agent_out``, ``chain_entry_to_parsed``)
  — in ``birth_pipeline.py`` from Phase C.2 (2026-04-30).

Why /archive does NOT generate a new audit-chain entry through
register_birth: archive doesn't insert a row, so the audit-event
mirror has to be called explicitly via register_audit_event. That
quirk is preserved verbatim from the pre-extraction module.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status

from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.daemon.deps import (
    get_audit_chain,
    get_registry,
    get_write_lock,
)
from forest_soul_forge.daemon.idempotency import (
    compute_request_hash,
    get_idempotency_key,
)
from forest_soul_forge.daemon.schemas import AgentOut, ArchiveRequest
from forest_soul_forge.registry import Registry
from forest_soul_forge.registry.registry import UnknownAgentError

from forest_soul_forge.daemon.routers.birth_pipeline import (
    chain_entry_to_parsed as _chain_entry_to_parsed,
    to_agent_out as _to_agent_out,
)
from forest_soul_forge.daemon.routers.writes._shared import (
    _cache_response,
    _maybe_replay_cached,
)


# Sub-router. The package facade (writes/__init__.py) declares the
# governance dependencies (require_writes_enabled + require_api_token);
# this router carries no deps of its own so include_router doesn't
# double-stack them on the included routes.
router = APIRouter()


@router.post("/archive", response_model=AgentOut)
def archive(
    req: ArchiveRequest,
    request: Request,
    registry: Registry = Depends(get_registry),
    audit: AuditChain = Depends(get_audit_chain),
    lock: threading.Lock = Depends(get_write_lock),
):
    idempotency_key = get_idempotency_key(request)
    request_hash = compute_request_hash("/archive", req.model_dump(mode="json"))

    try:
        row = registry.get_agent(req.instance_id)
    except UnknownAgentError as e:
        raise HTTPException(status_code=404, detail=f"unknown agent: {e}") from e

    if row.status == "archived":
        # Idempotent: archiving an already-archived agent is a no-op —
        # retries during a flaky client shouldn't fail. Skip the
        # idempotency cache here because there's nothing to mutate; the
        # response is derived from existing state.
        return _to_agent_out(row)

    with lock:
        cached = _maybe_replay_cached(
            registry, idempotency_key, "/archive", request_hash
        )
        if cached is not None:
            return cached

        event_data = {
            "instance_id": row.instance_id,
            "agent_name": row.agent_name,
            "role": row.role,
            "reason": req.reason,
            "archived_by": req.archived_by,
            "archived_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        try:
            entry = audit.append("agent_archived", event_data, agent_dna=row.dna)
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"audit append failed: {e}"
            ) from e
        # Mirror the chain entry into the registry's audit_events table so
        # /audit/tail and /audit/agent/{id} surface it. Birth/spawn do this
        # implicitly via register_birth(audit_entry=...); archive has to
        # call it explicitly because no row is being inserted.
        registry.register_audit_event(
            _chain_entry_to_parsed(entry), instance_id=row.instance_id
        )
        registry.update_status(row.instance_id, "archived")
        out = _to_agent_out(registry.get_agent(row.instance_id))
        _cache_response(
            registry,
            idempotency_key,
            "/archive",
            request_hash,
            status.HTTP_200_OK,
            out,
        )
        return out
