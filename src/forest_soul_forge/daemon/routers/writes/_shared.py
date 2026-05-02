"""Shared helpers used across writes/ sub-routers.

Extracted per ADR-0040 §7 mixin-pattern reuse for non-class
god-object decomposition. The helpers here own surfaces that are
genuinely reused across multiple per-endpoint sub-routers — moving
them to a sub-router would force cross-imports between siblings.

Burst 77 (2026-05-02): _maybe_replay_cached + _cache_response.
Burst 78 (2026-05-02): _maybe_render_voice promoted from creation
helpers because /regenerate-voice also dispatches through it.

Trust-surface scope (per ADR-0040 §1):
This file is the *shared utility surface* — code that genuinely
benefits from reuse across the per-endpoint sub-routers without
expanding any one of them into the others' governance domain. An
agent given ``allowed_paths`` to a sub-router (e.g. ``writes/birth.py``)
does NOT inherit access to this file by default; if the agent needs
to extend the idempotency or voice-render contract, the operator
grants ``writes/_shared.py`` separately.

What's NOT here:
- Creation-specific helpers (trait profile validation, genre
  enforcement, kit-tier ceiling, parent lineage) — those live in
  ``writes/birth.py`` because they aren't reused by /voice or /archive.
- DTO + chain adapters (``to_agent_out``, ``chain_entry_to_parsed``,
  ``idempotency_now``) — those live in ``birth_pipeline.py`` from
  Phase C.2 (2026-04-30).
"""
from __future__ import annotations

import asyncio

from fastapi import HTTPException, Response, status

from forest_soul_forge.core.dna import Lineage
from forest_soul_forge.core.genre_engine import GenreEngine
from forest_soul_forge.core.trait_engine import TraitEngine
from forest_soul_forge.daemon.config import DaemonSettings
from forest_soul_forge.daemon.providers import ProviderRegistry
from forest_soul_forge.daemon.routers.birth_pipeline import (
    idempotency_now as _idempotency_now,
)
from forest_soul_forge.daemon.schemas import AgentOut
from forest_soul_forge.registry import Registry
from forest_soul_forge.registry.registry import IdempotencyMismatchError
from forest_soul_forge.soul.voice_renderer import VoiceText, render_voice


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
