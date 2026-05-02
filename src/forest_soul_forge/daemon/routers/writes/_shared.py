"""Shared helpers used across writes/ sub-routers.

Extracted per ADR-0040 §7 mixin-pattern reuse for non-class
god-object decomposition (Burst 77, 2026-05-02). The two helpers
here own the idempotency-replay surface: every write endpoint
(/birth, /spawn, /regenerate-voice, /archive) calls them under
the daemon's write lock to make retries safe.

Trust-surface scope (per ADR-0040 §1):
This file is the *shared utility surface* — it contains code that
genuinely benefits from reuse across the per-endpoint sub-routers
without expanding any one of them into the others' governance
domain. An agent given ``allowed_paths`` to a sub-router (e.g.
``writes/voice.py``) does NOT inherit access to this file by
default; if the agent needs to extend idempotency behavior, the
operator grants ``writes/_shared.py`` separately.

What's NOT here:
- Creation-specific helpers (trait profile validation, genre
  enforcement, kit-tier ceiling, voice rendering) — those stay in
  the creation sub-router (``writes/birth.py`` after T3.2) because
  they aren't reusable across the other endpoints.
- DTO + chain adapters (``to_agent_out``, ``chain_entry_to_parsed``,
  ``idempotency_now``) — those live in ``birth_pipeline.py`` from
  the Phase C.2 (2026-04-30) extraction. Re-exporting them here
  would just thicken the dependency graph.
"""
from __future__ import annotations

from fastapi import HTTPException, Response, status

from forest_soul_forge.daemon.routers.birth_pipeline import (
    idempotency_now as _idempotency_now,
)
from forest_soul_forge.daemon.schemas import AgentOut
from forest_soul_forge.registry import Registry
from forest_soul_forge.registry.registry import IdempotencyMismatchError


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
