"""writes — package facade. Per-endpoint sub-routers.

Package layout (ADR-0040 T3, in progress):

    writes/
      __init__.py    — this file. Package facade. Declares the parent
                       APIRouter (governance dependencies live here so
                       they fire once per request) and include_router()s
                       each per-endpoint sub-router. Still owns /archive
                       directly — that moves out in T3.4 to close T3.
      _shared.py     — idempotency-replay + voice-render helpers used
                       by multiple endpoints (T3.1, T3.2).
      birth.py       — /birth + /spawn + _perform_create + 10 creation
                       helpers (T3.2).
      voice.py       — /regenerate-voice (T3.3, this burst).
      archive.py     — /archive (T3.4, pending — closes T3).

Public symbol: ``router`` (the parent APIRouter). ``app.py`` mounts it
exactly once via ``app.include_router(writes_router.router)``.

Ordering discipline — artifact-authoritative (ADR-0006):

    1. Generate soul + constitution byte-for-byte.
    2. Write them to disk (soul_generated/<filename>).
    3. Append one audit-chain entry.
    4. Register in SQLite (sibling_index + instance_id + ancestry).

Step 3 is the commit point. If step 3 succeeds but step 4 fails, the
registry can be rebuilt from artifacts and will re-derive the same row.
If step 3 fails, we delete the files from step 2 — the chain is the
source of truth, so a soul on disk that the chain never acknowledged is
a ghost we refuse to keep.

Serialization: every handler runs under ``app.state.write_lock`` (a
``threading.Lock``). FastAPI dispatches sync routes on a threadpool, so
a thread-level lock is the right primitive. This also guards the
``next_sibling_index`` → ``INSERT`` race on twin births.

Phase 4 (ADR-0017): when ``enrich_narrative`` resolves true, the LLM-
backed Voice renderer is called *outside* the write lock. Holding a
threading lock across a 1-4s network call would serialize unrelated
births for no benefit — the renderer's only side effect is the returned
``VoiceText``, not registry state.
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
    require_api_token,
    require_writes_enabled,
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

# Per-endpoint sub-routers. Each owns one trust surface; mounted
# under this package's parent router, which carries the governance
# deps.
from forest_soul_forge.daemon.routers.writes import birth as _birth_module
from forest_soul_forge.daemon.routers.writes import voice as _voice_module

# Helpers shared across endpoints — idempotency replay. Per ADR-0040
# §1 these live in _shared.py because multiple endpoints (only
# /archive remains in this facade) dispatch through them.
from forest_soul_forge.daemon.routers.writes._shared import (
    _cache_response,
    _maybe_replay_cached,
)


# Parent router. Governance deps declared HERE only — sub-routers
# carry no deps of their own so include_router doesn't double-stack
# the deps on the included routes.
router = APIRouter(
    tags=["writes"],
    # Order matters: 403 fires before 401 when writes are disabled, which
    # is the more informative response — "this deployment doesn't accept
    # writes" is a different problem than "you're missing the token".
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)

# Mount the per-endpoint sub-routers. T3.4 will mount archive the same
# way; at that point this facade has no @router decorators of its own
# and is purely a composition point.
router.include_router(_birth_module.router)
router.include_router(_voice_module.router)



# ---------------------------------------------------------------------------
# /archive — mark an existing agent as archived
# ---------------------------------------------------------------------------
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
