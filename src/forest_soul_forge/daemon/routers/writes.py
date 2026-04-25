"""``/birth``, ``/spawn``, ``/archive`` — write endpoints (Phase 3).

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
``VoiceText``, not registry state. We bridge async-to-sync via
``asyncio.run()`` because the handlers are sync (FastAPI threadpool
dispatch). Provider failures at the renderer level are caught inside
the renderer and produce a templated fallback; ``/birth`` never fails
because Ollama is down.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from forest_soul_forge.core.audit_chain import AuditChain, ChainEntry
from forest_soul_forge.core.constitution import build as build_constitution
from forest_soul_forge.core.dna import Lineage, dna_full, dna_short
from forest_soul_forge.core.trait_engine import (
    InvalidTraitValueError,
    SchemaError as TraitSchemaError,
    TraitEngine,
    UnknownRoleError,
    UnknownTraitError,
)
from forest_soul_forge.core.tool_catalog import (
    ToolCatalog,
    ToolCatalogError,
    ToolRef as CoreToolRef,
)
from forest_soul_forge.daemon.config import DaemonSettings
from forest_soul_forge.daemon.deps import (
    get_audit_chain,
    get_provider_registry,
    get_registry,
    get_settings,
    get_tool_catalog,
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
from forest_soul_forge.daemon.schemas import (
    AgentOut,
    ArchiveRequest,
    BirthRequest,
    SpawnRequest,
    TraitProfileIn,
)
from forest_soul_forge.registry import Registry
from forest_soul_forge.registry import ingest as _ingest
from forest_soul_forge.registry.ingest import ParsedAuditEntry
from forest_soul_forge.registry.registry import (
    IdempotencyMismatchError,
    UnknownAgentError,
)
from forest_soul_forge.soul.generator import SoulGenerator
from forest_soul_forge.soul.voice_renderer import (
    VoiceText,
    render_voice,
    update_soul_voice,
)


router = APIRouter(
    tags=["writes"],
    # Order matters: 403 fires before 401 when writes are disabled, which
    # is the more informative response — "this deployment doesn't accept
    # writes" is a different problem than "you're missing the token".
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _safe_agent_name(name: str) -> str:
    """Filename-safe rendering of the agent name.

    Whitelist-only: letters, digits, hyphen, underscore. Everything else
    becomes underscore. Keeps filenames portable across OSes and prevents
    a malicious agent name from being a traversal vector.
    """
    out: list[str] = []
    for ch in name:
        if ch.isalnum() or ch in ("-", "_"):
            out.append(ch)
        else:
            out.append("_")
    return "".join(out) or "agent"


def _instance_id_for(role: str, dna_short_hex: str, sibling_index: int) -> str:
    """Build the canonical instance_id.

    First sibling (the common case) gets the clean ``role_dna`` form. Twins
    and beyond append ``_N`` so the ID is unique and the suffix only
    appears when it's load-bearing.
    """
    base = f"{role}_{dna_short_hex}"
    return base if sibling_index <= 1 else f"{base}_{sibling_index}"


def _build_trait_profile(engine: TraitEngine, payload: TraitProfileIn):
    """Validate the inbound profile and surface engine errors as 400s."""
    try:
        return engine.build_profile(
            role=payload.role,
            overrides=dict(payload.trait_values or {}),
            domain_weight_overrides=dict(payload.domain_weight_overrides or {}),
        )
    except UnknownRoleError as e:
        raise HTTPException(status_code=400, detail=f"unknown role: {e}") from e
    except UnknownTraitError as e:
        raise HTTPException(status_code=400, detail=f"unknown trait: {e}") from e
    except InvalidTraitValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid trait value: {e}") from e
    except TraitSchemaError as e:
        # Raised for domain weight overrides that reference an unknown
        # domain or fall outside engine-configured bounds.
        raise HTTPException(status_code=400, detail=f"invalid profile: {e}") from e


def _derive_constitution_hash(
    derived_hash: str, constitution_override: str | None
) -> str:
    """Fold an optional override YAML into the constitution hash.

    Path D: when the caller supplies ``constitution_override``, we bind
    its bytes to the agent's constitution hash so tampering with the
    override invalidates verification. When absent, the derived hash is
    used untouched — behavior is identical to the no-override case.
    """
    if not constitution_override:
        return derived_hash
    h = hashlib.sha256()
    h.update(derived_hash.encode("utf-8"))
    h.update(b"\noverride:\n")
    h.update(constitution_override.encode("utf-8"))
    return h.hexdigest()


def _soul_path_for(
    out_dir: Path, agent_name: str, instance_id: str
) -> tuple[Path, Path]:
    """Return (soul_path, constitution_path) under the output dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = _safe_agent_name(agent_name)
    base = f"{safe}__{instance_id}"
    return out_dir / f"{base}.soul.md", out_dir / f"{base}.constitution.yaml"


def _parent_lineage_from_registry(registry: Registry, parent_instance_id: str):
    """Reconstruct the parent's :class:`Lineage` from the registry.

    ``get_ancestors`` is ordered parent-outward (depth ASC). We reverse
    to get root-first, then drop DNAs to build the ``ancestors`` tuple
    that :class:`Lineage` expects.
    """
    try:
        parent_row = registry.get_agent(parent_instance_id)
    except UnknownAgentError as e:
        raise HTTPException(
            status_code=404, detail=f"unknown parent instance: {e}"
        ) from e
    ancestors_rows = registry.get_ancestors(parent_instance_id)
    root_first = list(reversed(ancestors_rows))
    parent_lineage_ancestors = tuple(r.dna for r in root_first)
    return parent_row, parent_lineage_ancestors


def _to_agent_out(row) -> AgentOut:
    return AgentOut(**asdict(row))


def _write_artifacts(
    soul_path: Path, soul_md: str, constitution_path: Path, constitution_yaml: str
) -> None:
    """Write the paired artifacts.

    Writing constitution first so a crash between the two leaves a
    dangling constitution instead of a soul that points at nothing —
    easier to detect and clean up.
    """
    constitution_path.write_text(constitution_yaml, encoding="utf-8")
    soul_path.write_text(soul_md, encoding="utf-8")


def _rollback_artifacts(soul_path: Path, constitution_path: Path) -> None:
    """Best-effort cleanup when the audit append fails."""
    for p in (soul_path, constitution_path):
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass


def _idempotency_now() -> str:
    """ISO-8601 UTC timestamp for the idempotency-cache row."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _maybe_replay_cached(
    registry: Registry,
    key: str | None,
    endpoint: str,
    request_hash: str,
) -> Response | None:
    """Return a cached response if this key was already served; else None.

    Caller is responsible for holding the daemon's write lock before
    invoking this — the lookup + subsequent handler execution must be
    atomic so two concurrent replays can't both run the write path.
    """
    if key is None:
        return None
    try:
        hit = registry.lookup_idempotency_key(key, endpoint, request_hash)
    except IdempotencyMismatchError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    if hit is None:
        return None
    cached_status, cached_json = hit
    return Response(
        content=cached_json,
        status_code=cached_status,
        media_type="application/json",
    )


def _cache_response(
    registry: Registry,
    key: str | None,
    endpoint: str,
    request_hash: str,
    status_code: int,
    payload: AgentOut,
) -> None:
    """Store the serialized response under ``key`` for future replays."""
    if key is None:
        return
    registry.store_idempotency_key(
        key,
        endpoint,
        request_hash,
        status_code,
        payload.model_dump_json(),
        _idempotency_now(),
    )


def _resolve_tool_kit(
    catalog: ToolCatalog,
    role: str,
    tools_add_in: list,
    tools_remove_in: list[str],
):
    """Apply ADR-0018 kit resolution. Surfaces unknown-tool errors as 400.

    Returns a tuple of CoreToolRef in the order resolve_kit produces.
    """
    add_refs = [
        CoreToolRef(name=t.name, version=t.version) for t in (tools_add_in or [])
    ]
    try:
        return catalog.resolve_kit(
            role,
            tools_add=add_refs,
            tools_remove=list(tools_remove_in or []),
        )
    except ToolCatalogError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _resolve_enrich(req_value: bool | None, settings: DaemonSettings) -> bool:
    """Three-state precedence: explicit request value > settings default."""
    return req_value if req_value is not None else settings.enrich_narrative_default


def _maybe_render_voice(
    *,
    enrich: bool,
    providers: ProviderRegistry,
    profile,
    engine: TraitEngine,
    lineage: Lineage,
    settings: DaemonSettings,
) -> VoiceText | None:
    """Render the Voice section sync-callably.

    Returns ``None`` when enrich is False — caller must distinguish "no
    Voice section" from "templated fallback because provider was down"
    (the renderer handles the latter internally and returns a VoiceText
    with provider="template").

    Bridges the renderer's async API into the sync writes handler via
    ``asyncio.run()``. New event loop per call, torn down after — fine
    in a threadpool worker, no conflict with FastAPI's main loop.
    """
    if not enrich:
        return None
    role = engine.get_role(profile.role)
    return asyncio.run(
        render_voice(
            providers.active(),
            profile=profile,
            role=role,
            engine=engine,
            lineage=lineage,
            settings=settings,
        )
    )


def _voice_event_fields(voice: VoiceText | None) -> dict:
    """Optional narrative_* fields for audit event_data.

    Returns an empty dict when voice is None so callers can ``**spread``
    into the event payload without conditionals.
    """
    if voice is None:
        return {}
    return {
        "narrative_provider": voice.provider,
        "narrative_model": voice.model,
        "narrative_generated_at": voice.generated_at,
    }


def _chain_entry_to_parsed(entry: ChainEntry) -> ParsedAuditEntry:
    """Lift a :class:`ChainEntry` into a :class:`ParsedAuditEntry`.

    The registry's ``register_birth`` signature takes the parsed form
    (that's what the rebuild path also produces), so we translate once
    here rather than teach the registry two shapes.
    """
    return ParsedAuditEntry(
        seq=entry.seq,
        timestamp=entry.timestamp,
        prev_hash=entry.prev_hash,
        entry_hash=entry.entry_hash,
        agent_dna=entry.agent_dna,
        event_type=entry.event_type,
        event_data=dict(entry.event_data),
    )


# ---------------------------------------------------------------------------
# /birth — create a root agent
# ---------------------------------------------------------------------------
@router.post("/birth", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
def birth(
    req: BirthRequest,
    request: Request,
    registry: Registry = Depends(get_registry),
    engine: TraitEngine = Depends(get_trait_engine),
    audit: AuditChain = Depends(get_audit_chain),
    lock: threading.Lock = Depends(get_write_lock),
    settings: DaemonSettings = Depends(get_settings),
    providers: ProviderRegistry = Depends(get_provider_registry),
    tool_catalog: ToolCatalog = Depends(get_tool_catalog),
):
    idempotency_key = get_idempotency_key(request)
    request_hash = compute_request_hash("/birth", req.model_dump(mode="json"))
    profile = _build_trait_profile(engine, req.profile)

    dna_hex = dna_full(profile)
    dna_s = dna_short(profile)
    lineage = Lineage.root()

    # Resolve the tool kit BEFORE the lock — pure function, surfaces
    # unknown-tool errors as 400 before any artifact is touched.
    resolved_tools = _resolve_tool_kit(
        tool_catalog, profile.role, req.tools_add, req.tools_remove
    )

    # Build constitution outside the lock — pure function, any schema
    # error surfaces as a 400 before we touch the write path.
    try:
        constitution = build_constitution(
            profile, engine, agent_name=req.agent_name
        )
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"constitution build failed: {e}"
        ) from e

    effective_hash = _derive_constitution_hash(
        constitution.constitution_hash, req.constitution_override
    )

    # Phase 4 (ADR-0017): render the Voice section before the lock.
    # Provider errors are caught inside the renderer and converted to a
    # templated VoiceText, so this never raises.
    enrich = _resolve_enrich(req.enrich_narrative, settings)
    voice = _maybe_render_voice(
        enrich=enrich,
        providers=providers,
        profile=profile,
        engine=engine,
        lineage=lineage,
        settings=settings,
    )

    with lock:
        # Idempotency check is the *first* thing inside the lock so two
        # concurrent requests with the same key can't both execute the
        # write path. On hit: return the cached response verbatim.
        cached = _maybe_replay_cached(
            registry, idempotency_key, "/birth", request_hash
        )
        if cached is not None:
            return cached

        sibling_index = registry.next_sibling_index(dna_s)
        instance_id = _instance_id_for(profile.role, dna_s, sibling_index)
        soul_path, const_path = _soul_path_for(
            settings.soul_output_dir, req.agent_name, instance_id
        )

        generator = SoulGenerator(engine)
        soul_doc = generator.generate(
            profile=profile,
            agent_name=req.agent_name,
            agent_version=req.agent_version,
            lineage=lineage,
            constitution_hash=effective_hash,
            constitution_file=const_path.name,
            instance_id=instance_id,
            sibling_index=sibling_index,
            voice=voice,
            tools=resolved_tools,
            tool_catalog_version=tool_catalog.version,
        )
        constitution_yaml = constitution.to_yaml(generated_at=soul_doc.generated_at)
        if req.constitution_override:
            constitution_yaml = (
                constitution_yaml
                + "\n# --- override ---\n"
                + req.constitution_override
            )

        _write_artifacts(soul_path, soul_doc.markdown, const_path, constitution_yaml)

        event_data = {
            "instance_id": instance_id,
            "agent_name": req.agent_name,
            "role": profile.role,
            "dna_full": dna_hex,
            "sibling_index": sibling_index,
            "twin_of": (
                _instance_id_for(profile.role, dna_s, 1)
                if sibling_index > 1
                else None
            ),
            "constitution_source": (
                "derived+override" if req.constitution_override else "derived"
            ),
            "constitution_hash": effective_hash,
            "soul_path": str(soul_path),
            "constitution_path": str(const_path),
            "owner_id": req.owner_id,
            "tools": [r.to_dict() for r in resolved_tools],
            "tool_catalog_version": tool_catalog.version,
            **_voice_event_fields(voice),
        }
        try:
            entry = audit.append("agent_created", event_data, agent_dna=dna_s)
        except Exception as e:
            _rollback_artifacts(soul_path, const_path)
            raise HTTPException(
                status_code=500, detail=f"audit append failed: {e}"
            ) from e

        parsed = _ingest.parse_soul_file(soul_path)
        registry.register_birth(
            parsed,
            audit_entry=_chain_entry_to_parsed(entry),
            instance_id=instance_id,
            sibling_index=sibling_index,
        )
        out = _to_agent_out(registry.get_agent(instance_id))
        _cache_response(
            registry,
            idempotency_key,
            "/birth",
            request_hash,
            status.HTTP_201_CREATED,
            out,
        )
        return out


# ---------------------------------------------------------------------------
# /spawn — child agent from an existing parent
# ---------------------------------------------------------------------------
@router.post("/spawn", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
def spawn(
    req: SpawnRequest,
    request: Request,
    registry: Registry = Depends(get_registry),
    engine: TraitEngine = Depends(get_trait_engine),
    audit: AuditChain = Depends(get_audit_chain),
    lock: threading.Lock = Depends(get_write_lock),
    settings: DaemonSettings = Depends(get_settings),
    providers: ProviderRegistry = Depends(get_provider_registry),
    tool_catalog: ToolCatalog = Depends(get_tool_catalog),
):
    idempotency_key = get_idempotency_key(request)
    request_hash = compute_request_hash("/spawn", req.model_dump(mode="json"))
    profile = _build_trait_profile(engine, req.profile)
    parent_row, parent_ancestors = _parent_lineage_from_registry(
        registry, req.parent_instance_id
    )

    # Resolve tool kit before lock — same pattern as /birth.
    resolved_tools = _resolve_tool_kit(
        tool_catalog, profile.role, req.tools_add, req.tools_remove
    )

    dna_hex = dna_full(profile)
    dna_s = dna_short(profile)
    parent_lineage = Lineage(
        parent_dna=parent_row.dna,
        ancestors=parent_ancestors,
        spawned_by=parent_row.agent_name,
    )
    child_lineage = Lineage.from_parent(
        parent_dna=parent_row.dna,
        parent_lineage=parent_lineage,
        parent_agent_name=parent_row.agent_name,
    )

    try:
        constitution = build_constitution(
            profile, engine, agent_name=req.agent_name
        )
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"constitution build failed: {e}"
        ) from e

    effective_hash = _derive_constitution_hash(
        constitution.constitution_hash, req.constitution_override
    )

    enrich = _resolve_enrich(req.enrich_narrative, settings)
    voice = _maybe_render_voice(
        enrich=enrich,
        providers=providers,
        profile=profile,
        engine=engine,
        lineage=child_lineage,
        settings=settings,
    )

    with lock:
        cached = _maybe_replay_cached(
            registry, idempotency_key, "/spawn", request_hash
        )
        if cached is not None:
            return cached

        sibling_index = registry.next_sibling_index(dna_s)
        instance_id = _instance_id_for(profile.role, dna_s, sibling_index)
        soul_path, const_path = _soul_path_for(
            settings.soul_output_dir, req.agent_name, instance_id
        )

        generator = SoulGenerator(engine)
        soul_doc = generator.generate(
            profile=profile,
            agent_name=req.agent_name,
            agent_version=req.agent_version,
            lineage=child_lineage,
            constitution_hash=effective_hash,
            constitution_file=const_path.name,
            instance_id=instance_id,
            parent_instance=parent_row.instance_id,
            sibling_index=sibling_index,
            voice=voice,
            tools=resolved_tools,
            tool_catalog_version=tool_catalog.version,
        )
        constitution_yaml = constitution.to_yaml(generated_at=soul_doc.generated_at)
        if req.constitution_override:
            constitution_yaml = (
                constitution_yaml
                + "\n# --- override ---\n"
                + req.constitution_override
            )

        _write_artifacts(soul_path, soul_doc.markdown, const_path, constitution_yaml)

        event_data = {
            "instance_id": instance_id,
            "agent_name": req.agent_name,
            "role": profile.role,
            "dna_full": dna_hex,
            "sibling_index": sibling_index,
            "twin_of": (
                _instance_id_for(profile.role, dna_s, 1)
                if sibling_index > 1
                else None
            ),
            "constitution_source": (
                "derived+override" if req.constitution_override else "derived"
            ),
            "constitution_hash": effective_hash,
            "parent_instance": parent_row.instance_id,
            "parent_dna": parent_row.dna,
            "lineage_depth": child_lineage.depth,
            "soul_path": str(soul_path),
            "constitution_path": str(const_path),
            "owner_id": req.owner_id,
            "tools": [r.to_dict() for r in resolved_tools],
            "tool_catalog_version": tool_catalog.version,
            **_voice_event_fields(voice),
        }
        try:
            entry = audit.append("agent_spawned", event_data, agent_dna=dna_s)
        except Exception as e:
            _rollback_artifacts(soul_path, const_path)
            raise HTTPException(
                status_code=500, detail=f"audit append failed: {e}"
            ) from e

        parsed = _ingest.parse_soul_file(soul_path)
        registry.register_birth(
            parsed,
            audit_entry=_chain_entry_to_parsed(entry),
            instance_id=instance_id,
            sibling_index=sibling_index,
        )
        out = _to_agent_out(registry.get_agent(instance_id))
        _cache_response(
            registry,
            idempotency_key,
            "/spawn",
            request_hash,
            status.HTTP_201_CREATED,
            out,
        )
        return out


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
