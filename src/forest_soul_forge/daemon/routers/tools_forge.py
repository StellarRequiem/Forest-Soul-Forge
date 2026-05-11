"""``/tools/forge`` + ``/tools/install`` + ``/tools/staged`` —
operator-direct prompt-template tool creation surface
(ADR-0058, B202).

Sister of ``daemon/routers/skills_forge.py``. Same propose →
preview → install loop, but for prompt-template tools rather than
skills. Prompt-template tools are a thin wrapper around llm_think
parameterized by a baked-in template; the implementation is the
generic ``PromptTemplateTool`` class registered MULTIPLE times
(once per forged spec).

Endpoints:

  POST   /tools/forge                         propose stage
  POST   /tools/install                       install stage
  GET    /tools/staged/forged                 list pending staged specs
  DELETE /tools/staged/forged/{name}/{version} discard a staged spec

The path prefix on staged is ``/tools/staged/forged/`` (not just
``/tools/staged/``) to avoid colliding with any existing
``/tools/staged`` semantics — this router owns only the prompt-
template tool path. ADR-0058 § Decision documents the boundary.

Audit emit: ``forge_tool_proposed`` on propose, ``forge_tool_installed``
on install. Both fire under app.state.write_lock (B199 single-writer
discipline; chain.append itself is thread-safe).
"""
from __future__ import annotations

import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from forest_soul_forge.daemon.deps import (
    get_active_provider,
    get_audit_chain,
    get_settings,
    get_write_lock,
    require_api_token,
    require_writes_enabled,
)


router = APIRouter(tags=["tools-forge"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class ForgeToolIn(BaseModel):
    description: str = Field(..., min_length=10, max_length=4000)
    name: str | None = Field(None, max_length=64)
    version: str = Field("1", max_length=16)


class ForgedToolOut(BaseModel):
    ok: bool
    staged_path: str
    spec_path: str
    name: str
    version: str
    spec_hash: str
    description: str
    input_schema_keys: list[str]
    archetype_tags: list[str]
    audit_seq: int | None
    forge_log_excerpt: str
    prompt_template_preview: str = Field(
        ...,
        description="First ~400 chars of the template so the modal can show "
        "what the LLM produced before install commits.",
    )


class InstallToolIn(BaseModel):
    staged_path: str
    overwrite: bool = False


class InstalledToolOut(BaseModel):
    ok: bool
    installed_path: str
    name: str
    version: str
    spec_hash: str
    audit_seq: int


class StagedToolSummary(BaseModel):
    name: str
    version: str
    staged_path: str
    description_preview: str
    spec_hash: str
    forged_at: str | None


class StagedToolsOut(BaseModel):
    count: int
    staged: list[StagedToolSummary]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _resolve_staged_root(settings: Any) -> Path:
    root = getattr(settings, "tool_staged_dir", None)
    if root is None:
        root = Path("data/forge/tools/staged")
    return Path(root).resolve()


def _resolve_install_root(settings: Any) -> Path:
    root = getattr(settings, "tool_install_dir", None)
    if root is None:
        root = Path("data/forge/tools/installed")
    return Path(root).resolve()


def _ensure_under(parent: Path, child: Path) -> None:
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"path {child} is not under staged root {parent}",
        ) from e


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _operator_label(request: Request) -> str:
    ua = request.headers.get("user-agent", "")[:60]
    return f"http_api:{ua}" if ua else "http_api"


def _register_forged_tool(
    request: Request,
    spec_path: Path,
) -> tuple[str, str]:
    """Construct + register a PromptTemplateTool for a spec.yaml at
    ``spec_path`` and augment ``app.state.tool_catalog`` with a synthetic
    ToolDef so the dispatcher's catalog cross-check accepts it.

    Returns ``(name, version)`` of the registered tool. Raises
    HTTPException on duplicate registration / spec parse failure /
    catalog conflict.

    Called from both the install endpoint AND the lifespan walk
    (see ``daemon/lifespan_forged_tools.py``).
    """
    from forest_soul_forge.forge.prompt_tool_forge import (
        ToolSpecError,
        parse_spec,
    )
    from forest_soul_forge.tools.builtin.prompt_template_tool import (
        PromptTemplateTool,
    )
    from forest_soul_forge.tools.base import ToolError

    raw = spec_path.read_text(encoding="utf-8")
    try:
        spec = parse_spec(
            raw,
            forged_by="installed",
            forge_provider="installed",
        )
    except ToolSpecError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "spec_validation_failed", "path": e.path,
                    "detail": e.detail},
        ) from e

    tool = PromptTemplateTool(
        name=spec.name,
        version=spec.version,
        description=spec.description,
        input_schema=spec.input_schema,
        prompt_template=spec.prompt_template,
        archetype_tags=spec.archetype_tags,
        forged_by=spec.forged_by,
    )

    registry = getattr(request.app.state, "tool_registry", None)
    if registry is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="tool_registry not on app.state — daemon may have failed lifespan",
        )
    try:
        registry.register(tool)
    except ToolError as e:
        # Most likely cause: duplicate registration. Surface as 409 so
        # the modal can suggest overwrite or a different name.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"registry refused: {e}",
        ) from e

    # Augment app.state.tool_catalog so the dispatcher's catalog
    # cross-check passes for forged tools. The catalog is normally
    # built at lifespan from tool_catalog.yaml; we add a synthetic
    # ToolDef pointing at the same (name, version, side_effects) the
    # registered tool exposes.
    catalog = getattr(request.app.state, "tool_catalog", None)
    if catalog is not None:
        try:
            from forest_soul_forge.core.tool_catalog import ToolDef
            key = f"{spec.name}.v{spec.version}"
            if key not in catalog.tools:
                catalog.tools[key] = ToolDef(
                    name=spec.name,
                    version=spec.version,
                    description=spec.description,
                    input_schema=spec.input_schema,
                    side_effects=tool.side_effects,
                    archetype_tags=tuple(spec.archetype_tags),
                )
        except ImportError:
            # If the ToolDef import shape changed, the catalog cross-
            # check would catch it at the next /healthz; surface as
            # warning rather than blocking the install.
            pass

    return spec.name, spec.version


# ---------------------------------------------------------------------------
# POST /tools/forge — propose stage
# ---------------------------------------------------------------------------
@router.post(
    "/tools/forge",
    response_model=ForgedToolOut,
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
async def forge_tool_endpoint(
    body: ForgeToolIn,
    request: Request,
    settings=Depends(get_settings),
    provider=Depends(get_active_provider),
    audit_chain=Depends(get_audit_chain),
    write_lock: threading.RLock = Depends(get_write_lock),
) -> ForgedToolOut:
    from forest_soul_forge.forge.prompt_tool_forge import (
        ToolSpecError,
        forge_prompt_tool,
    )

    staged_root = _resolve_staged_root(settings)
    staged_root.mkdir(parents=True, exist_ok=True)

    try:
        # B204: pass the live genre_engine so the propose prompt
        # surfaces valid archetype_tags. Without it the LLM may invent
        # archetype names that don't match any real genre. Same fix
        # shape as B204's catalog injection on the skill forge side.
        result = await forge_prompt_tool(
            description=body.description,
            provider=provider,
            out_dir=staged_root,
            forged_by=_operator_label(request),
            name_override=body.name,
            version=body.version,
            genre_engine=getattr(request.app.state, "genre_engine", None),
        )
    except ToolSpecError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "spec_validation_failed", "path": e.path,
                    "detail": e.detail},
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": "forge_engine_failed", "kind": type(e).__name__,
                    "message": str(e)[:500]},
        ) from e

    spec = result.spec
    forge_log_text = result.log_path.read_text(encoding="utf-8") if result.log_path.exists() else ""

    audit_seq: int | None = None
    try:
        with write_lock:
            entry = audit_chain.append(
                "forge_tool_proposed",
                {
                    "tool_name": spec.name,
                    "tool_version": spec.version,
                    "spec_hash": spec.spec_hash,
                    "implementation": spec.implementation,
                    "side_effects": spec.side_effects,
                    "staged_dir": str(result.staged_dir),
                    "forged_by": _operator_label(request),
                    "mode": "http_api",
                },
            )
            audit_seq = entry.seq
    except Exception:
        pass

    input_keys = sorted(spec.input_schema.get("properties", {}).keys())
    return ForgedToolOut(
        ok=True,
        staged_path=str(result.staged_dir),
        spec_path=str(result.spec_path),
        name=spec.name,
        version=spec.version,
        spec_hash=spec.spec_hash,
        description=spec.description,
        input_schema_keys=input_keys,
        archetype_tags=list(spec.archetype_tags),
        audit_seq=audit_seq,
        forge_log_excerpt=forge_log_text[-600:] if forge_log_text else "",
        prompt_template_preview=spec.prompt_template[:400],
    )


# ---------------------------------------------------------------------------
# POST /tools/install — install stage
# ---------------------------------------------------------------------------
@router.post(
    "/tools/install",
    response_model=InstalledToolOut,
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
async def install_tool_endpoint(
    body: InstallToolIn,
    request: Request,
    settings=Depends(get_settings),
    audit_chain=Depends(get_audit_chain),
    write_lock: threading.RLock = Depends(get_write_lock),
) -> InstalledToolOut:
    from forest_soul_forge.forge.prompt_tool_forge import (
        ToolSpecError,
        parse_spec,
    )

    staged_root = _resolve_staged_root(settings)
    staged_dir = Path(body.staged_path).resolve()
    _ensure_under(staged_root, staged_dir)

    if not staged_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"staged dir not found: {staged_dir}",
        )
    spec_path = staged_dir / "spec.yaml"
    if not spec_path.exists():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"no spec.yaml in {staged_dir}",
        )

    try:
        spec = parse_spec(
            spec_path.read_text(encoding="utf-8"),
            forged_by="install",
            forge_provider="install",
        )
    except ToolSpecError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "spec_validation_failed", "path": e.path,
                    "detail": e.detail},
        ) from e

    install_dir = _resolve_install_root(settings)
    install_dir.mkdir(parents=True, exist_ok=True)
    target = install_dir / f"{spec.name}.v{spec.version}.yaml"

    if target.exists() and not body.overwrite:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{target.name} already installed. Pass overwrite=true to "
                "replace."
            ),
        )

    with write_lock:
        # If overwriting, unregister the existing tool first so the
        # new template takes effect.
        registry = getattr(request.app.state, "tool_registry", None)
        if body.overwrite and registry is not None:
            key = f"{spec.name}.v{spec.version}"
            if key in registry.tools:
                del registry.tools[key]
            catalog = getattr(request.app.state, "tool_catalog", None)
            if catalog is not None and key in catalog.tools:
                del catalog.tools[key]

        shutil.copyfile(spec_path, target)
        # Register live so the next dispatch can reach it without
        # daemon restart.
        try:
            _register_forged_tool(request, target)
        except HTTPException:
            # Roll back the file copy so the registry stays consistent.
            try:
                target.unlink()
            except OSError:
                pass
            raise

        entry = audit_chain.append(
            "forge_tool_installed",
            {
                "tool_name": spec.name,
                "tool_version": spec.version,
                "spec_hash": spec.spec_hash,
                "implementation": spec.implementation,
                "side_effects": spec.side_effects,
                "installed_from": str(staged_dir),
                "installed_to": str(target),
                "installed_by": _operator_label(request),
                "mode": "http_api",
            },
        )

    return InstalledToolOut(
        ok=True,
        installed_path=str(target),
        name=spec.name,
        version=spec.version,
        spec_hash=spec.spec_hash,
        audit_seq=entry.seq,
    )


# ---------------------------------------------------------------------------
# GET /tools/staged/forged — list pending proposals
# ---------------------------------------------------------------------------
@router.get(
    "/tools/staged/forged",
    response_model=StagedToolsOut,
    dependencies=[Depends(require_api_token)],
)
async def list_staged_tools(
    settings=Depends(get_settings),
) -> StagedToolsOut:
    from forest_soul_forge.forge.prompt_tool_forge import (
        ToolSpecError,
        parse_spec,
    )

    staged_root = _resolve_staged_root(settings)
    if not staged_root.exists():
        return StagedToolsOut(count=0, staged=[])

    # B211: skip entries with an existing installed yaml so the
    # Approvals 'Forged proposals' panel only shows pending proposals
    # after a successful install. The staged dir survives install
    # (it's the propose audit trail), so without this filter the
    # operator sees duplicates.
    install_root = _resolve_install_root(settings)

    rows: list[StagedToolSummary] = []
    for staged_dir in sorted(staged_root.iterdir()):
        if not staged_dir.is_dir():
            continue
        spec_path = staged_dir / "spec.yaml"
        if not spec_path.exists():
            continue
        try:
            spec = parse_spec(
                spec_path.read_text(encoding="utf-8"),
                forged_by="listing",
                forge_provider="listing",
            )
        except ToolSpecError:
            continue
        # B211: skip installed.
        installed_path = install_root / f"{spec.name}.v{spec.version}.yaml"
        if installed_path.exists():
            continue
        try:
            mtime = datetime.fromtimestamp(
                spec_path.stat().st_mtime, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        except OSError:
            mtime = None
        rows.append(StagedToolSummary(
            name=spec.name,
            version=spec.version,
            staged_path=str(staged_dir),
            description_preview=(spec.description or "")[:200].strip(),
            spec_hash=spec.spec_hash,
            forged_at=mtime,
        ))

    return StagedToolsOut(count=len(rows), staged=rows)


# ---------------------------------------------------------------------------
# DELETE /tools/staged/forged/{name}/{version} — discard
# ---------------------------------------------------------------------------
@router.delete(
    "/tools/staged/forged/{name}/{version}",
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
async def discard_staged_tool(
    name: str,
    version: str,
    settings=Depends(get_settings),
    audit_chain=Depends(get_audit_chain),
    write_lock: threading.RLock = Depends(get_write_lock),
    request: Request = None,
) -> dict[str, Any]:
    staged_root = _resolve_staged_root(settings)
    staged_dir = (staged_root / f"{name}.v{version}").resolve()
    _ensure_under(staged_root, staged_dir)

    if not staged_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"staged tool not found: {name} v{version}",
        )

    with write_lock:
        try:
            shutil.rmtree(staged_dir)
        except OSError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"failed to remove staged dir: {e}",
            ) from e
        audit_chain.append(
            "forge_tool_proposed",
            {
                "tool_name": name,
                "tool_version": version,
                "staged_dir": str(staged_dir),
                "mode": "discarded",
                "discarded_by": _operator_label(request) if request else "http_api",
                "discarded_at": _now_iso(),
            },
        )

    return {"ok": True, "discarded": f"{name}.v{version}"}


# ---------------------------------------------------------------------------
# DELETE /tools/installed/{name}/{version} — uninstall (B212)
# ---------------------------------------------------------------------------
@router.delete(
    "/tools/installed/{name}/{version}",
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
async def uninstall_tool(
    name: str,
    version: str,
    settings=Depends(get_settings),
    audit_chain=Depends(get_audit_chain),
    write_lock: threading.RLock = Depends(get_write_lock),
    request: Request = None,
) -> dict[str, Any]:
    """Uninstall an installed forged prompt-template tool.

    Removes the canonical ``data/forge/tools/installed/<name>.v<version>.yaml``
    AND unregisters the live PromptTemplateTool instance from the
    in-process registry so subsequent dispatches see it as unknown.
    Emits a ``forge_tool_uninstalled`` chain event.

    Pre-B212 the only path was rm + daemon restart, which left the
    audit chain silent and required a process bounce to take effect.
    """
    install_root = _resolve_install_root(settings)
    target = (install_root / f"{name}.v{version}.yaml").resolve()
    _ensure_under(install_root, target)

    if not target.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"installed tool not found: {name} v{version}",
        )

    with write_lock:
        # Unregister live before removing the file so a concurrent
        # /agents/{id}/tools/call doesn't race with the unlink.
        key = f"{name}.v{version}"
        registry = getattr(request.app.state, "tool_registry", None)
        if registry is not None and key in getattr(registry, "tools", {}):
            del registry.tools[key]
        catalog = getattr(request.app.state, "tool_catalog", None)
        if catalog is not None and key in getattr(catalog, "tools", {}):
            del catalog.tools[key]

        try:
            target.unlink()
        except OSError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"failed to remove installed spec: {e}",
            ) from e

        audit_chain.append(
            "forge_tool_uninstalled",
            {
                "tool_name": name,
                "tool_version": version,
                "uninstalled_from": str(target),
                "uninstalled_by": _operator_label(request) if request else "http_api",
                "uninstalled_at": _now_iso(),
                "mode": "http_api",
            },
        )

    return {"ok": True, "uninstalled": f"{name}.v{version}"}
