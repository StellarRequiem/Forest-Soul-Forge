"""Dependency-injection helpers for the daemon.

Shared objects live on ``app.state``:

* ``app.state.registry`` — a single open :class:`Registry` for the
  process. WAL mode means concurrent reads are fine; writes go through
  ``app.state.write_lock`` (thread-level) to keep the single-writer
  SQLite discipline.
* ``app.state.providers`` — the :class:`ProviderRegistry`.
* ``app.state.trait_engine`` — parsed trait tree, used by /birth.
* ``app.state.audit_chain`` — append-only chain, used by /birth /spawn /archive.
* ``app.state.write_lock`` — :class:`threading.RLock` serializing writes.
  Reentrant because nested skill runs (delegate.v1 invoking a target's
  skill from inside a caller's skill_run) re-acquire the same lock.

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


class ToolDispatcherUnavailable(RuntimeError):
    """Raised when the dispatcher's dependencies aren't all present.

    The HTTP-flavored ``get_tool_dispatcher`` maps this to 503.
    Non-HTTP callers (the scheduler, in particular) catch it and decide
    what to do — typically log + skip the dispatch.
    """


def build_or_get_tool_dispatcher(app):
    """Build a :class:`ToolDispatcher` from ``app.state`` (or return the cached one).

    Single source of truth for dispatcher construction. Two callers:

    * ``get_tool_dispatcher`` — per-request HTTP dependency.
    * ``forest_soul_forge.daemon.scheduler.task_types.tool_call`` —
      scheduler runner for ADR-0041 T3 ``tool_call`` tasks. The
      scheduler has no ``Request`` so it can't use the HTTP dep
      directly; it imports this helper instead.

    Raises :class:`ToolDispatcherUnavailable` if any of the required
    sub-systems failed to load at startup. Lifespan failures are
    surfaced via ``/healthz``; this helper just refuses to construct
    a half-wired dispatcher.
    """
    cached = getattr(app.state, "tool_dispatcher", None)
    if cached is not None:
        return cached

    registry = getattr(app.state, "tool_registry", None)
    audit = getattr(app.state, "audit_chain", None)
    fsf_registry = getattr(app.state, "registry", None)
    if registry is None or audit is None or fsf_registry is None:
        raise ToolDispatcherUnavailable(
            "tool dispatcher unavailable — one of "
            "tool_registry/audit_chain/registry failed to load at "
            "startup. Check /healthz for the underlying error."
        )
    # ADR-0019 T6: pull the loaded genre engine if it's there. Empty
    # engine (load failure / no genres.yaml) is benign — the dispatcher
    # treats a missing engine as 'no enforcement'.
    genre_engine = getattr(app.state, "genre_engine", None)
    # ADR-0022 v0.1: build a Memory bound to the registry's connection.
    # Single-writer SQLite discipline is preserved by the daemon's
    # write lock — the dispatcher only touches Memory while holding it.
    # ADR-0050 T4 (B269): when at-rest encryption is on (master key
    # available), wire the EncryptionConfig so memory writes encrypt
    # content + reads decrypt transparently. ``master_key`` is
    # populated by the lifespan only when FSF_AT_REST_ENCRYPTION=true
    # AND the master key resolved cleanly (otherwise the daemon
    # already raised at startup). None = pre-T4 plaintext path.
    from forest_soul_forge.core.memory import Memory
    _master_key = getattr(app.state, "master_key", None)
    _enc_config = None
    if _master_key is not None:
        from forest_soul_forge.core.at_rest_encryption import (
            EncryptionConfig as _EncryptionConfig,
        )
        _enc_config = _EncryptionConfig(master_key=_master_key)
    memory = Memory(
        conn=fsf_registry._conn,  # noqa: SLF001 — internal access by design
        encryption_config=_enc_config,
    )
    # ADR-0033 A6 + B3: PrivClient (or None) flows from app.state into
    # the dispatcher and is threaded into every ToolContext. The
    # privileged tools (isolate_process.v1, dynamic_policy.v1,
    # tamper_detect.v1's SIP path) refuse cleanly when ctx.priv_client
    # is None — the daemon stays up even when the helper isn't
    # installed.
    priv_client = getattr(app.state, "priv_client", None)
    from forest_soul_forge.tools.dispatcher import ToolDispatcher
    dispatcher = ToolDispatcher(
        registry=registry,
        audit=audit,
        counter_get=fsf_registry.get_tool_call_count,
        counter_inc=fsf_registry.increment_tool_call_count,
        # ADR-0019 T4: per-call accounting writer. Mirrors each
        # terminating dispatch event into the registry's tool_calls
        # table for queryable character-sheet roll-ups. The audit
        # chain is the source of truth; this is the indexed view.
        record_call=fsf_registry.record_tool_call,
        # ADR-0019 T3: approval-queue persistence. Writes one row per
        # call gated by requires_human_approval. The endpoints
        # (list/detail/approve/reject) read and mutate them.
        pending_writer=fsf_registry.record_pending_approval,
        # ADR-0019 T6: runtime enforcement of genre risk floor.
        # Companion → reject non-local provider; Observer → reject
        # non-read_only side effects; etc. None when genres.yaml
        # didn't load (best-effort: dispatch keeps working without
        # enforcement, character sheet shows the load failure).
        genre_engine=genre_engine,
        # ADR-0022 v0.1: memory subsystem. memory_recall.v1 + future
        # memory_write.v1 reach for ctx.memory; the dispatcher
        # threads this instance through every ToolContext.
        memory=memory,
        priv_client=priv_client,
        # ADR-003X Phase C6: agent Registry handle. suggest_agent.v1
        # enumerates agents from this; other tools that need to look
        # up siblings/lineage by id will share it.
        agent_registry=fsf_registry,
        # ADR-0043 T4.5 (Burst 107): plugin runtime. When the
        # daemon's lifespan successfully constructed it
        # (app.state.plugin_runtime), the dispatcher gets a
        # reference. mcp_call.v1 then sees plugin-registered MCP
        # servers via ctx.constraints["mcp_registry"]. None means
        # plugins aren't wired (lifespan load failure or test
        # context) — mcp_call falls back to its YAML-only loader.
        plugin_runtime=getattr(app.state, "plugin_runtime", None),
        # ADR-0043 follow-up #2 (Burst 113): post-birth plugin grants.
        # The Registry's PluginGrantsTable is the same SQLite
        # connection-bound accessor everything else uses; the
        # dispatcher reads active_plugin_names() once per dispatch to
        # union with constitution.allowed_mcp_servers. None when
        # fsf_registry is None (test contexts) — dispatcher falls
        # back to constitution-only allowlist.
        plugin_grants=(
            getattr(fsf_registry, "plugin_grants", None)
            if fsf_registry is not None else None
        ),
        # ADR-0060 T2 (B220): runtime catalog-tool grants accessor +
        # tool_catalog for default-constraint lookup. The dispatcher
        # uses both together — grants live in the registry, defaults
        # come from the catalog. Either being None makes
        # ConstraintResolutionStep skip the grant fallback and refuse
        # tool_not_in_constitution as pre-B220.
        catalog_grants=(
            getattr(fsf_registry, "catalog_grants", None)
            if fsf_registry is not None else None
        ),
        tool_catalog=getattr(app.state, "tool_catalog", None),
    )
    # ADR-0054 T6 (B194) — procedural-shortcut substrate wiring.
    # Master switch is ``settings.procedural_shortcut_enabled`` (default
    # False). When True, the dispatcher's ProceduralShortcutStep matches
    # llm_think dispatches against stored situation→action rows.
    # Closures (not constants) so an operator who flips the env var at
    # runtime + restarts sees the change without touching code; tests
    # can also override via direct assignment to settings.
    #
    # Per ADR-0054 D1 + ADR-0001 D2: shortcuts are per-instance state.
    # constitution_hash + DNA stay constant across the table's growth.
    settings_for_shortcuts = getattr(app.state, "settings", None)
    if settings_for_shortcuts is not None and fsf_registry is not None:
        try:
            from forest_soul_forge.registry.tables.procedural_shortcuts import (
                ProceduralShortcutsTable,
            )
            shortcuts_table = ProceduralShortcutsTable(fsf_registry._conn)
            dispatcher.procedural_shortcuts_table = shortcuts_table
            dispatcher.procedural_shortcut_enabled_fn = (
                lambda s=settings_for_shortcuts: bool(
                    getattr(s, "procedural_shortcut_enabled", False)
                )
            )
            dispatcher.procedural_cosine_floor_fn = (
                lambda s=settings_for_shortcuts: float(
                    getattr(s, "procedural_cosine_floor", 0.92)
                )
            )
            dispatcher.procedural_reinforcement_floor_fn = (
                lambda s=settings_for_shortcuts: int(
                    getattr(s, "procedural_reinforcement_floor", 2)
                )
            )
            dispatcher.procedural_embed_model_fn = (
                lambda s=settings_for_shortcuts: str(
                    getattr(s, "procedural_embed_model", "nomic-embed-text:latest")
                )
            )
        except Exception:
            # Defensive — any failure constructing the table or
            # wiring the closures must NOT crash the daemon's
            # startup. Dispatcher's _resolve_shortcut_match handles
            # a None table by short-circuiting to no-match.
            pass

    # ADR-0033 A3: build the cross-agent delegator factory now that
    # the dispatcher exists, then mutate the dispatcher to hold a
    # reference. Late binding because the factory captures the
    # dispatcher itself (so nested tool calls inside a delegated
    # skill go through the same dispatcher) — the chicken-and-egg
    # is resolved by constructing the dispatcher first and patching
    # the factory in. Same instance, no need for a setter on the
    # frozen dataclass: ToolDispatcher is `@dataclass` (mutable).
    settings = getattr(app.state, "settings", None)
    write_lock = getattr(app.state, "write_lock", None)
    if settings is not None and write_lock is not None:
        from forest_soul_forge.tools.delegator import build_delegator_factory
        dispatcher.delegator_factory = build_delegator_factory(
            registry=fsf_registry,
            audit_chain=audit,
            dispatcher=dispatcher,
            skill_install_dir=settings.skill_install_dir,
            write_lock=write_lock,
            provider_resolver=lambda: getattr(
                app.state, "active_provider", None,
            ),
        )
    app.state.tool_dispatcher = dispatcher
    return dispatcher


def get_tool_dispatcher(request: Request):
    """Return the shared :class:`ToolDispatcher` or 503 if unavailable.

    Per-request HTTP dependency. Delegates to
    :func:`build_or_get_tool_dispatcher`; that helper handles the
    lazy-build + cache-on-app.state pattern. Splitting the helper out
    lets the ADR-0041 scheduler reuse the same construction path
    without forging a fake ``Request`` object.
    """
    try:
        return build_or_get_tool_dispatcher(request.app)
    except ToolDispatcherUnavailable as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )


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
