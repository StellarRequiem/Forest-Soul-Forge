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
from forest_soul_forge.daemon.routers import character_sheet as character_sheet_router
from forest_soul_forge.daemon.routers import genres as genres_router
from forest_soul_forge.daemon.routers import preview as preview_router
from forest_soul_forge.daemon.routers import runtime as runtime_router
from forest_soul_forge.daemon.routers import tool_dispatch as tool_dispatch_router
from forest_soul_forge.daemon.routers import tools as tools_router
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
        #
        # Each load attempt is recorded into ``startup_diagnostics`` so
        # /healthz can surface the actual exception type + message when a
        # write endpoint later 503s. Without this, a load failure shows
        # up as the misleading "trait engine not available (check
        # FSF_TRAIT_TREE_PATH)" message and the operator chases a path
        # issue when the real cause was (for example) a permission bit.
        # Caught one of these on 2026-04-25 — chmod-on-COPY race in the
        # Dockerfile — that wasted ~30 minutes diagnosing.
        startup_diagnostics: list[dict] = []
        trait_engine: TraitEngine | None = None
        audit_chain: AuditChain | None = None
        # Tool catalog (ADR-0018) — loaded best-effort. A missing or
        # malformed catalog falls back to empty so /birth still works
        # for callers who don't override tools.
        from forest_soul_forge.core.tool_catalog import (
            ToolCatalogError,
            empty_catalog,
            load_catalog,
        )
        try:
            tool_catalog = load_catalog(settings.tool_catalog_path)
            startup_diagnostics.append(
                {"component": "tool_catalog", "status": "ok",
                 "path": str(settings.tool_catalog_path), "error": None}
            )
        except ToolCatalogError as e:
            tool_catalog = empty_catalog()
            startup_diagnostics.append(
                {"component": "tool_catalog", "status": "failed",
                 "path": str(settings.tool_catalog_path),
                 "error": f"{type(e).__name__}: {e}"}
            )
        except FileNotFoundError as e:
            tool_catalog = empty_catalog()
            startup_diagnostics.append(
                {"component": "tool_catalog", "status": "failed",
                 "path": str(settings.tool_catalog_path),
                 "error": f"FileNotFoundError: {e}"}
            )

        # Genre engine (ADR-0021) — same load discipline as tool_catalog:
        # missing or malformed file degrades to empty engine so /birth
        # keeps working (agents just get genre=None). After load, run
        # validate_against_trait_engine to catch the common case where
        # a new role is added to trait_tree.yaml but not yet claimed
        # by any genre — surface as a startup diagnostic, not a hard
        # failure. Operators see the lag in /healthz and can fix it
        # without restarting if they accept the warning.
        from forest_soul_forge.core.genre_engine import (
            GenreEngineError,
            empty_engine as empty_genre_engine,
            load_genres,
            validate_against_trait_engine,
        )
        try:
            genre_engine = load_genres(settings.genres_path)
            startup_diagnostics.append(
                {"component": "genre_engine", "status": "ok",
                 "path": str(settings.genres_path), "error": None}
            )
        except (GenreEngineError, FileNotFoundError) as e:
            genre_engine = empty_genre_engine()
            startup_diagnostics.append(
                {"component": "genre_engine", "status": "failed",
                 "path": str(settings.genres_path),
                 "error": f"{type(e).__name__}: {e}"}
            )
        if settings.allow_write_endpoints:
            try:
                trait_engine = TraitEngine(settings.trait_tree_path)
                startup_diagnostics.append(
                    {"component": "trait_engine", "status": "ok",
                     "path": str(settings.trait_tree_path), "error": None}
                )
            except Exception as e:
                # Tolerate missing trait tree at startup — endpoints that
                # need it will 503. Read-only endpoints still work.
                trait_engine = None
                startup_diagnostics.append(
                    {"component": "trait_engine", "status": "failed",
                     "path": str(settings.trait_tree_path),
                     "error": f"{type(e).__name__}: {e}"}
                )
            try:
                audit_chain = AuditChain(settings.audit_chain_path)
                startup_diagnostics.append(
                    {"component": "audit_chain", "status": "ok",
                     "path": str(settings.audit_chain_path), "error": None}
                )
            except Exception as e:
                audit_chain = None
                startup_diagnostics.append(
                    {"component": "audit_chain", "status": "failed",
                     "path": str(settings.audit_chain_path),
                     "error": f"{type(e).__name__}: {e}"}
                )

        # ADR-0019 T1: tool runtime registry. Loads built-in tools
        # at lifespan; future T5 (.fsf plugins) extends this with
        # operator-installed tools. Failure to register a built-in
        # is fatal — it means a catalog/runtime mismatch the operator
        # needs to know about. We surface it on /healthz rather than
        # 503'ing the whole daemon (read-only endpoints stay up).
        from forest_soul_forge.tools.base import ToolError, empty_registry
        from forest_soul_forge.tools.builtin import register_builtins
        try:
            from forest_soul_forge.tools import ToolRegistry
            tool_registry = ToolRegistry()
            register_builtins(tool_registry)
            # Cross-check: every registered tool's (name, version) +
            # side_effects must match a real catalog entry. Catches the
            # "registered v1 but catalog only has v2" class of mistake
            # at boot, not at first dispatch.
            mismatches: list[str] = []
            for key, tool in tool_registry.tools.items():
                td = tool_catalog.tools.get(key)
                if td is None:
                    # Tool registered but not in catalog. Tolerable in
                    # principle (operator could ship a plugin not yet
                    # in the catalog YAML), but worth flagging.
                    mismatches.append(
                        f"{key}: not in tool_catalog.yaml (catalog has {sorted(tool_catalog.tools.keys())})"
                    )
                    continue
                if td.side_effects != tool.side_effects:
                    mismatches.append(
                        f"{key}: side_effects mismatch — registry={tool.side_effects} catalog={td.side_effects}"
                    )
            if mismatches:
                startup_diagnostics.append(
                    {"component": "tool_runtime", "status": "failed",
                     "path": None,
                     "error": "tool registry / catalog mismatch: " + "; ".join(mismatches)}
                )
            else:
                startup_diagnostics.append(
                    {"component": "tool_runtime", "status": "ok",
                     "path": None, "error": None}
                )
        except (ToolError, Exception) as e:
            tool_registry = empty_registry()
            startup_diagnostics.append(
                {"component": "tool_runtime", "status": "failed",
                 "path": None,
                 "error": f"{type(e).__name__}: {e}"}
            )

        # ADR-0021 invariant: every TraitEngine role must be claimed by
        # some genre. Skip when either engine failed to load — running
        # the check in that case would just produce noise on top of the
        # actual load failure. We surface unclaimed roles as a separate
        # startup_diagnostic entry rather than failing hard, because a
        # new role added to trait_tree.yaml without a matching genre
        # claim is an authoring lag, not an unrecoverable error.
        if trait_engine is not None and genre_engine.genres:
            unclaimed = validate_against_trait_engine(
                genre_engine,
                list(trait_engine.roles.keys()),
            )
            if unclaimed:
                startup_diagnostics.append(
                    {"component": "genre_engine_invariant",
                     "status": "failed",
                     "path": str(settings.genres_path),
                     "error": (
                         f"ADR-0021 invariant violated: roles in trait_tree "
                         f"unclaimed by any genre: {unclaimed}. Births of "
                         f"these roles will produce genre=None."
                     )}
                )
            else:
                startup_diagnostics.append(
                    {"component": "genre_engine_invariant",
                     "status": "ok",
                     "path": str(settings.genres_path), "error": None}
                )

        app.state.settings = settings
        app.state.registry = registry
        app.state.providers = providers
        app.state.trait_engine = trait_engine
        app.state.audit_chain = audit_chain
        app.state.tool_catalog = tool_catalog
        app.state.genre_engine = genre_engine
        app.state.tool_registry = tool_registry
        app.state.startup_diagnostics = startup_diagnostics
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
    app.include_router(tools_router.router)
    app.include_router(tool_dispatch_router.router)
    app.include_router(genres_router.router)
    app.include_router(character_sheet_router.router)
    app.include_router(preview_router.router)
    app.include_router(writes_router.router)
    return app


# Module-level singleton for ``uvicorn forest_soul_forge.daemon.app:app``.
app = build_app()
