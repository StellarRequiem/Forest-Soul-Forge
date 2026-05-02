"""writes — package facade. Per-endpoint sub-routers.

Package layout (ADR-0040 T3, in progress):

    writes/
      __init__.py    — this file. Package facade. Declares the parent
                       APIRouter (governance dependencies live here so
                       they fire once per request) and include_router()s
                       each per-endpoint sub-router. Currently still
                       owns /regenerate-voice and /archive directly —
                       those move out in T3.3 + T3.4.
      _shared.py     — idempotency-replay + voice-render helpers used
                       by multiple endpoints (T3.1, T3.2).
      birth.py       — /birth + /spawn + _perform_create + 10 creation
                       helpers (T3.2, this burst).
      voice.py       — /regenerate-voice (T3.3, pending).
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
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status

from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.core.dna import Lineage
from forest_soul_forge.core.genre_engine import GenreEngine
from forest_soul_forge.core.trait_engine import (
    InvalidTraitValueError,
    SchemaError as TraitSchemaError,
    TraitEngine,
    UnknownRoleError,
    UnknownTraitError,
)
from forest_soul_forge.daemon.config import DaemonSettings
from forest_soul_forge.daemon.deps import (
    get_audit_chain,
    get_genre_engine,
    get_provider_registry,
    get_registry,
    get_settings,
    get_trait_engine,
    get_write_lock,
    require_api_token,
    require_writes_enabled,
)
from forest_soul_forge.daemon.idempotency import (
    compute_request_hash,
    get_idempotency_key,
)
from forest_soul_forge.daemon.providers import ProviderRegistry
from forest_soul_forge.daemon.schemas import AgentOut, ArchiveRequest
from forest_soul_forge.registry import Registry
from forest_soul_forge.registry import ingest as _ingest
from forest_soul_forge.registry.registry import UnknownAgentError
from forest_soul_forge.soul.voice_renderer import VoiceText, update_soul_voice

from forest_soul_forge.daemon.routers.birth_pipeline import (
    chain_entry_to_parsed as _chain_entry_to_parsed,
    to_agent_out as _to_agent_out,
)

# ADR-0040 T3.2 — birth sub-router (creation surface). Mounted under
# this package's parent router, which carries the governance deps.
from forest_soul_forge.daemon.routers.writes import birth as _birth_module

# Helpers shared across endpoints — idempotency replay + voice render
# bridge. Per ADR-0040 §1 these live in _shared.py because multiple
# sub-routers + this facade dispatch through them.
from forest_soul_forge.daemon.routers.writes._shared import (
    _cache_response,
    _maybe_render_voice,
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

# Mount the birth sub-router. T3.3 + T3.4 will mount voice + archive
# the same way once those endpoints extract.
router.include_router(_birth_module.router)



# ---------------------------------------------------------------------------
# /agents/{instance_id}/regenerate-voice — re-run the LLM voice renderer
# ---------------------------------------------------------------------------
@router.post("/agents/{instance_id}/regenerate-voice", response_model=AgentOut)
def regenerate_voice(
    instance_id: str,
    request: Request,
    registry: Registry = Depends(get_registry),
    engine: TraitEngine = Depends(get_trait_engine),
    audit: AuditChain = Depends(get_audit_chain),
    lock: threading.Lock = Depends(get_write_lock),
    settings: DaemonSettings = Depends(get_settings),
    providers: ProviderRegistry = Depends(get_provider_registry),
    genre_engine: GenreEngine = Depends(get_genre_engine),
):
    """Re-run the Voice renderer for an existing agent (ADR-0017 follow-up).

    The agent's identity (dna, instance_id, constitution_hash) is
    unchanged. Only the soul.md ``## Voice`` body and the three
    narrative_* frontmatter fields are rewritten in-place. An audit
    event ``voice_regenerated`` records the before/after provider+model
    so the chain captures the prompt-iteration history.

    Useful for tuning prompts or comparing output across providers
    without spinning up new agents and polluting the registry.
    """
    try:
        row = registry.get_agent(instance_id)
    except UnknownAgentError as e:
        raise HTTPException(status_code=404, detail=f"unknown agent: {e}") from e

    soul_path = Path(row.soul_path)
    if not soul_path.exists():
        # Disk drift — registry has the row but the file is gone. Refuse
        # to regenerate; rebuild-from-artifacts is the right repair path.
        raise HTTPException(
            status_code=409,
            detail=f"soul file missing on disk: {soul_path}",
        )

    # Reconstruct the trait profile from the existing soul.md frontmatter.
    # Pulls trait_values + role + domain_weight_overrides directly so we
    # don't need them on the registry row.
    parsed_text = soul_path.read_text(encoding="utf-8")
    fm_match = _ingest._FRONTMATTER_RE.match(parsed_text)
    if fm_match is None:
        raise HTTPException(
            status_code=409,
            detail=f"soul file has no parseable frontmatter: {soul_path}",
        )
    frontmatter = _ingest._parse_frontmatter_block(fm_match.group(1))
    # The tolerant frontmatter parser returns scalars for inline `{}`
    # placeholders (e.g. SoulGenerator emits `domain_weight_overrides: {}`
    # literally). Coerce non-dict values to empty maps so build_profile
    # gets the shape it expects.
    trait_values = frontmatter.get("trait_values")
    if not isinstance(trait_values, dict):
        trait_values = {}
    domain_weight_overrides = frontmatter.get("domain_weight_overrides")
    if not isinstance(domain_weight_overrides, dict):
        domain_weight_overrides = {}

    try:
        profile = engine.build_profile(
            role=row.role,
            overrides={k: int(v) for k, v in trait_values.items()},
            domain_weight_overrides={
                k: float(v) for k, v in domain_weight_overrides.items()
            },
        )
    except (UnknownRoleError, UnknownTraitError, InvalidTraitValueError, TraitSchemaError) as e:
        raise HTTPException(
            status_code=400,
            detail=f"could not reconstruct profile from soul.md: {e}",
        ) from e

    # Reconstruct lineage from registry — same shape /spawn uses.
    if row.parent_instance:
        try:
            parent_row = registry.get_agent(row.parent_instance)
            ancestors_rows = registry.get_ancestors(row.parent_instance)
            root_first = list(reversed(ancestors_rows))
            parent_lineage_ancestors = tuple(r.dna for r in root_first)
            parent_lineage = Lineage(
                parent_dna=parent_row.dna,
                ancestors=parent_lineage_ancestors,
                spawned_by=parent_row.agent_name,
            )
            lineage = Lineage.from_parent(
                parent_dna=parent_row.dna,
                parent_lineage=parent_lineage,
                parent_agent_name=parent_row.agent_name,
            )
        except UnknownAgentError:
            lineage = Lineage.root()
    else:
        lineage = Lineage.root()

    # Render the new voice. Renderer catches provider errors internally
    # and returns a templated VoiceText, so this never raises — same
    # contract as /birth.
    new_voice = _maybe_render_voice(
        enrich=True,
        providers=providers,
        profile=profile,
        engine=engine,
        lineage=lineage,
        settings=settings,
        genre_engine=genre_engine,
    )
    if new_voice is None:
        # _maybe_render_voice only returns None when enrich=False, which
        # we never pass here. Defensive guard.
        raise HTTPException(status_code=500, detail="voice renderer returned None")

    with lock:
        # Patch the soul.md in place. Audit event records before/after.
        prev_provider = frontmatter.get("narrative_provider")
        prev_model = frontmatter.get("narrative_model")
        try:
            update_soul_voice(soul_path, new_voice)
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"soul.md voice update failed: {e}",
            ) from e

        event_data = {
            "instance_id": instance_id,
            "agent_name": row.agent_name,
            "role": row.role,
            "previous_provider": prev_provider,
            "previous_model": prev_model,
            "narrative_provider": new_voice.provider,
            "narrative_model": new_voice.model,
            "narrative_generated_at": new_voice.generated_at,
            "soul_path": str(soul_path),
        }
        try:
            entry = audit.append("voice_regenerated", event_data, agent_dna=row.dna)
        except Exception as e:
            # Soul.md was updated but the audit append failed. Don't try
            # to roll back the file — auditors can detect the drift via
            # rebuild-from-artifacts if needed. Surface the error.
            raise HTTPException(
                status_code=500,
                detail=f"audit append failed: {e}",
            ) from e
        registry.register_audit_event(
            _chain_entry_to_parsed(entry), instance_id=instance_id
        )

    return _to_agent_out(registry.get_agent(instance_id))


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
