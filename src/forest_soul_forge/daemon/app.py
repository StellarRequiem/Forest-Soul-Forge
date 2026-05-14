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
from forest_soul_forge.daemon.routers import memory_consents as memory_consents_router
from forest_soul_forge.daemon.routers import pending_calls as pending_calls_router
from forest_soul_forge.daemon.routers import preview as preview_router
from forest_soul_forge.daemon.routers import runtime as runtime_router
from forest_soul_forge.daemon.routers import skills_catalog as skills_catalog_router
from forest_soul_forge.daemon.routers import skills_forge as skills_forge_router
from forest_soul_forge.daemon.routers import skills_reload as skills_reload_router
from forest_soul_forge.daemon.routers import skills_run as skills_run_router
from forest_soul_forge.daemon.routers import tools_forge as tools_forge_router
from forest_soul_forge.daemon.routers import tool_dispatch as tool_dispatch_router
from forest_soul_forge.daemon.routers import tools as tools_router
from forest_soul_forge.daemon.routers import conversations as conversations_router
from forest_soul_forge.daemon.routers import conversations_admin as conversations_admin_router
from forest_soul_forge.daemon.routers import hardware as hardware_router
from forest_soul_forge.daemon.routers import passport as passport_router
from forest_soul_forge.daemon.routers import (
    orchestrator as orchestrator_router,
)
from forest_soul_forge.daemon.routers import voice as voice_router
from forest_soul_forge.daemon.routers import (
    reality_anchor as reality_anchor_router,
)
from forest_soul_forge.daemon.routers import security as security_router
from forest_soul_forge.daemon.routers import tools_reload as tools_reload_router
from forest_soul_forge.daemon.routers import traits as traits_router
from forest_soul_forge.daemon.routers import triune as triune_router
from forest_soul_forge.daemon.routers import plugins as plugins_router
from forest_soul_forge.daemon.routers import (
    plugin_grants as plugin_grants_router,
)
from forest_soul_forge.daemon.routers import (
    catalog_grants as catalog_grants_router,
)
from forest_soul_forge.daemon.routers import (
    agent_posture as agent_posture_router,
)
from forest_soul_forge.daemon.routers import scheduler as scheduler_router
from forest_soul_forge.daemon.routers import cycles as cycles_router
from forest_soul_forge.daemon.routers import marketplace as marketplace_router
from forest_soul_forge.daemon.routers import secrets as secrets_router
from forest_soul_forge.daemon.routers import verifier as verifier_router
from forest_soul_forge.daemon.routers import writes as writes_router
from forest_soul_forge.registry import Registry


def _build_provider_registry(settings: DaemonSettings) -> ProviderRegistry:
    local = LocalProvider(
        base_url=settings.local_base_url,
        models=settings.local_model_map(),
        timeout_s=settings.local_timeout_s,
    )
    # B185 — secrets-store fallback for the frontier API key. The
    # operator's preferred storage is the macOS Keychain (or whatever
    # FSF_SECRET_STORE resolves to); env-var FSF_FRONTIER_API_KEY is
    # the override for ops cases that need explicit injection (CI,
    # headless containers, etc.). When the env is unset AND the
    # frontier provider is enabled, we look up
    # ``settings.frontier_api_key_secret_name`` in the resolved
    # secret store. Failure to read the store NEVER takes the daemon
    # down — the FrontierProvider just reports ``no API key
    # configured`` on dispatch and the local provider continues to
    # serve requests routed to it. ADR-0052 D2 invariance:
    # daemon-startup is local-first; remote credential reads are
    # opt-in.
    api_key = settings.frontier_api_key
    if settings.frontier_enabled and not api_key:
        try:
            from forest_soul_forge.security.secrets import resolve_secret_store
            store = resolve_secret_store()
            api_key = store.get(settings.frontier_api_key_secret_name)
        except Exception:
            api_key = None
    frontier = FrontierProvider(
        enabled=settings.frontier_enabled,
        base_url=settings.frontier_base_url,
        api_key=api_key,
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
        # ADR-0050 T2 (B267): at-rest encryption gate. When
        # FSF_AT_REST_ENCRYPTION=true the registry bootstrap uses
        # SQLCipher with the resolved master key. Default off
        # preserves pre-T2 behavior (plaintext SQLite). Master-key
        # resolution failure here is fatal under encryption-on (the
        # daemon would otherwise fall back to plaintext and silently
        # downgrade the operator's posture); the daemon refuses to
        # boot and the operator decides whether to fix the keystore
        # or turn off the env var.
        import os as _os_lifespan
        _at_rest_on = (
            _os_lifespan.environ.get("FSF_AT_REST_ENCRYPTION", "false")
            .strip().lower() == "true"
        )
        _registry_master_key: bytes | None = None
        if _at_rest_on:
            try:
                from forest_soul_forge.security.master_key import (
                    resolve_master_key as _resolve_master_key_early,
                )
                _registry_master_key = _resolve_master_key_early()
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(
                    f"FSF_AT_REST_ENCRYPTION=true but master key could "
                    f"not be resolved ({type(e).__name__}: {e}). "
                    "Either fix the keystore or unset the env var to "
                    "fall back to plaintext SQLite. See "
                    "docs/runbooks/encryption-at-rest.md (T7)."
                ) from e

        # Bootstrap the registry. If the file doesn't exist, it's created;
        # if it does, schema version is verified (mismatch -> raises).
        # With master_key set (encryption on), every per-thread
        # connection opens via sqlcipher3 + PRAGMA key. Without, stdlib
        # sqlite3 + plaintext file — bit-identical pre-T2 behavior.
        registry = Registry.bootstrap(
            settings.registry_db_path,
            master_key=_registry_master_key,
        )
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
        # Skill catalog (ADR-0031 T5) — same load discipline as the
        # tool catalog: missing dir or malformed manifests degrade to
        # an empty catalog, errors surface as a diagnostic.
        from forest_soul_forge.core.skill_catalog import (
            empty_catalog as empty_skill_catalog,
            load_catalog as load_skill_catalog,
        )
        try:
            skill_catalog, skill_errors = load_skill_catalog(
                settings.skill_install_dir,
            )
            if skill_errors:
                startup_diagnostics.append(
                    {"component": "skill_catalog", "status": "degraded",
                     "path": str(settings.skill_install_dir),
                     "error": "; ".join(skill_errors)}
                )
            else:
                startup_diagnostics.append(
                    {"component": "skill_catalog", "status": "ok",
                     "path": str(settings.skill_install_dir),
                     "error": None}
                )
        except Exception as e:
            skill_catalog = empty_skill_catalog()
            startup_diagnostics.append(
                {"component": "skill_catalog", "status": "failed",
                 "path": str(settings.skill_install_dir),
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
                # ADR-0050 T3 (B268): when FSF_AT_REST_ENCRYPTION=true,
                # wire the EncryptionConfig into the chain so new
                # entries get the AES-256-GCM envelope on disk. Mixed
                # legacy+encrypted chains are explicitly supported —
                # the existing plaintext genesis + any pre-T3 entries
                # round-trip through ``_entry_from_dict`` as they
                # always did; only entries appended AFTER this
                # wiring carry the envelope. ``_registry_master_key``
                # is non-None iff the operator opted into encryption
                # AND the master key resolved cleanly (otherwise the
                # daemon already raised at the T2 gate above).
                if _registry_master_key is not None:
                    from forest_soul_forge.core.at_rest_encryption import (
                        EncryptionConfig as _EncryptionConfig,
                    )
                    audit_chain.set_encryption(
                        _EncryptionConfig(master_key=_registry_master_key)
                    )
                    startup_diagnostics.append(
                        {"component": "audit_chain", "status": "ok",
                         "path": str(settings.audit_chain_path),
                         "error": None,
                         "at_rest_encryption": "AES-256-GCM (kid=master:default)"}
                    )
                else:
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

        # ADR-0019 T5: load .fsf plugins from data/plugins/. Each
        # plugin augments the in-memory tool_catalog so the dispatcher's
        # catalog cross-check accepts it. Plugin load failures are
        # isolated — one broken plugin doesn't keep the others (or the
        # daemon) down.
        from forest_soul_forge.tools.plugin_loader import load_plugins
        try:
            plugin_results, tool_catalog = load_plugins(
                settings.plugins_dir,
                registry=tool_registry,
                catalog=tool_catalog,
            )
            ok = [r for r in plugin_results if r.tool is not None]
            err = [r for r in plugin_results if r.tool is None]
            if err:
                startup_diagnostics.append(
                    {"component": "plugin_loader", "status": "degraded",
                     "path": str(settings.plugins_dir),
                     "error": "; ".join(r.error for r in err if r.error)}
                )
            else:
                startup_diagnostics.append(
                    {"component": "plugin_loader", "status": "ok",
                     "path": str(settings.plugins_dir),
                     "error": f"{len(ok)} plugin(s) loaded" if ok else None}
                )
        except Exception as e:
            startup_diagnostics.append(
                {"component": "plugin_loader", "status": "failed",
                 "path": str(settings.plugins_dir),
                 "error": f"{type(e).__name__}: {e}"}
            )

        # ADR-0058 / B202: walk data/forge/tools/installed/ and register
        # one PromptTemplateTool per spec.yaml. Forged tools are
        # operator-direct prompt-template wrappers — they were created
        # via /tools/forge + /tools/install at runtime; we replay them
        # at every lifespan so they survive daemon restarts.
        from pathlib import Path as _ForgedPath
        forged_tool_root = _ForgedPath(settings.tool_install_dir)
        forged_loaded = 0
        forged_errors: list[str] = []
        if forged_tool_root.exists():
            from forest_soul_forge.forge.prompt_tool_forge import (
                ToolSpecError,
                parse_spec,
            )
            from forest_soul_forge.tools.builtin.prompt_template_tool import (
                PromptTemplateTool,
            )
            from forest_soul_forge.tools.base import ToolError
            try:
                from forest_soul_forge.core.tool_catalog import ToolDef
                _tool_def_available = True
            except ImportError:
                _tool_def_available = False
            for spec_file in sorted(forged_tool_root.glob("*.yaml")):
                try:
                    spec = parse_spec(
                        spec_file.read_text(encoding="utf-8"),
                        forged_by="lifespan",
                        forge_provider="lifespan",
                    )
                    tool = PromptTemplateTool(
                        name=spec.name,
                        version=spec.version,
                        description=spec.description,
                        input_schema=spec.input_schema,
                        prompt_template=spec.prompt_template,
                        archetype_tags=spec.archetype_tags,
                        forged_by=spec.forged_by,
                    )
                    tool_registry.register(tool)
                    if _tool_def_available:
                        key = f"{spec.name}.v{spec.version}"
                        if key not in tool_catalog.tools:
                            tool_catalog.tools[key] = ToolDef(
                                name=spec.name,
                                version=spec.version,
                                description=spec.description,
                                input_schema=spec.input_schema,
                                side_effects=tool.side_effects,
                                archetype_tags=tuple(spec.archetype_tags),
                            )
                    forged_loaded += 1
                except (ToolSpecError, ToolError, Exception) as e:
                    forged_errors.append(f"{spec_file.name}: {type(e).__name__}: {e}")
        startup_diagnostics.append({
            "component": "forged_tool_loader",
            "status": "degraded" if forged_errors else "ok",
            "path": str(forged_tool_root),
            "error": "; ".join(forged_errors) if forged_errors else None,
            "loaded": forged_loaded,
        })

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

        # ADR-0033 A6 + B3: PrivClient lifespan wiring. Off by default;
        # operators flip FSF_ENABLE_PRIV_CLIENT=true after running the
        # sudo-helper-install runbook. When on, we construct the client
        # and call assert_available() — a missing helper is recorded as a
        # startup diagnostic but does NOT abort boot, since read_only
        # tools keep working and the privileged tools (isolate_process,
        # dynamic_policy, tamper_detect SIP path) refuse cleanly with
        # "no PrivClient wired."
        priv_client = None
        if settings.enable_priv_client:
            from forest_soul_forge.security.priv_client import (
                HelperMissing,
                PrivClient,
            )
            try:
                pc = PrivClient(helper_path=settings.priv_helper_path)
                pc.assert_available()
                priv_client = pc
                startup_diagnostics.append(
                    {"component": "priv_client", "status": "ok",
                     "path": settings.priv_helper_path, "error": None}
                )
            except HelperMissing as e:
                startup_diagnostics.append(
                    {"component": "priv_client", "status": "degraded",
                     "path": settings.priv_helper_path,
                     "error": (
                         f"helper unavailable — {e}. Privileged tools "
                         "will refuse cleanly until the helper is "
                         "installed and the daemon is restarted."
                     )}
                )
            except Exception as e:
                startup_diagnostics.append(
                    {"component": "priv_client", "status": "failed",
                     "path": settings.priv_helper_path,
                     "error": f"{type(e).__name__}: {e}"}
                )
        else:
            startup_diagnostics.append(
                {"component": "priv_client", "status": "disabled",
                 "path": settings.priv_helper_path,
                 "error": "FSF_ENABLE_PRIV_CLIENT=false (default)"}
            )

        # B148 (T25 — security hardening): auto-generate FSF_API_TOKEN
        # if unset, so write endpoints aren't accessible to any local
        # process by default. Operator can explicitly opt out via
        # FSF_INSECURE_NO_TOKEN=true (matches the FSF_ENABLE_PRIV_CLIENT
        # opt-in/out shape).
        #
        # The 2026-05-05 outside security review flagged the optional-
        # token default as the highest-exposure architectural hole:
        # browser extensions, sibling daemons, malware, all could
        # approve tools or fire writes without auth. Auto-generation
        # closes this without breaking the frictionless-local-dev UX
        # — operators who don't care just keep working with the
        # generated token written to .env on first start.
        if not settings.api_token:
            if settings.insecure_no_token:
                startup_diagnostics.append(
                    {"component": "auth",
                     "status": "INSECURE_no_token",
                     "warning": (
                         "FSF_INSECURE_NO_TOKEN=true set — write "
                         "endpoints accept ANY local request. Set "
                         "FSF_API_TOKEN=$(openssl rand -hex 16) in "
                         ".env to harden."
                     )}
                )
            else:
                import secrets as _secrets
                from datetime import datetime as _dt, timezone as _tz
                from pathlib import Path as _Path
                _token = _secrets.token_hex(16)
                _env_file = _Path(".env")
                _wrote_to_disk = False
                try:
                    with _env_file.open("a") as _f:
                        _f.write(
                            f"\n"
                            f"# Auto-generated by daemon on first boot "
                            f"(B148, T25 — security hardening).\n"
                            f"# Generated at "
                            f"{_dt.now(_tz.utc).isoformat()}\n"
                            f"# To opt out of token auth, set "
                            f"FSF_INSECURE_NO_TOKEN=true and remove "
                            f"this line.\n"
                            f"# To rotate, replace the value below + "
                            f"restart the daemon.\n"
                            f"FSF_API_TOKEN={_token}\n"
                        )
                    _wrote_to_disk = True
                except OSError as _e:
                    startup_diagnostics.append(
                        {"component": "auth",
                         "status": "auto_token_ephemeral",
                         "warning": (
                             f"FSF_API_TOKEN auto-generated but couldn't "
                             f"write to {_env_file.resolve()}: {_e}. "
                             f"Token is in-memory only; will regenerate "
                             f"on next restart."
                         ),
                         "token_value": _token}
                    )
                if _wrote_to_disk:
                    startup_diagnostics.append(
                        {"component": "auth",
                         "status": "auto_token_persisted",
                         "message": (
                             f"FSF_API_TOKEN auto-generated and "
                             f"appended to {_env_file.resolve()}. All "
                             f"write endpoints now require X-FSF-Token "
                             f"header matching the generated token."
                         )}
                    )
                # Mutate the live settings instance so the rest of
                # this run uses the generated token.
                settings.api_token = _token
        else:
            startup_diagnostics.append(
                {"component": "auth",
                 "status": "ok",
                 "message": "FSF_API_TOKEN set; write endpoints require X-FSF-Token."}
            )

        app.state.settings = settings
        app.state.registry = registry
        app.state.providers = providers
        app.state.trait_engine = trait_engine
        app.state.audit_chain = audit_chain

        # ADR-0050 T1 (B266) — resolve the at-rest encryption master
        # key. The substrate is opt-in by ADR Decision 6 ("mixed legacy
        # / encrypted chain — no rewrites"); T1 stands up the
        # key-management surface so downstream tranches (T2 SQLCipher,
        # T3 audit-chain per-event encryption, T4 memory body
        # encryption) can consume it. Caches under the OS keychain
        # on darwin, file-backed elsewhere. Failure here is non-fatal —
        # the daemon proceeds without master-key-backed encryption and
        # the operator sees the diagnostic. Strict-mode daemons (a
        # future ADR-0050 T6) will refuse to boot when the key can't
        # be obtained.
        # ADR-0050 T4 (B269) gate refinement: only stash the master
        # key on app.state when FSF_AT_REST_ENCRYPTION is on. Pre-T4
        # B266 set it unconditionally on any successful resolution,
        # which would have caused deps.py's Memory + T3's audit chain
        # wiring to silently start encrypting even when the operator
        # didn't opt in. Now the env-var gate is authoritative: env
        # var off = master_key never reaches app.state, and every
        # downstream consumer's ``getattr(app.state, "master_key",
        # None)`` returns None.
        if _at_rest_on:
            try:
                from forest_soul_forge.security.master_key import (
                    configured_backend_name as _configured_master_backend,
                    resolve_master_key as _resolve_master_key,
                )
                # ``_registry_master_key`` was already resolved above as
                # part of the T2 gate; reuse it so we don't double-load.
                # If the env var is on but the T2 path skipped key
                # resolution (e.g. tests inject a daemon without the
                # T2 block), resolve here as a safety net.
                _master_key = (
                    _registry_master_key
                    if _registry_master_key is not None
                    else _resolve_master_key()
                )
                app.state.master_key = _master_key
                startup_diagnostics.append(
                    {"component": "encryption_at_rest",
                     "status": "ok",
                     "message": (
                         f"master key loaded from {_configured_master_backend()} "
                         "backend; consumers (registry SQLCipher / audit-chain / "
                         "memory body) wire up via the same env var."
                     )}
                )
            except Exception as e:  # noqa: BLE001
                app.state.master_key = None
                startup_diagnostics.append(
                    {"component": "encryption_at_rest",
                     "status": "degraded",
                     "message": (
                         f"FSF_AT_REST_ENCRYPTION=true but master key "
                         f"unavailable: {type(e).__name__}: {e}. Daemon "
                         "will run with the legacy plaintext posture."
                     )}
                )
        else:
            app.state.master_key = None
            startup_diagnostics.append(
                {"component": "encryption_at_rest",
                 "status": "off",
                 "message": (
                     "FSF_AT_REST_ENCRYPTION not set; legacy plaintext "
                     "posture (registry, audit chain, memory bodies)."
                 )}
            )
        # ADR-0049 T5+T6 (B244): wire the sign-on-emit + verify-on-replay
        # closures into the audit chain. The closures resolve agent_dna
        # → instance_id → key (private for signing, public for verify)
        # via the registry's agents table + the AgentKeyStore. Wired
        # here (lifespan) so core/audit_chain.py stays decoupled from
        # both the registry and the keystore — both are runtime
        # dependencies that core shouldn't know about. Failure to
        # install the closures (registry unavailable, etc.) is non-
        # fatal — the chain still hashes correctly without them, just
        # without the per-event signature property.
        if audit_chain is not None and registry is not None:
            try:
                from forest_soul_forge.security.keys import (
                    resolve_agent_key_store,
                )
                from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                    Ed25519PrivateKey, Ed25519PublicKey,
                )
                from cryptography.exceptions import InvalidSignature
                import base64 as _b64_lifespan

                _agent_key_store = resolve_agent_key_store()

                def _instance_id_for_dna(agent_dna: str) -> str | None:
                    """Resolve agent_dna → instance_id via the agents
                    table. The dna column is the short form (12 char);
                    the audit chain stores the short form too."""
                    try:
                        row = registry._conn.execute(
                            "SELECT instance_id FROM agents "
                            "WHERE dna = ? LIMIT 1;",
                            (agent_dna,),
                        ).fetchone()
                    except Exception:
                        return None
                    if row is None:
                        return None
                    return row[0]

                def _public_key_for_dna(agent_dna: str) -> bytes | None:
                    """Resolve agent_dna → agents.public_key (base64
                    string) → raw 32-byte ed25519 public-key bytes."""
                    try:
                        row = registry._conn.execute(
                            "SELECT public_key FROM agents "
                            "WHERE dna = ? LIMIT 1;",
                            (agent_dna,),
                        ).fetchone()
                    except Exception:
                        return None
                    if row is None or row[0] is None:
                        return None
                    try:
                        return _b64_lifespan.b64decode(
                            row[0].encode("ascii"), validate=True,
                        )
                    except Exception:
                        return None

                def _signer(entry_hash_bytes: bytes, agent_dna: str) -> bytes | None:
                    """Look up the agent's private key + sign
                    entry_hash. Returns None when the agent has no
                    keypair on file (legacy pre-ADR-0049 agent), so
                    the chain entry lands unsigned and the verifier
                    treats it as legacy."""
                    instance_id = _instance_id_for_dna(agent_dna)
                    if instance_id is None:
                        return None
                    priv_bytes = _agent_key_store.fetch(instance_id)
                    if priv_bytes is None:
                        return None
                    try:
                        priv = Ed25519PrivateKey.from_private_bytes(priv_bytes)
                        return priv.sign(entry_hash_bytes)
                    except Exception:
                        return None

                def _verifier(
                    entry_hash_bytes: bytes,
                    signature_bytes: bytes,
                    agent_dna: str,
                ) -> bool:
                    pub_bytes = _public_key_for_dna(agent_dna)
                    if pub_bytes is None:
                        # No public key on file — entry SHOULD NOT
                        # have a signature in that case. Treat as
                        # invalid: an attacker who attached a sig to
                        # a legacy agent's entry shouldn't pass.
                        return False
                    try:
                        pub = Ed25519PublicKey.from_public_bytes(pub_bytes)
                        pub.verify(signature_bytes, entry_hash_bytes)
                        return True
                    except InvalidSignature:
                        return False
                    except Exception:
                        return False

                audit_chain.set_signer(_signer)
                audit_chain.set_verifier(_verifier)
                startup_diagnostics.append(
                    {"component": "audit_chain_signer",
                     "status": "ok",
                     "message": "ed25519 sign/verify wired (ADR-0049 T5+T6)"}
                )
            except Exception as e:
                startup_diagnostics.append(
                    {"component": "audit_chain_signer",
                     "status": "failed",
                     "error": f"{type(e).__name__}: {e}"}
                )
        app.state.tool_catalog = tool_catalog
        app.state.genre_engine = genre_engine
        app.state.tool_registry = tool_registry
        app.state.skill_catalog = skill_catalog
        app.state.priv_client = priv_client
        app.state.startup_diagnostics = startup_diagnostics
        # ADR-003Y Y5: ambient mode rate. Operator sets via env var
        # FSF_AMBIENT_RATE in {minimal, normal, heavy}; default
        # 'minimal' (1 ambient nudge per agent per conversation per
        # day). Per ADR-003Y the default is intentionally low — the
        # operator turns it up deliberately.
        import os as _os
        _ambient_rate_env = (_os.environ.get("FSF_AMBIENT_RATE") or "minimal").lower()
        if _ambient_rate_env not in ("minimal", "normal", "heavy"):
            _ambient_rate_env = "minimal"
        app.state.ambient_rate = _ambient_rate_env
        # threading.RLock (not asyncio.Lock): sync route handlers run on
        # the FastAPI threadpool, so a thread-level lock is the right
        # primitive. RLock (reentrant) — not plain Lock — because the
        # delegator acquires this same lock when running a target's
        # skill nested inside a caller's skill_run. Plain Lock would
        # deadlock the worker thread on the second acquisition. RLock
        # tracks ownership + a per-thread acquire count, releases when
        # the count hits zero. Discovered live during the ADR-0033
        # Phase E smoke when the canonical chain hung at the first
        # delegate.v1 call.
        app.state.write_lock = threading.RLock()

        # ADR-0041 T2 — scheduler. Started here, stopped in finally.
        # ADR-0041 scheduler. T2 (Burst 86) shipped runtime + lifespan
        # integration. T3 (Burst 89, this) registers the tool_call
        # task type so scheduled tool dispatches can fire — closes
        # ADR-0036 T4 (verifier scheduled scans). The runner uses the
        # standard ToolDispatcher, so all governance applies.
        from forest_soul_forge.daemon.scheduler import (
            Scheduler,
            build_task_from_config,
        )
        from forest_soul_forge.daemon.scheduler.persistence import (
            SchedulerStateRepo,
        )
        from forest_soul_forge.daemon.scheduler.task_types import (
            scenario_runner,
            tool_call_runner,
        )
        scheduler_enabled = (_os.environ.get("FSF_SCHEDULER_ENABLED") or "true").lower() == "true"
        scheduler_poll_interval = float(_os.environ.get("FSF_SCHEDULER_POLL_INTERVAL_SECONDS") or "30")
        # ADR-0041 T5 (Burst 90): persistence over the registry's
        # connection. Scheduler.start() reads to hydrate in-memory
        # state; _dispatch upserts after every outcome under the
        # write_lock. Schema v13 added scheduled_task_state.
        scheduler_state_repo = SchedulerStateRepo(registry._conn)  # noqa: SLF001
        scheduler = Scheduler(
            poll_interval_seconds=scheduler_poll_interval,
            context={
                # ``app`` is in the context so runners can reach
                # lazily-built subsystems (notably the tool dispatcher
                # via build_or_get_tool_dispatcher). Holding a ref is
                # safe — the scheduler's lifecycle is bounded by the
                # app's lifespan.
                "app": app,
                "registry": registry,
                "audit_chain": audit_chain,
                "tool_registry": tool_registry,
                "providers": providers,
                "settings": settings,
            },
            state_repo=scheduler_state_repo,
        )
        # Register task-type runners. Adding a new runner is a
        # one-line change here; the runner module owns its own
        # config validation + outcome shape.
        scheduler.register_task_type("tool_call", tool_call_runner)
        # ADR-0041 T4 (Burst 93): scenario task type. YAML-driven
        # multi-step workflows. Step types in v0.4: dispatch_tool,
        # read_file, write_file, iterate.
        scheduler.register_task_type("scenario", scenario_runner)
        # Optional config file load — silent skip if absent.
        scheduler_config_path = settings.scheduled_tasks_path
        if scheduler_config_path.exists():
            import yaml as _yaml
            try:
                cfg = _yaml.safe_load(scheduler_config_path.read_text()) or {}
                for spec in cfg.get("tasks", []):
                    try:
                        scheduler.add_task(build_task_from_config(spec))
                    except Exception as e:
                        startup_diagnostics.append(
                            {"component": "scheduler",
                             "status": "task_skipped",
                             "task_id": spec.get("id", "<unknown>"),
                             "error": str(e)}
                        )
                startup_diagnostics.append(
                    {"component": "scheduler",
                     "status": "ok" if scheduler_enabled else "disabled",
                     "task_count": len(scheduler.list_tasks()),
                     "config_path": str(scheduler_config_path)}
                )
            except Exception as e:
                startup_diagnostics.append(
                    {"component": "scheduler",
                     "status": "config_load_failed",
                     "config_path": str(scheduler_config_path),
                     "error": str(e)}
                )
        else:
            startup_diagnostics.append(
                {"component": "scheduler",
                 "status": "ok" if scheduler_enabled else "disabled",
                 "task_count": 0,
                 "config_path": f"{scheduler_config_path} (absent)"}
            )

        if scheduler_enabled:
            await scheduler.start()
        app.state.scheduler = scheduler

        # ADR-0043 T3 (Burst 105): plugin runtime. Walks
        # ~/.forest/plugins/installed/ on startup and exposes the
        # /plugins HTTP surface. Daemon-internal mutations grab
        # write_lock before the runtime touches disk.
        #
        # B199 (2026-05-08): the initial reload INSIDE
        # build_plugin_runtime emits one ``plugin_installed`` event
        # per plugin found on disk. The previous implementation skipped
        # write_lock here on the assumption that "lifespan owns the
        # only handle and there are no concurrent writers yet" — but
        # the scheduler started ticking on line 618 and is already
        # writing scheduled_task_dispatched events to the same chain.
        # Two writers, neither holding write_lock, racing the same
        # head pointer: that is exactly the fork pattern at chain
        # seqs 3728/3735-3738/3740 in
        # docs/audits/2026-05-08-chain-fork-incident.md. The chain's
        # internal lock (B199 Layer 2) now prevents the fork on the
        # disk artifact, but acquiring write_lock here also keeps
        # the cross-resource discipline honest — chain + plugin
        # filesystem state should advance together.
        try:
            from pathlib import Path as _Path
            from forest_soul_forge.daemon.plugins_runtime import (
                build_plugin_runtime,
            )
            plugin_root_override = _os.environ.get("FSF_PLUGIN_ROOT")
            with app.state.write_lock:
                plugin_runtime = build_plugin_runtime(
                    plugin_root=(
                        _Path(plugin_root_override) if plugin_root_override else None
                    ),
                    audit_chain=audit_chain,
                )
            app.state.plugin_runtime = plugin_runtime
            startup_diagnostics.append({
                "component": "plugin_runtime",
                "status": "ok",
                "active_count": len(plugin_runtime.active()),
                "disabled_count": len(plugin_runtime.disabled()),
                "plugin_root": str(plugin_runtime.repository.directories.root),
            })
        except Exception as e:
            startup_diagnostics.append({
                "component": "plugin_runtime",
                "status": "load_failed",
                "error": str(e),
            })
            app.state.plugin_runtime = None

        try:
            yield
        finally:
            try:
                await scheduler.stop()
            except Exception:
                pass
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
    app.include_router(tools_reload_router.router)
    app.include_router(pending_calls_router.router)
    app.include_router(skills_run_router.router)
    app.include_router(skills_catalog_router.router)
    app.include_router(skills_reload_router.router)
    app.include_router(skills_forge_router.router)  # ADR-0057 B201
    app.include_router(tools_forge_router.router)  # ADR-0058 B202
    app.include_router(genres_router.router)
    app.include_router(memory_consents_router.router)
    app.include_router(verifier_router.router)
    app.include_router(character_sheet_router.router)
    app.include_router(preview_router.router)
    app.include_router(writes_router.router)
    app.include_router(triune_router.router)
    app.include_router(hardware_router.router)
    app.include_router(passport_router.router)  # ADR-0061 T6 (B248)
    app.include_router(reality_anchor_router.router)  # ADR-0063 T7 (B256)
    app.include_router(orchestrator_router.router)    # ADR-0067 T8 (B285)
    app.include_router(voice_router.router)           # ADR-0070 T2 (B287)
    app.include_router(security_router.router)  # ADR-0062 T6 (B258)
    app.include_router(conversations_router.router)
    app.include_router(conversations_admin_router.router)
    app.include_router(scheduler_router.router)
    app.include_router(plugins_router.router)
    app.include_router(plugin_grants_router.router)
    app.include_router(catalog_grants_router.router)  # ADR-0060 T3 (B220)
    app.include_router(agent_posture_router.router)
    app.include_router(secrets_router.router)
    app.include_router(marketplace_router.router)
    app.include_router(cycles_router.router)
    return app


# Module-level singleton for ``uvicorn forest_soul_forge.daemon.app:app``.
app = build_app()
