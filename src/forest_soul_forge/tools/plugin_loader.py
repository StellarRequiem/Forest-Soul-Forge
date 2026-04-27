"""``.fsf`` plugin loader — ADR-0019 T5.

A plugin lives in ``data/plugins/<name>.v<version>/`` and contains:

    spec.yaml      — ToolSpec (same shape Tool Forge produces)
    tool.py        — Python module with one Tool-shaped class
    test_<name>.py — optional; not loaded at runtime

The loader at daemon lifespan:

  1. Walks plugins_dir, finds each subdirectory.
  2. Reads spec.yaml to get name, version, side_effects, etc.
  3. ``importlib`` loads tool.py as a module under a synthetic name
     (``forest_soul_forge.plugins.<name>_v<version>``).
  4. Finds the class in the module that satisfies the Tool Protocol.
  5. Registers it with the supplied ToolRegistry.
  6. Builds a ToolDef and merges it into the catalog so the
     dispatcher's "is this tool in the catalog?" cross-check passes.

Failure modes (each isolated to one plugin — others still load):
  - spec.yaml missing or malformed → skipped + reported
  - tool.py raises on import → skipped + reported
  - no Tool-shaped class found → skipped + reported
  - register raises (duplicate name+version) → skipped + reported

The lifespan reports plugin load errors in startup_diagnostics so
``GET /healthz`` surfaces them. Operators see at a glance which
plugin is broken without grepping logs.

T5 deliberately does NOT include hot-reload-on-file-change. The
``POST /tools/reload`` endpoint (B2) is the operator-driven
refresh; OS-level filesystem watchers come later if needed.
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class PluginLoadResult:
    """Outcome of loading one plugin directory.

    On success ``tool`` is the registered class instance + ``tool_def``
    is the ToolDef ready to merge into the catalog. On failure both
    are None and ``error`` describes what went wrong.
    """

    plugin_dir: Path
    name: str | None
    version: str | None
    tool: Any | None
    tool_def: Any | None
    error: str | None = None


def load_plugins(
    plugins_dir: Path,
    *,
    registry,  # forest_soul_forge.tools.ToolRegistry
    catalog,   # forest_soul_forge.core.tool_catalog.ToolCatalog
) -> tuple[list[PluginLoadResult], object]:
    """Walk plugins_dir, register each plugin, return (results, augmented_catalog).

    ``catalog`` is the catalog as loaded from config/tool_catalog.yaml.
    The function returns a NEW ToolCatalog with plugin tools merged in;
    the lifespan replaces ``app.state.tool_catalog`` with the result.
    Built-in tools (already in the supplied catalog) are NOT removed.
    """
    results: list[PluginLoadResult] = []
    if not plugins_dir.exists() or not plugins_dir.is_dir():
        return results, catalog

    from forest_soul_forge.core.tool_catalog import ToolCatalog
    plugin_tool_defs: dict[str, Any] = {}
    for sub in sorted(plugins_dir.iterdir()):
        if not sub.is_dir():
            continue
        # Skip dotfiles.
        if sub.name.startswith("."):
            continue
        result = _load_one(sub, registry=registry, catalog=catalog)
        results.append(result)
        if result.tool is not None and result.tool_def is not None:
            key = f"{result.name}.v{result.version}"
            plugin_tool_defs[key] = result.tool_def

    if not plugin_tool_defs:
        return results, catalog

    # Merge plugin tools into a new catalog. Don't mutate the
    # original — keeps the load idempotent under reload.
    merged_tools = dict(catalog.tools)
    merged_tools.update(plugin_tool_defs)
    augmented = ToolCatalog(
        version=getattr(catalog, "version", "1"),
        tools=merged_tools,
        archetypes=getattr(catalog, "archetypes", {}),
        genre_default_tools=getattr(catalog, "genre_default_tools", {}),
        source_path=getattr(catalog, "source_path", None),
    )
    return results, augmented


def _load_one(
    plugin_dir: Path, *, registry, catalog,
) -> PluginLoadResult:
    """Load a single plugin. Caller wraps the broad error catch."""
    spec_path = plugin_dir / "spec.yaml"
    tool_path = plugin_dir / "tool.py"

    if not spec_path.exists():
        return _error(plugin_dir, "spec.yaml missing")
    if not tool_path.exists():
        return _error(plugin_dir, "tool.py missing")

    try:
        spec_data = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        return _error(plugin_dir, f"spec.yaml parse failed: {e}")
    if not isinstance(spec_data, dict):
        return _error(plugin_dir, "spec.yaml top-level must be a mapping")

    name = str(spec_data.get("name") or "").strip()
    version = str(spec_data.get("version") or "").strip()
    side_effects = str(spec_data.get("side_effects") or "").strip()
    if not name or not version:
        return _error(plugin_dir, "spec.yaml missing name or version")
    from forest_soul_forge.tools.base import SIDE_EFFECTS_VALUES
    if side_effects not in SIDE_EFFECTS_VALUES:
        return _error(
            plugin_dir,
            f"spec.yaml side_effects {side_effects!r} not in {list(SIDE_EFFECTS_VALUES)}",
        )

    # Already registered? — defensively check before importing.
    if registry.has(name, version):
        return _error(
            plugin_dir,
            f"tool {name}.v{version} already registered (duplicate plugin or "
            f"shadowing a built-in)",
        )

    # Load the Python module.
    module_name = f"forest_soul_forge.plugins.{name}_v{version}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, tool_path)
        if spec is None or spec.loader is None:
            return _error(plugin_dir, "importlib could not build a spec")
        module = importlib.util.module_from_spec(spec)
        # Cache in sys.modules BEFORE exec — any imports inside the
        # module that reference this same module (rare but possible)
        # resolve correctly.
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except Exception as e:
        # Roll back the sys.modules entry to keep state clean for
        # subsequent reloads.
        sys.modules.pop(module_name, None)
        return _error(
            plugin_dir,
            f"tool.py import raised: {type(e).__name__}: {e}",
        )

    # Find a Tool-shaped class. Walk the module dict and pick the
    # first class whose name/version match the spec.yaml.
    tool_class = _find_tool_class(module, name=name, version=version)
    if tool_class is None:
        sys.modules.pop(module_name, None)
        return _error(
            plugin_dir,
            f"no class with name={name!r} + version={version!r} found in tool.py",
        )

    try:
        tool_instance = tool_class()
    except Exception as e:
        sys.modules.pop(module_name, None)
        return _error(
            plugin_dir,
            f"tool class instantiation raised: {type(e).__name__}: {e}",
        )

    # Cross-check the class's own metadata against spec.yaml. The
    # class is the truth at runtime, but a mismatch is suspicious.
    cls_se = getattr(tool_instance, "side_effects", None)
    if cls_se != side_effects:
        sys.modules.pop(module_name, None)
        return _error(
            plugin_dir,
            f"side_effects mismatch — class={cls_se!r} spec={side_effects!r}",
        )

    try:
        registry.register(tool_instance)
    except Exception as e:
        sys.modules.pop(module_name, None)
        return _error(
            plugin_dir,
            f"registry.register raised: {type(e).__name__}: {e}",
        )

    # Build a ToolDef so the dispatcher's catalog cross-check
    # (lifespan integrity check) accepts plugin tools.
    tool_def = _build_tool_def(spec_data, name=name, version=version)
    return PluginLoadResult(
        plugin_dir=plugin_dir,
        name=name, version=version,
        tool=tool_instance, tool_def=tool_def,
    )


def _find_tool_class(module, *, name: str, version: str):
    """Pick the class in ``module`` whose ``name`` + ``version`` class
    attrs match. Multiple classes is not an error — we pick the first.
    """
    for attr_name in dir(module):
        if attr_name.startswith("_"):
            continue
        obj = getattr(module, attr_name)
        if not isinstance(obj, type):
            continue
        if getattr(obj, "name", None) == name \
                and str(getattr(obj, "version", "")) == version:
            return obj
    return None


def _build_tool_def(spec_data: dict, *, name: str, version: str):
    """Construct a ToolDef-equivalent. We use the same dataclass the
    catalog loader builds to keep the cross-check simple."""
    from forest_soul_forge.core.tool_catalog import ToolDef
    return ToolDef(
        name=name,
        version=version,
        description=str(spec_data.get("description") or "").strip(),
        input_schema=dict(spec_data.get("input_schema") or {"type": "object"}),
        side_effects=str(spec_data.get("side_effects")),
        archetype_tags=tuple(spec_data.get("archetype_tags") or ()),
    )


def _error(plugin_dir: Path, msg: str) -> PluginLoadResult:
    return PluginLoadResult(
        plugin_dir=plugin_dir,
        name=None, version=None, tool=None, tool_def=None,
        error=f"{plugin_dir.name}: {msg}",
    )


def unload_plugins(
    *, registry, plugins_dir: Path,
) -> int:
    """Remove every plugin module from sys.modules + drop their
    registrations from the registry. Used by /tools/reload before
    re-walking plugins_dir.

    Returns count of plugins unloaded. Built-in tools are NOT
    affected — the loader naming convention
    ``forest_soul_forge.plugins.<name>_v<version>`` keeps the two
    namespaces disjoint.
    """
    unloaded = 0
    # 1. Drop modules.
    for mod_name in [n for n in sys.modules if n.startswith("forest_soul_forge.plugins.")]:
        sys.modules.pop(mod_name, None)
        unloaded += 1
    # 2. Drop registrations. We can't differentiate a plugin
    # registration from a built-in by inspection alone, so we
    # walk the plugins_dir again to determine which keys to drop.
    if plugins_dir.exists() and plugins_dir.is_dir():
        for sub in plugins_dir.iterdir():
            if not sub.is_dir() or sub.name.startswith("."):
                continue
            spec_path = sub / "spec.yaml"
            if not spec_path.exists():
                continue
            try:
                data = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            n = str(data.get("name") or "").strip()
            v = str(data.get("version") or "").strip()
            if n and v:
                key = f"{n}.v{v}"
                # Pop directly from the registry's tools dict — no
                # public API for removal yet, but the dataclass field
                # is mutable.
                registry.tools.pop(key, None)
    return unloaded
