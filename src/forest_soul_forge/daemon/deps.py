"""Dependency-injection helpers for the daemon.

Shared objects live on ``app.state``:

* ``app.state.registry`` — a single open :class:`Registry` for the
  process. WAL mode means concurrent reads are fine; writes go through
  ``app.state.write_lock`` (thread-level) to keep the single-writer
  SQLite discipline.
* ``app.state.providers`` — the :class:`ProviderRegistry`.
* ``app.state.trait_engine`` — parsed trait tree, used by /birth.
* ``app.state.audit_chain`` — append-only chain, used by /birth /spawn /archive.
* ``app.state.write_lock`` — :class:`threading.Lock` serializing writes.

Routers reach these via the helpers below so the dependency graph is
explicit and testable.
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, Request, status

from forest_soul_forge.daemon.providers import (
    ModelProvider,
    ProviderRegistry,
    UnknownProviderError,
)

if TYPE_CHECKING:
    from forest_soul_forge.core.audit_chain import AuditChain
    from forest_soul_forge.core.trait_engine import TraitEngine
    from forest_soul_forge.registry import Registry


def get_registry(request: Request) -> "Registry":
    reg = getattr(request.app.state, "registry", None)
    if reg is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="registry not initialized",
        )
    return reg


def get_provider_registry(request: Request) -> ProviderRegistry:
    pr = getattr(request.app.state, "providers", None)
    if pr is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="provider registry not initialized",
        )
    return pr


def get_active_provider(
    providers: ProviderRegistry = Depends(get_provider_registry),
) -> ModelProvider:
    return providers.active()


def get_provider_by_name(
    name: str,
    providers: ProviderRegistry = Depends(get_provider_registry),
) -> ModelProvider:
    try:
        return providers.get(name)
    except UnknownProviderError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


def get_trait_engine(request: Request) -> "TraitEngine":
    """Return the shared :class:`TraitEngine` or 503 if not bootstrapped.

    Write endpoints depend on this — if the trait tree failed to load at
    startup (missing file, bad YAML), read endpoints keep working but
    /birth and /spawn return 503 with a clear message rather than
    raising deep inside the handler.
    """
    engine = getattr(request.app.state, "trait_engine", None)
    if engine is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="trait engine not available (check FSF_TRAIT_TREE_PATH)",
        )
    return engine


def get_tool_catalog(request: Request):
    """Return the loaded :class:`ToolCatalog` from app.state.

    Always returns a catalog (possibly empty) — never raises 503 — so
    /birth keeps working even when the catalog file is missing. The
    empty-catalog case yields zero standard tools and rejects any
    tools_add referencing unknown entries with a clear 400.
    """
    from forest_soul_forge.core.tool_catalog import empty_catalog
    catalog = getattr(request.app.state, "tool_catalog", None)
    if catalog is None:
        return empty_catalog()
    return catalog


def get_genre_engine(request: Request):
    """Return the loaded :class:`GenreEngine` from app.state.

    Always returns an engine (possibly empty) — never raises 503 — so
    /birth keeps working when ``genres.yaml`` is missing or malformed.
    Empty engine means no role is claimed by any genre, so birth and
    spawn proceed with ``genre=None`` and the resulting constitution
    has the empty-string sentinel in its hash body.
    """
    from forest_soul_forge.core.genre_engine import empty_engine
    engine = getattr(request.app.state, "genre_engine", None)
    if engine is None:
        return empty_engine()
    return engine


def get_tool_registry(request: Request):
    """Return the loaded :class:`ToolRegistry` from app.state.

    Like the catalog/genre engines, this is best-effort: a load failure
    at lifespan degrades to an empty registry so read-only endpoints
    keep working. Callers that depend on actual tools being present
    (the dispatcher endpoint) check ``has(...)`` before dispatching
    and 503 if the registry is empty AND a non-empty catalog was
    expected.
    """
    from forest_soul_forge.tools.base import empty_registry
    reg = getattr(request.app.state, "tool_registry", None)
    if reg is None:
        return empty_registry()
    return reg


def get_tool_dispatcher(request: Request):
    """Return the shared :class:`ToolDispatcher` or 503 if unavailable.

    The dispatcher is built lazily on first request and cached on
    ``app.state.tool_dispatcher`` so the registry, audit chain, and
    counter callbacks are wired up exactly once. Tests can pre-stash
    a fake on app.state to bypass the lazy build.
    """
    cached = getattr(request.app.state, "tool_dispatcher", None)
    if cached is not None:
        return cached

    registry = getattr(request.app.state, "tool_registry", None)
    audit = getattr(request.app.state, "audit_chain", None)
    fsf_registry = getattr(request.app.state, "registry", None)
    if registry is None or audit is None or fsf_registry is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "tool dispatcher unavailable — one of "
                "tool_registry/audit_chain/registry failed to load at "
                "startup. Check /healthz for the underlying error."
            ),
        )
    from forest_soul_forge.tools.dispatcher import ToolDispatcher
    dispatcher = ToolDispatcher(
        registry=registry,
        audit=audit,
        counter_get=fsf_registry.get_tool_call_count,
        counter_inc=fsf_registry.increment_tool_call_count,
    )
    request.app.state.tool_dispatcher = dispatcher
    return dispatcher


def get_audit_chain(request: Request) -> "AuditChain":
    chain = getattr(request.app.state, "audit_chain", None)
    if chain is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="audit chain not available (check FSF_AUDIT_CHAIN_PATH)",
        )
    return chain


def get_write_lock(request: Request) -> threading.Lock:
    lock = getattr(request.app.state, "write_lock", None)
    if lock is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="write lock not initialized",
        )
    return lock


def get_settings(request: Request):
    """Return :class:`DaemonSettings` stored on ``app.state``.

    Pulled into its own dep so handlers don't reach into ``request.app.state``
    directly — keeps the dependency graph flat and mockable.
    """
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="settings not initialized",
        )
    return settings


def require_writes_enabled(request: Request) -> None:
    """FastAPI dep that 403s when writes are globally disabled.

    ``allow_write_endpoints`` is a settings flag so a misconfigured
    daemon can be forced read-only without redeploying with a different
    router wiring.
    """
    settings = getattr(request.app.state, "settings", None)
    if settings is None or not getattr(settings, "allow_write_endpoints", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="write endpoints disabled (FSF_ALLOW_WRITE_ENDPOINTS=false)",
        )


def require_api_token(request: Request) -> None:
    """Shared-secret token gate.

    Compares ``X-FSF-Token`` header to ``settings.api_token`` with
    :func:`hmac.compare_digest` so we don't leak timing info about how
    many bytes matched. When the setting is ``None`` (dev default),
    auth is bypassed entirely — preserves frictionless local use.

    Returns 401 on missing or wrong token. 401 is the right code here
    per RFC 7235: the request is well-formed, authentication failed.
    """
    import hmac

    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="settings not initialized",
        )
    expected = getattr(settings, "api_token", None)
    if expected is None:
        return  # auth disabled → pass through
    got = request.headers.get("x-fsf-token") or ""
    # hmac.compare_digest is constant-time in the length of the shorter
    # input; encode both sides to keep lengths consistent if callers send
    # non-ASCII (shouldn't happen for tokens, but defensive).
    if not hmac.compare_digest(expected.encode("utf-8"), got.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid X-FSF-Token",
            headers={"WWW-Authenticate": "FSF-Token"},
        )
