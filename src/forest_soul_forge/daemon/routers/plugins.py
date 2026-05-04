"""``/plugins`` router — ADR-0043 T3 daemon HTTP surface.

GET surface (ungated, same posture as /audit + /healthz):
  GET /plugins                    — list all (active + disabled)
  GET /plugins/{name}             — one plugin's manifest + state

Mutating surface (require_writes_enabled + require_api_token,
same posture as the writes routes and the scheduler control
endpoints from ADR-0041 T6):
  POST /plugins/reload            — re-walk installed/ + diff
  POST /plugins/{name}/enable     — move disabled/<n>/ → installed/
  POST /plugins/{name}/disable    — move installed/<n>/ → disabled/
  POST /plugins/{name}/verify     — re-check entry-point sha256

POSTs hold the daemon's ``app.state.write_lock`` for the
filesystem mutation. The lock is RLock-based so a hot-reload
that internally calls into the repository doesn't deadlock.

Audit emit (the 6 ``plugin_*`` events from ADR-0043) lands in
T4 / Burst 106. Today the routes execute the operation but
don't yet write evidence to the chain.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from forest_soul_forge.daemon.deps import (
    require_api_token,
    require_writes_enabled,
)
from forest_soul_forge.daemon.plugins_runtime import PluginRuntime
from forest_soul_forge.plugins import PluginInfo, PluginState
from forest_soul_forge.plugins.errors import (
    PluginError,
    PluginNotFound,
    PluginValidationError,
)


router = APIRouter(tags=["plugins"])


def _runtime(request: Request) -> PluginRuntime:
    rt = getattr(request.app.state, "plugin_runtime", None)
    if rt is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "plugin runtime not initialized — daemon lifespan "
                "may have failed to construct it. Check /healthz "
                "for the underlying error."
            ),
        )
    return rt


def _serialize(info: PluginInfo) -> dict[str, Any]:
    """JSON-friendly view of one plugin. Mirrors the CLI's
    ``fsf plugin info --json`` output so scripts can use either
    surface interchangeably."""
    m = info.manifest
    return {
        "name": info.name,
        "state": info.state.value,
        "directory": str(info.directory),
        "manifest": {
            "schema_version": m.schema_version,
            "name": m.name,
            "display_name": m.display_label(),
            "version": m.version,
            "type": m.type.value,
            "author": m.author,
            "license": m.license,
            "side_effects": m.side_effects.value,
            "capabilities": list(m.capabilities),
            "requires_human_approval": dict(m.requires_human_approval),
            "entry_point": {
                "type": m.entry_point.type.value,
                "command": m.entry_point.command,
                "args": list(m.entry_point.args),
                "sha256": m.entry_point.sha256,
            },
            "required_secrets": [
                {
                    "name": s.name,
                    "description": s.description,
                    "env_var": s.env_var,
                }
                for s in m.required_secrets
            ],
            "verified_at": m.verified_at,
            "verified_by_sha256": m.verified_by_sha256,
        },
    }


# ---- read ---------------------------------------------------------------

@router.get("/plugins")
def list_plugins(request: Request) -> dict[str, Any]:
    """List every plugin (active + disabled) with full manifest +
    filesystem state. The response shape includes the plugin
    runtime's MCP-bridge view at the top level so operators can see
    what's actually wired through to mcp_call.v1.
    """
    rt = _runtime(request)
    all_plugins = rt.all()
    return {
        "count": len(all_plugins),
        "active_count": sum(
            1 for p in all_plugins if p.state == PluginState.INSTALLED
        ),
        "disabled_count": sum(
            1 for p in all_plugins if p.state == PluginState.DISABLED
        ),
        "plugins": [_serialize(p) for p in all_plugins],
        "mcp_servers_view": rt.mcp_servers_view(),
    }


@router.get("/plugins/{name}")
def get_plugin(name: str, request: Request) -> dict[str, Any]:
    rt = _runtime(request)
    try:
        info = rt.get(name)
    except PluginNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no plugin named {name!r}",
        )
    return _serialize(info)


# ---- mutate (gated) -----------------------------------------------------

@router.post(
    "/plugins/reload",
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
def reload_plugins(request: Request) -> dict[str, Any]:
    """Re-walk the plugin directory + update the in-process view.

    Returns a structured diff: which plugins were added (newly
    appeared in installed/), removed (gone from installed/),
    updated (manifest version or sha256 changed), and any per-
    plugin errors.
    """
    rt = _runtime(request)
    write_lock = getattr(request.app.state, "write_lock", None)
    if write_lock is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="write_lock not on app.state",
        )
    with write_lock:
        result = rt.reload()
    return {
        "ok": not bool(result.errors),
        **result.to_dict(),
        "active_count": len(rt.active()),
        "disabled_count": len(rt.disabled()),
    }


@router.post(
    "/plugins/{name}/enable",
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
def enable_plugin(name: str, request: Request) -> dict[str, Any]:
    rt = _runtime(request)
    write_lock = getattr(request.app.state, "write_lock", None)
    if write_lock is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="write_lock not on app.state",
        )
    try:
        with write_lock:
            info = rt.enable(name)
    except PluginNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no plugin named {name!r}",
        )
    except PluginError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    return {"ok": True, "plugin": _serialize(info)}


@router.post(
    "/plugins/{name}/disable",
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
def disable_plugin(name: str, request: Request) -> dict[str, Any]:
    rt = _runtime(request)
    write_lock = getattr(request.app.state, "write_lock", None)
    if write_lock is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="write_lock not on app.state",
        )
    try:
        with write_lock:
            info = rt.disable(name)
    except PluginNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no plugin named {name!r}",
        )
    except PluginError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    return {"ok": True, "plugin": _serialize(info)}


@router.post(
    "/plugins/{name}/verify",
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
def verify_plugin(name: str, request: Request) -> dict[str, Any]:
    """Re-compute entry-point sha256 + compare to manifest pin.
    Returns 200 with ok=True/False; 404 unknown name; 422 when
    the binary itself is missing (manifest references something
    that doesn't exist on disk)."""
    rt = _runtime(request)
    try:
        ok, info = rt.verify(name)
    except PluginNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no plugin named {name!r}",
        )
    except PluginValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
    except PluginError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    return {
        "ok": ok,
        "plugin": _serialize(info),
        "expected_sha256": info.manifest.entry_point.sha256,
    }
