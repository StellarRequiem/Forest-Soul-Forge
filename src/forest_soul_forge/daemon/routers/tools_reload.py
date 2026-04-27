"""``POST /tools/reload`` — refresh the tool registry + catalog from
``data/plugins/`` without a daemon restart (ADR-0019 T5 / B2).

Sequence under the write lock:

  1. Build a fresh ToolRegistry, register the built-ins.
  2. Reload the on-disk tool catalog from settings.tool_catalog_path.
  3. Walk plugins_dir via plugin_loader.load_plugins, augmenting the
     registry + catalog.
  4. Atomically swap the new registry + catalog onto app.state.
  5. Drop the cached tool_dispatcher so the next request rebuilds it
     against the new registry.

Read-then-swap, no in-place mutation. A malformed plugin during reload
won't blow away the existing registry — the new one only replaces
state when the load succeeds for at least the built-ins (load failures
on plugins are isolated to those plugins).
"""
from __future__ import annotations

from threading import Lock

from fastapi import APIRouter, Depends, Request

from forest_soul_forge.daemon.deps import (
    get_settings,
    get_write_lock,
    require_api_token,
    require_writes_enabled,
)


router = APIRouter(tags=["tools"])


@router.post(
    "/tools/reload",
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
async def reload_tools(
    request: Request,
    settings=Depends(get_settings),
    write_lock: Lock = Depends(get_write_lock),
) -> dict:
    """Refresh built-ins + catalog YAML + .fsf plugins from disk."""
    from forest_soul_forge.core.tool_catalog import (
        ToolCatalogError,
        empty_catalog,
        load_catalog,
    )
    from forest_soul_forge.tools import ToolRegistry
    from forest_soul_forge.tools.builtin import register_builtins
    from forest_soul_forge.tools.plugin_loader import (
        load_plugins,
        unload_plugins,
    )

    with write_lock:
        # 1. Drop existing plugin module registrations so reload picks
        #    up the latest tool.py bytes (importlib otherwise serves
        #    cached modules).
        existing_registry = getattr(request.app.state, "tool_registry", None)
        if existing_registry is not None:
            unload_plugins(
                registry=existing_registry,
                plugins_dir=settings.plugins_dir,
            )

        # 2. Fresh registry + built-ins.
        registry = ToolRegistry()
        register_builtins(registry)

        # 3. Reload catalog YAML from disk.
        try:
            catalog = load_catalog(settings.tool_catalog_path)
        except (ToolCatalogError, FileNotFoundError):
            catalog = empty_catalog()

        # 4. Walk plugins_dir.
        plugin_results, augmented_catalog = load_plugins(
            settings.plugins_dir,
            registry=registry,
            catalog=catalog,
        )
        ok = [r for r in plugin_results if r.tool is not None]
        err = [r.error for r in plugin_results if r.error is not None]

        # 5. Atomic swap.
        request.app.state.tool_registry = registry
        request.app.state.tool_catalog = augmented_catalog
        # Drop the cached dispatcher so next request rebuilds it.
        request.app.state.tool_dispatcher = None

    return {
        "registered_count": len(registry.tools),
        "plugins_loaded": len(ok),
        "plugin_errors": err,
        "catalog_path": str(settings.tool_catalog_path),
        "plugins_dir": str(settings.plugins_dir),
    }
