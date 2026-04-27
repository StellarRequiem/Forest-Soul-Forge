"""``/tools`` — read-only tool catalog discovery.

Powers the frontend's tools-overrides UI (ADR-0018 T4):

* ``GET /tools/catalog`` — full catalog snapshot. Used by the "add a
  tool" picker so the UI doesn't have to parse ``config/tool_catalog.yaml``
  itself or invent a separate format. Mirrors the in-memory
  :class:`ToolCatalog` minus the ``input_schema`` fields (heavy, only
  needed by the execution runtime once ADR-0019 lands).
* ``GET /tools/kit/{role}`` — the role's archetype-default standard kit.
  Returned WITHOUT the policy applied because the policy depends on the
  agent's trait profile, which doesn't exist at role-pick time. The UI
  uses this endpoint to seed its checklist; once the user nudges sliders
  and a profile exists, ``/preview`` returns the same kit with
  policy-resolved constraints in ``resolved_tools``.

Both endpoints are pure reads of ``app.state.tool_catalog``. They never
raise 503 — when the catalog file failed to load at startup, the daemon
falls back to an empty catalog (see ``daemon.deps.get_tool_catalog``)
and these endpoints return the empty shape so the frontend can render
"no tools available" rather than blowing up.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from forest_soul_forge.core.tool_catalog import ToolCatalog
from forest_soul_forge.daemon.deps import get_tool_catalog
from forest_soul_forge.daemon.schemas import (
    ArchetypeBundleOut,
    RegisteredToolOut,
    RegisteredToolsOut,
    ResolvedKitOut,
    ResolvedToolOut,
    ToolCatalogOut,
    ToolDefOut,
    ToolRefIn,
)


router = APIRouter(tags=["tools"])


def _tool_def_to_out(td) -> ToolDefOut:  # noqa: ANN001 — ToolDef is a frozen dataclass
    return ToolDefOut(
        name=td.name,
        version=td.version,
        description=td.description,
        side_effects=td.side_effects,
        archetype_tags=list(td.archetype_tags),
    )


def _bundle_to_out(b) -> ArchetypeBundleOut:  # noqa: ANN001
    return ArchetypeBundleOut(
        role=b.role,
        standard_tools=[
            ToolRefIn(name=ref.name, version=ref.version)
            for ref in b.standard_tools
        ],
    )


@router.get("/tools/catalog", response_model=ToolCatalogOut)
async def get_catalog(
    catalog: ToolCatalog = Depends(get_tool_catalog),
) -> ToolCatalogOut:
    """Return the full loaded catalog.

    Stable across the daemon's lifetime — the catalog is loaded once at
    lifespan startup and never mutated. Tools and archetypes are
    enumerated in their declaration order (the YAML keys' iteration order
    in Python 3.7+, which is insertion order).
    """
    return ToolCatalogOut(
        version=catalog.version,
        tools=[_tool_def_to_out(td) for td in catalog.tools.values()],
        archetypes=[_bundle_to_out(b) for b in catalog.archetypes.values()],
    )


@router.get("/tools/kit/{role}", response_model=ResolvedKitOut)
async def get_role_kit(
    role: str,
    catalog: ToolCatalog = Depends(get_tool_catalog),
) -> ResolvedKitOut:
    """Return the archetype's standard tool kit for ``role``.

    Constraints are returned as the policy DEFAULTS — ``max_calls_per_session:
    1000``, ``requires_human_approval: false``, ``audit_every_call: true``
    — and ``applied_rules`` is empty. This is "what the kit looks like
    before any agent profile is resolved against it." The frontend
    hits this once when the user picks a role, then relies on
    ``/preview.resolved_tools`` for the policy-applied view.

    404 when the role has no archetype entry. Roles that exist in the
    trait engine but have no tool-catalog archetype are valid (no
    standard kit) — return an empty kit with role echoed so the UI can
    display "no default tools for this role" without a special case.
    """
    bundle = catalog.archetypes.get(role)

    # No archetype entry at all — distinct from "archetype with empty
    # standard_tools." We treat both the same way: empty kit, role echoed,
    # no error. The frontend renders "this role has no default kit" and
    # tools_add still works through /birth.
    standard_refs = bundle.standard_tools if bundle is not None else ()

    # Surface unknown-tool refs in the bundle as 500. This shouldn't
    # happen — load_catalog validates archetype refs against the tools
    # mapping at lifespan time — but if it does, we want a clear error
    # rather than a misleading "tool kit missing" UX bug.
    tools_out: list[ResolvedToolOut] = []
    for ref in standard_refs:
        td = catalog.tools.get(ref.key)
        if td is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    f"catalog inconsistency: archetype {role!r} references "
                    f"unknown tool {ref.key} (load-time validation should "
                    "have caught this)"
                ),
            )
        tools_out.append(
            ResolvedToolOut(
                name=td.name,
                version=td.version,
                description=td.description,
                side_effects=td.side_effects,
                # Default constraints — the same DEFAULT_CONSTRAINTS dict
                # tool_policy starts every resolution with. Duplicated as a
                # literal here (rather than imported) to avoid coupling the
                # discovery endpoint to the policy module — if the policy
                # gets richer, this still gives the UI a reasonable
                # pre-profile view.
                constraints={
                    "max_calls_per_session": 1000,
                    "requires_human_approval": False,
                    "audit_every_call": True,
                },
                applied_rules=[],
            )
        )

    return ResolvedKitOut(
        role=role,
        catalog_version=catalog.version,
        tools=tools_out,
    )


@router.get("/tools/registered", response_model=RegisteredToolsOut)
async def get_registered_tools(
    request: Request,
    catalog: ToolCatalog = Depends(get_tool_catalog),
) -> RegisteredToolsOut:
    """Return what's actually live in the tool registry.

    Distinct from ``/tools/catalog`` (the YAML view) — this endpoint
    surfaces the runtime registry contents, classified by source
    (built-in vs plugin). The frontend Tools tab renders from this
    so an operator can see at a glance which tools the dispatcher
    will resolve, including any plugins loaded post-restart via
    ``POST /tools/reload``.

    Source classification: a registered tool is ``builtin`` if a
    file with its name+version exists under
    ``forest_soul_forge.tools.builtin``; ``plugin`` if a directory
    with the matching ``<name>.v<version>`` exists under
    ``settings.plugins_dir``; ``unknown`` otherwise.
    """
    registry = getattr(request.app.state, "tool_registry", None)
    if registry is None:
        return RegisteredToolsOut(count=0, tools=[])

    settings = getattr(request.app.state, "settings", None)
    plugins_dir = getattr(settings, "plugins_dir", None) if settings else None

    out: list[RegisteredToolOut] = []
    for key in sorted(registry.tools):
        tool = registry.tools[key]
        name = getattr(tool, "name", "")
        version = getattr(tool, "version", "")
        side_effects = getattr(tool, "side_effects", "")

        # Catalog cross-check.
        td = catalog.tools.get(key)
        in_catalog = td is not None
        description = td.description if td else None
        archetype_tags = list(td.archetype_tags) if td else []

        # Source classification.
        source = _classify_source(name, version, plugins_dir)

        out.append(RegisteredToolOut(
            name=name, version=version, side_effects=side_effects,
            source=source, in_catalog=in_catalog,
            description=description, archetype_tags=archetype_tags,
        ))
    return RegisteredToolsOut(count=len(out), tools=out)


def _classify_source(name: str, version: str, plugins_dir) -> str:
    """Decide whether a registered tool came from builtin or plugin.

    Cheap and best-effort: checks for the well-known file paths.
    Falls back to ``unknown`` if neither exists — shouldn't happen
    in v1 but the endpoint stays useful even if a future load path
    introduces a third category.
    """
    # Built-in: file exists under tools/builtin/<name>.py.
    try:
        from forest_soul_forge.tools import builtin as _builtin_pkg
        builtin_dir = _Path(_builtin_pkg.__file__).parent
        if (builtin_dir / f"{name}.py").exists():
            return "builtin"
    except Exception:
        pass

    # Plugin: directory exists under plugins_dir.
    if plugins_dir is not None:
        try:
            plugin_subdir = _Path(plugins_dir) / f"{name}.v{version}"
            if plugin_subdir.exists() and plugin_subdir.is_dir():
                return "plugin"
        except Exception:
            pass

    return "unknown"


# Local import — pathlib at function scope keeps the test mock surface
# minimal (no top-level import to monkey-patch).
from pathlib import Path as _Path  # noqa: E402
