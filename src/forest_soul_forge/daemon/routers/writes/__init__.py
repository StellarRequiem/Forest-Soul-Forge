"""``/birth``, ``/spawn``, ``/regenerate-voice``, ``/archive`` — write endpoints.

Package layout (ADR-0040 T3, in progress):

    writes/
      __init__.py    — this file. Currently the catch-all for all
                       four endpoints + creation helpers + the package
                       APIRouter symbol app.py imports. Per-endpoint
                       sub-routers (birth.py, voice.py, archive.py) land
                       in T3.2-T3.4 and replace the body here with a
                       facade that include_router()s each sub-router.
      _shared.py     — idempotency-replay helpers used by every endpoint
                       (extracted T3.1, Burst 77).

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

from fastapi import APIRouter, Depends, HTTPException, Request, status

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
from forest_soul_forge.core.genre_engine import (
    GenreEngine,
    GenreEngineError,
    kit_violations_for_genre,
    trait_floor_violations,
)
from forest_soul_forge.core.tool_policy import resolve_constraints
from forest_soul_forge.daemon.config import DaemonSettings
from forest_soul_forge.daemon.deps import (
    get_audit_chain,
    get_genre_engine,
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
from forest_soul_forge.registry.registry import UnknownAgentError
from forest_soul_forge.soul.generator import SoulGenerator
from forest_soul_forge.soul.voice_renderer import (
    VoiceText,
    render_voice,
    update_soul_voice,
)

# Phase C.2 (2026-04-30) extraction — the truly pure helpers (string/path
# math, hashing, adapters, filesystem, time) moved to birth_pipeline.py
# so they're unit-testable in isolation. Import-as-alias keeps every
# call site below byte-stable; the underscore convention marks them as
# module-internal so external callers continue to use the routes, not
# the helpers.
from forest_soul_forge.daemon.routers.birth_pipeline import (
    chain_entry_to_parsed as _chain_entry_to_parsed,
    derive_constitution_hash as _derive_constitution_hash,
    instance_id_for as _instance_id_for,
    rollback_artifacts as _rollback_artifacts,
    safe_agent_name as _safe_agent_name,
    soul_path_for as _soul_path_for,
    to_agent_out as _to_agent_out,
    voice_event_fields as _voice_event_fields,
    write_artifacts as _write_artifacts,
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
# The truly pure helpers (string/path math, hashing, adapters,
# filesystem, time) moved to birth_pipeline.py during the 2026-04-30
# Phase C.2 decomposition; imported as underscore aliases above so
# every call site below this comment is byte-stable.
#
# What stayed: the HTTPException-raising helpers below (_build_trait_profile,
# _parent_lineage_from_registry, _resolve_*, _enforce_genre_kit_tier,
# _maybe_replay_cached, _cache_response, _maybe_render_voice). Each
# couples to FastAPI by raising HTTPException, so they belong with the
# routes for now. Future tranches can decide whether to extract them.


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


# ADR-0040 T3.1 (Burst 77, 2026-05-02): _maybe_replay_cached and
# _cache_response moved to _shared.py so per-endpoint sub-routers
# (birth.py / voice.py / archive.py — landing in T3.2-T3.4) can
# import them without inheriting the creation-surface helpers below.
# Imported as underscore aliases here so every existing call site
# in this module is byte-stable.
from forest_soul_forge.daemon.routers.writes._shared import (
    _cache_response,
    _maybe_replay_cached,
)


def _resolve_tool_kit(
    catalog: ToolCatalog,
    role: str,
    tools_add_in: list,
    tools_remove_in: list[str],
    genre: str | None = None,
):
    """Apply ADR-0018 kit resolution. Surfaces unknown-tool errors as 400.

    ``genre`` (ADR-0021 T4) is the OPTIONAL hint that lets the resolver
    fall back to the genre's default_tools when the role has no
    archetype-specific kit. Pass None for legacy / unclaimed roles —
    the resolver behaves bit-for-bit as it did pre-T4.

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
            genre=genre,
        )
    except ToolCatalogError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _resolve_tool_constraints(
    catalog: ToolCatalog,
    profile,
    resolved_tools,
) -> tuple[dict, ...]:
    """For each resolved tool, compute per-tool constraints via the
    tool_policy module (ADR-0018 T2.5). Returns a tuple of dicts ready
    for constitution.build(tools=...) and audit event_data emission.

    Each dict shape:
      { name, version, side_effects, constraints, applied_rules }
    """
    out: list[dict] = []
    for ref in resolved_tools:
        tool_def = catalog.get_tool(ref)
        rc = resolve_constraints(profile, tool_def)
        out.append(rc.to_dict())
    return tuple(out)


def _resolve_enrich(req_value: bool | None, settings: DaemonSettings) -> bool:
    """Three-state precedence: explicit request value > settings default."""
    return req_value if req_value is not None else settings.enrich_narrative_default


def _enforce_genre_kit_tier(
    genre_engine: GenreEngine,
    catalog: ToolCatalog,
    role: str,
    resolved_tools,
) -> None:
    """ADR-0021 T5: refuse a kit whose tools exceed the genre's
    ``max_side_effects`` ceiling.

    No-op when the role is unclaimed (no genre to enforce against) or
    when the resolved kit is empty. Raises HTTPException(400) with a
    detail naming the offending tool(s) and the genre's ceiling. The
    daemon refuses the birth/spawn before any artifact is written, so
    the audit chain stays clean of would-be-illegal agents.
    """
    if not resolved_tools:
        return
    try:
        gd = genre_engine.genre_for(role)
    except GenreEngineError:
        return  # unclaimed role → no genre check
    pairs: list[tuple[str, str]] = []
    for ref in resolved_tools:
        td = catalog.tools.get(ref.key)
        if td is None:
            continue
        pairs.append((td.name, td.side_effects))
    violations = kit_violations_for_genre(gd, pairs)
    if violations:
        offenders = ", ".join(
            f"{name} ({se})" for name, se in violations
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"genre kit-tier violation: {role!r} is in genre "
                f"{gd.name!r} (max_side_effects={gd.risk_profile.max_side_effects}); "
                f"the resolved kit contains tools that exceed that ceiling: "
                f"{offenders}. Drop the offending tools via tools_remove "
                f"or change the role to one whose genre allows them."
            ),
        )


def _enforce_genre_trait_floors(
    genre_engine: GenreEngine,
    role: str,
    trait_values: dict[str, int],
) -> None:
    """ADR-0038 T1: refuse a profile whose trait values fall below the
    genre's declared ``min_trait_floors``.

    No-op when:
      - The role isn't claimed by any genre (no floors to enforce against).
      - The genre declares no floors (default for all non-Companion genres).

    Raises HTTPException(400) listing every violating trait so the
    operator sees the full picture in one error rather than playing
    whack-a-mole. Refusal happens BEFORE constraint resolution + before
    any artifact is written; audit chain stays clean of would-be
    illegal agents.

    The §0 reasoning for hard-refusing rather than warning: the floor
    exists to mitigate H-1 (sycophancy) at birth time. A warning that
    the operator can dismiss reproduces exactly the failure mode the
    floor is meant to prevent. Operator override path emits a separate
    ``genre_trait_floor_override`` audit event (T2 work; not in this
    tranche — declaring + enforcing the floor is the v0.2 minimum).
    """
    try:
        gd = genre_engine.genre_for(role)
    except GenreEngineError:
        return  # unclaimed role → no genre check
    if not gd.min_trait_floors:
        return
    violations = trait_floor_violations(gd, trait_values)
    if violations:
        offenders = ", ".join(
            f"{name}={actual} (floor {floor})"
            for name, actual, floor in violations
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"genre trait-floor violation: {role!r} is in genre "
                f"{gd.name!r} which declares min_trait_floors={dict(gd.min_trait_floors)}; "
                f"the requested profile falls below: {offenders}. Raise the "
                f"offending trait value(s) at or above the floor or change "
                f"the role to one whose genre has no floor for those traits."
            ),
        )


def _resolve_initiative_posture(
    genre_engine: GenreEngine, role: str,
) -> tuple[str, str]:
    """ADR-0021-amendment §2: derive (initiative_level, initiative_ceiling)
    for an agent at birth time.

    The ceiling comes from the genre's ``max_initiative_level``; the
    default level comes from the genre's ``default_initiative_level``.
    Operator-supplied overrides land via a separate per-request flow
    (deferred to a later tranche; v0.2 ships with genre defaults).

    Returns ``("L5", "L5")`` (back-compat defaults) when the role is
    unclaimed by any genre OR the genre engine is empty. The
    constitution build path treats those defaults as "no initiative
    ceiling", matching pre-amendment behavior.
    """
    try:
        gd = genre_engine.genre_for(role)
    except GenreEngineError:
        return "L5", "L5"
    return gd.default_initiative_level, gd.max_initiative_level


def _resolve_genre(
    genre_engine: GenreEngine, role: str
) -> tuple[str | None, str | None]:
    """ADR-0021 T3: derive (genre, genre_description) from the role.

    Returns ``(None, None)`` when the role isn't claimed (empty engine
    or unclaimed role). Birth/spawn proceed with no genre — the
    constitution still builds; consumers see the empty-string sentinel
    in the hash body and a missing ``genre:`` line in the soul / yaml.

    Per ADR-0021 open question 1: genre is implicit (always derived
    from role), not a per-request override. The override mechanism
    for spawn-compatibility violations is a separate concern handled
    in T6, not here.
    """
    try:
        gd = genre_engine.genre_for(role)
    except GenreEngineError:
        return None, None
    return gd.name, gd.description


def _maybe_render_voice(
    *,
    enrich: bool,
    providers: ProviderRegistry,
    profile,
    engine: TraitEngine,
    lineage: Lineage,
    settings: DaemonSettings,
    genre_engine: GenreEngine | None = None,
) -> VoiceText | None:
    """Render the Voice section sync-callably.

    Returns ``None`` when enrich is False — caller must distinguish "no
    Voice section" from "templated fallback because provider was down"
    (the renderer handles the latter internally and returns a VoiceText
    with provider="template").

    Bridges the renderer's async API into the sync writes handler via
    ``asyncio.run()``. New event loop per call, torn down after — fine
    in a threadpool worker, no conflict with FastAPI's main loop.

    ``genre_engine`` (ADR-0021 T7) is optional. When supplied AND the
    role is claimed, the renderer receives the genre name + trait
    emphasis. When unsupplied or the role is unclaimed, the renderer
    behaves bit-identically to the pre-T7 path.
    """
    if not enrich:
        return None
    role = engine.get_role(profile.role)
    genre_name: str | None = None
    genre_trait_emphasis: tuple[str, ...] = ()
    if genre_engine is not None:
        try:
            gd = genre_engine.genre_for(profile.role)
            genre_name = gd.name
            genre_trait_emphasis = gd.trait_emphasis
        except GenreEngineError:
            pass  # unclaimed role → no genre context, that's fine
    return asyncio.run(
        render_voice(
            providers.active(),
            profile=profile,
            role=role,
            engine=engine,
            lineage=lineage,
            settings=settings,
            genre_name=genre_name,
            genre_trait_emphasis=genre_trait_emphasis,
        )
    )


# ---------------------------------------------------------------------------
# _perform_create — the shared body of /birth and /spawn (R2 refactor)
# ---------------------------------------------------------------------------
# Pre-R2 history: birth() and spawn() were 218 LoC + 285 LoC of nearly
# identical code. The /spawn-only behaviors (parent lookup, spawn-rule
# enforcement, lineage construction with parent ancestry, three extra
# event_data fields, two follow-on audit events when the spawn-rule
# was overridden) were structural deltas, but everything else — profile
# build, genre resolve, kit + constraint resolution, constitution build,
# voice render, idempotency check, soul generation, hardware binding,
# audit append, registry register, response cache — was duplicated
# verbatim. R2 collapses the duplicate into this helper.
#
# The route handlers (`birth`, `spawn`) own only the work that genuinely
# differs: spawn does parent lookup + spawn-rule check before delegating;
# birth does nothing. Then both call _perform_create with a small set of
# `mode` parameters that select the differing behavior:
#
#   endpoint     "/birth" or "/spawn" — used in idempotency request_hash
#                and the response-cache key
#   event_type   "agent_created" or "agent_spawned"
#   parent_row   AgentRow when spawning, None when birthing — gates the
#                three spawn-only event_data fields and the SoulGenerator
#                parent_instance arg
#   child_lineage  Lineage.root() for /birth; Lineage.from_parent(...) for
#                  /spawn — caller computes both forms; the helper just
#                  threads it through
#   parent_genre, spawn_override_used  spawn-only context for the
#                spawn_genre_override + governance_relaxed audit
#                follow-ons; both are no-ops on /birth
#
# Every comment block from the pre-R2 code is preserved verbatim
# because each one encodes ADR rationale (T3, T4, T5, T6, K6, R-track,
# T2.1) and rephrasing those during a mechanical extraction is exactly
# how rationale silently rots out of the codebase.
def _perform_create(
    *,
    req,                          # BirthRequest or SpawnRequest
    request: Request,
    registry: Registry,
    engine: TraitEngine,
    audit: AuditChain,
    lock: threading.Lock,
    settings: DaemonSettings,
    providers: ProviderRegistry,
    tool_catalog: ToolCatalog,
    genre_engine: GenreEngine,
    endpoint: str,                # "/birth" or "/spawn"
    event_type: str,              # "agent_created" or "agent_spawned"
    parent_row,                   # AgentRow | None — None for birth
    child_lineage: Lineage,       # Lineage.root() for birth, .from_parent(...) for spawn
    parent_genre: str | None,     # spawn-only context for follow-on audit
    spawn_override_used: bool,    # spawn-only — emit override audits
):
    idempotency_key = get_idempotency_key(request)
    request_hash = compute_request_hash(endpoint, req.model_dump(mode="json"))
    profile = _build_trait_profile(engine, req.profile)

    dna_hex = dna_full(profile)
    dna_s = dna_short(profile)

    # ADR-0021 T3: derive genre BEFORE kit resolution so the T4 fallback
    # has a chance to fire for unclaimed-archetype roles. None when the
    # role isn't claimed; the resolver and the kit-tier guard treat None
    # as "no genre rules apply" (back-compat).
    genre, genre_description = _resolve_genre(genre_engine, profile.role)

    # Resolve the tool kit BEFORE the lock — pure function, surfaces
    # unknown-tool errors as 400 before any artifact is touched. Genre
    # is passed so the T4 fallback can supply a default kit for roles
    # whose archetype entry is empty.
    resolved_tools = _resolve_tool_kit(
        tool_catalog, profile.role, req.tools_add, req.tools_remove,
        genre=genre,
    )
    # ADR-0021 T5: kit must respect the genre's max_side_effects ceiling.
    # No-op when the role is unclaimed; raises 400 with offending names
    # otherwise. Done BEFORE constraint resolution so a violating birth
    # rejects fast.
    _enforce_genre_kit_tier(genre_engine, tool_catalog, profile.role, resolved_tools)
    # ADR-0038 T1: profile must respect the genre's min_trait_floors.
    # Same fail-fast posture as kit-tier — refusal lands before any
    # artifact write. No-op for genres without declared floors (all
    # but Companion at v0.2).
    _enforce_genre_trait_floors(genre_engine, profile.role, profile.trait_values)
    # Per-tool constraints from the trait profile (ADR-0018 T2.5).
    # constitution.build() ingests these so they're part of the
    # constitution_hash — agents with the same profile but different
    # tool surfaces or different constraint resolutions get different
    # hashes, which is correct.
    tool_constraints = _resolve_tool_constraints(
        tool_catalog, profile, resolved_tools
    )

    # genre + genre_description were resolved above (T3 + T4). Re-using
    # the same values means /preview, /birth, and the audit event all
    # see one consistent genre claim per request.

    # ADR-0021-amendment §2 — initiative posture from the genre.
    # Returns L5/L5 for unclaimed roles (back-compat); for claimed
    # roles, returns the genre's default + ceiling.
    initiative_level, initiative_ceiling = _resolve_initiative_posture(
        genre_engine, profile.role,
    )

    # Build constitution outside the lock — pure function, any schema
    # error surfaces as a 400 before we touch the write path.
    try:
        constitution = build_constitution(
            profile, engine, agent_name=req.agent_name,
            tools=tool_constraints,
            genre=genre,
            genre_description=genre_description,
            initiative_level=initiative_level,
            initiative_ceiling=initiative_ceiling,
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
        lineage=child_lineage,
        settings=settings,
        genre_engine=genre_engine,
    )

    with lock:
        # Idempotency check is the *first* thing inside the lock so two
        # concurrent requests with the same key can't both execute the
        # write path. On hit: return the cached response verbatim.
        cached = _maybe_replay_cached(
            registry, idempotency_key, endpoint, request_hash
        )
        if cached is not None:
            return cached

        sibling_index = registry.next_sibling_index(dna_s)
        instance_id = _instance_id_for(profile.role, dna_s, sibling_index)
        soul_path, const_path = _soul_path_for(
            settings.soul_output_dir, req.agent_name, instance_id
        )

        generator = SoulGenerator(engine)
        # Pass parent_instance only when there is one — SoulGenerator
        # signs the parent linkage into the soul markdown so it has to
        # see None for /birth and the real parent_instance for /spawn.
        gen_kwargs = dict(
            profile=profile,
            agent_name=req.agent_name,
            agent_version=req.agent_version,
            lineage=child_lineage,
            constitution_hash=effective_hash,
            constitution_file=const_path.name,
            instance_id=instance_id,
            sibling_index=sibling_index,
            voice=voice,
            tools=resolved_tools,
            tool_catalog_version=tool_catalog.version,
            genre=genre,
            genre_description=genre_description,
        )
        if parent_row is not None:
            gen_kwargs["parent_instance"] = parent_row.instance_id
        soul_doc = generator.generate(**gen_kwargs)

        constitution_yaml = constitution.to_yaml(generated_at=soul_doc.generated_at)
        if req.constitution_override:
            constitution_yaml = (
                constitution_yaml
                + "\n# --- override ---\n"
                + req.constitution_override
            )

        # ADR-003X K6 — opt-in hardware binding. Append a block to the
        # constitution YAML; outside canonical_body() so constitution_hash
        # is unaffected. The lifespan quarantine check reads this back.
        hardware_binding_value: str | None = None
        hardware_source_value: str | None = None
        if req.bind_to_hardware:
            from forest_soul_forge.core.hardware import compute_hardware_fingerprint
            fp = compute_hardware_fingerprint()
            if fp.source == "hostname_fallback" and not req.allow_weak_binding:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "hardware binding refused: machine fingerprint source is "
                        "'hostname_fallback' (no IOPlatformUUID/machine-id available). "
                        "Pass allow_weak_binding=true to override."
                    ),
                )
            hardware_binding_value = fp.fingerprint
            hardware_source_value = fp.source
            constitution_yaml += (
                f"\n# --- hardware_binding (ADR-003X K6) ---\n"
                f"hardware_binding:\n"
                f"  fingerprint: {fp.fingerprint}\n"
                f"  source: {fp.source}\n"
                f"  bound_at: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            )

        _write_artifacts(soul_path, soul_doc.markdown, const_path, constitution_yaml)

        # ADR-003X K6 — emit hardware_bound event when binding was set.
        if hardware_binding_value:
            try:
                audit.append(
                    "hardware_bound",
                    {
                        "instance_id": instance_id,
                        "fingerprint": hardware_binding_value,
                        "source": hardware_source_value,
                    },
                    agent_dna=dna_hex,
                )
            except Exception:
                # Don't fail birth on audit-event failure; binding is in
                # the constitution YAML which is the source of truth.
                pass

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
            "tools": list(tool_constraints),
            "tool_catalog_version": tool_catalog.version,
            "genre": genre,
            **_voice_event_fields(voice),
        }
        # Spawn-only event_data fields. Adding them only when there's an
        # actual parent keeps /birth event_data shape stable — no None
        # parent_instance / parent_dna / lineage_depth=0 leakage.
        if parent_row is not None:
            event_data["parent_instance"] = parent_row.instance_id
            event_data["parent_dna"] = parent_row.dna
            event_data["lineage_depth"] = child_lineage.depth

        try:
            entry = audit.append(event_type, event_data, agent_dna=dna_s)
        except Exception as e:
            _rollback_artifacts(soul_path, const_path)
            raise HTTPException(
                status_code=500, detail=f"audit append failed: {e}"
            ) from e

        # ADR-0021 T6: emit a separate `spawn_genre_override` event when
        # the operator bypassed a genre spawn-rule. Logged AFTER
        # `agent_spawned` so the agent's first event is still the canonical
        # birth/spawn record; the override is a follow-on observation that
        # readers can correlate by agent_dna + parent_instance. Failure of
        # this append doesn't roll back the spawn — the agent is real and
        # the chain will be rebuilt-resilient because `agent_spawned`
        # already landed.
        if spawn_override_used:
            try:
                audit.append(
                    "spawn_genre_override",
                    {
                        "instance_id": instance_id,
                        "parent_instance": parent_row.instance_id,
                        "parent_genre": parent_genre,
                        "child_genre": genre,
                        "rationale": (
                            "operator set override_genre_spawn_rule=true"
                        ),
                    },
                    agent_dna=dna_s,
                )
                # T2.1: also emit the meta-event governance_relaxed so
                # operators can filter on a single event type across all
                # relaxation kinds. The dedicated spawn_genre_override
                # event stays for back-compat with anything that already
                # filters on it; this is additive.
                audit.append(
                    "governance_relaxed",
                    {
                        "relaxation_type": "spawn_genre_override",
                        "instance_id":     instance_id,
                        "parent_instance": parent_row.instance_id,
                        "parent_genre":    parent_genre,
                        "child_genre":     genre,
                        "rationale":       "operator set override_genre_spawn_rule=true",
                    },
                    agent_dna=dna_s,
                )
            except Exception:
                # Override audit failure is non-fatal — the spawn itself
                # is already on the chain; the missing override entry
                # will show up as a chain-integrity gap on next verify.
                pass

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
            endpoint,
            request_hash,
            status.HTTP_201_CREATED,
            out,
        )
        return out


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
    genre_engine: GenreEngine = Depends(get_genre_engine),
):
    """Create a new root agent.

    Post-R2: this handler owns nothing beyond the birth-specific mode
    settings — root lineage, agent_created event type, no parent. The
    actual artifact + chain + registry work happens in
    :func:`_perform_create`, which is shared with /spawn.
    """
    return _perform_create(
        req=req,
        request=request,
        registry=registry,
        engine=engine,
        audit=audit,
        lock=lock,
        settings=settings,
        providers=providers,
        tool_catalog=tool_catalog,
        genre_engine=genre_engine,
        endpoint="/birth",
        event_type="agent_created",
        parent_row=None,
        child_lineage=Lineage.root(),
        parent_genre=None,
        spawn_override_used=False,
    )


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
    genre_engine: GenreEngine = Depends(get_genre_engine),
):
    """Create a child agent under an existing parent.

    Post-R2: this handler does only the spawn-specific work — parent
    lookup, ADR-0021 T6 genre spawn-rule check, child lineage
    construction. Then it hands off to :func:`_perform_create` with the
    spawn-mode parameters (parent_row, lineage from_parent, agent_spawned
    event_type, spawn_override flag for the follow-on audit events).

    The spawn-rule check has to live HERE rather than in the shared
    helper because it depends on the child's profile being known AND on
    the parent's row being looked up — both done before _perform_create
    is called. Pulling it into _perform_create would mean threading
    parent_row + parent_genre through the whole helper just for the
    single 400-or-set-flag decision; cleaner to leave the check at the
    boundary where the caller already has both pieces of context.
    """
    profile = _build_trait_profile(engine, req.profile)
    parent_row, parent_ancestors = _parent_lineage_from_registry(
        registry, req.parent_instance_id
    )

    # ADR-0021 T3 + T4: same hoisting as /birth — genre is derived first
    # so the kit resolver's T4 fallback can fire when the role has no
    # archetype kit. The shared helper will also call _resolve_genre on
    # the child's role (purely; same answer) — accepting that double
    # call is the price of keeping the spawn-rule check at this layer.
    child_genre, _ = _resolve_genre(genre_engine, profile.role)

    # ADR-0021 T6: spawn-compatibility check. Resolve the parent's genre
    # and compare against the child's. The forbidden case returns 400
    # unless the operator explicitly sets override_genre_spawn_rule;
    # in the override path we record `spawn_genre_override` in the
    # audit chain alongside the regular `agent_spawned` event so the
    # violation is auditable after the fact. Forgiving behavior on
    # missing genre data: if EITHER side is unclaimed by any genre
    # (legacy artifacts, new role not yet in genres.yaml), skip the
    # check entirely — the genre engine isn't authoritative for those
    # roles and a hard refusal would be a false negative.
    parent_genre, _ = _resolve_genre(genre_engine, parent_row.role)
    spawn_override_used = False
    if parent_genre is not None and child_genre is not None:
        # Both sides have a claim; can_spawn never raises here because
        # we just confirmed parent_genre exists in the engine.
        if not genre_engine.can_spawn(parent_genre, child_genre):
            if not req.override_genre_spawn_rule:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"genre spawn-rule violation: parent genre "
                        f"{parent_genre!r} does not allow spawning child "
                        f"genre {child_genre!r}. Set override_genre_spawn_rule "
                        f"to true to bypass (the override emits a "
                        f"spawn_genre_override audit event)."
                    ),
                )
            spawn_override_used = True

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

    return _perform_create(
        req=req,
        request=request,
        registry=registry,
        engine=engine,
        audit=audit,
        lock=lock,
        settings=settings,
        providers=providers,
        tool_catalog=tool_catalog,
        genre_engine=genre_engine,
        endpoint="/spawn",
        event_type="agent_spawned",
        parent_row=parent_row,
        child_lineage=child_lineage,
        parent_genre=parent_genre,
        spawn_override_used=spawn_override_used,
    )


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
