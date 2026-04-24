"""FastAPI app factory for the Forest Soul Forge daemon.

Usage:

    uvicorn forest_soul_forge.daemon.app:app --host 127.0.0.1 --port 7423

Or programmatically::

    from forest_soul_forge.daemon.app import build_app
    app = build_app()

Lifespan opens the registry connection and stashes it + the provider
registry on ``app.state``. Shutdown closes the registry.

Read-only by design in v1. Write endpoints (birth, spawn, archive) land
behind ``app.state.write_lock`` (to be added) so writers are serialized
per single-writer SQLite discipline.
"""
from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.core.trait_engine import TraitEngine
from forest_soul_forge.daemon.config import DaemonSettings, build_settings
from forest_soul_forge.daemon.providers import ProviderRegistry
from forest_soul_forge.daemon.providers.frontier import FrontierProvider
from forest_soul_forge.daemon.providers.local import LocalProvider
from forest_soul_forge.daemon.routers import agents as agents_router
from forest_soul_forge.daemon.routers import audit as audit_router
from forest_soul_forge.daemon.routers import health as health_router
from forest_soul_forge.daemon.routers import preview as preview_router
from forest_soul_forge.daemon.routers import runtime as runtime_router
from forest_soul_forge.daemon.routers import traits as traits_router
from forest_soul_forge.daemon.routers import writes as writes_router
from forest_soul_forge.registry import Registry


def _build_provider_registry(settings: DaemonSettings) -> ProviderRegistry:
    local = LocalProvider(
        base_url=settings.local_base_url,
        models=settings.local_model_map(),
        timeout_s=settings.local_timeout_s,
    )
    frontier = FrontierProvider(
        enabled=settings.frontier_enabled,
        base_url=settings.frontier_base_url,
        api_key=settings.frontier_api_key,
        models=settings.frontier_model_map(),
        timeout_s=settings.frontier_timeout_s,
    )
    return ProviderRegistry(
        providers={"local": local, "frontier": frontier},
        default=settings.default_provider,
    )


def build_app(settings: DaemonSettings | None = None) -> FastAPI:
    """Compose the FastAPI app.

    Tests pass an explicit ``settings`` with a tmp registry path. Production
    omits the argument and reads from env.
    """
    settings = settings or build_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Bootstrap the registry. If the file doesn't exist, it's created;
        # if it does, schema version is verified (mismatch -> raises).
        registry = Registry.bootstrap(settings.registry_db_path)
        providers = _build_provider_registry(settings)

        # Write-path objects — loaded here so each request reuses them.
        # TraitEngine parses YAML, so we only want to do that once per
        # process; the audit chain holds a head pointer that must not be
        # shared across ad-hoc instances; the write lock serializes all
        # mutating endpoints (single-writer SQLite discipline, ADR-0007).
        trait_engine: TraitEngine | None = None
        audit_chain: AuditChain | None = None
        if settings.allow_write_endpoints:
            try:
                trait_engine = TraitEngine(settings.trait_tree_path)
            except Exception:
                # Tolerate missing trait tree at startup — endpoints that
                # need it will 503. Read-only endpoints still work.
                trait_engine = None
            try:
                audit_chain = AuditChain(settings.audit_chain_path)
            except Exception:
                audit_chain = None

        app.state.settings = settings
        app.state.registry = registry
        app.state.providers = providers
        app.state.trait_engine = trait_engine
        app.state.audit_chain = audit_chain
        # threading.Lock (not asyncio.Lock): sync route handlers run on the
        # FastAPI threadpool, so a thread-level lock is the right primitive.
        app.state.write_lock = threading.Lock()
        try:
            yield
        finally:
            registry.close()

    app = FastAPI(
        title="Forest Soul Forge",
        version="0.3.0-phase3",
        description=(
            "Local-first blue-team agent factory. Read-only API surface in v1 — "
            "canonical artifacts (soul.md, constitution.yaml, audit chain) are "
            "the source of truth; this daemon serves a SQLite index over them. "
            "Local model provider is the default (ADR-0008)."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=False,
        allow_methods=["GET", "PUT", "POST"],
        allow_headers=["*"],
    )
    app.include_router(health_router.router)
    app.include_router(agents_router.router)
    app.include_router(audit_router.router)
    app.include_router(runtime_router.router)
    app.include_router(traits_router.router)
    app.include_router(preview_router.router)
    app.include_router(writes_router.router)
    return app


# Module-level singleton for ``uvicorn forest_soul_forge.daemon.app:app``.
app = build_app()
