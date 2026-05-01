"""Unit tests for the .fsf plugin loader — ADR-0019 T5 / Round B1."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from forest_soul_forge.core.tool_catalog import ToolCatalog
from forest_soul_forge.tools import ToolRegistry
from forest_soul_forge.tools.plugin_loader import (
    PluginLoadResult,
    load_plugins,
    unload_plugins,
)


_SPEC = {
    "name": "demo_plugin",
    "version": "1",
    "description": "A test plugin.",
    "side_effects": "read_only",
    "archetype_tags": ["test_role"],
    "input_schema": {"type": "object"},
    "output_schema": {"type": "object"},
}

_TOOL_PY = textwrap.dedent('''
    """demo_plugin.v1 — a stub."""
    from __future__ import annotations
    from typing import Any
    from forest_soul_forge.tools.base import ToolContext, ToolResult


    class DemoPluginTool:
        name = "demo_plugin"
        version = "1"
        side_effects = "read_only"

        def validate(self, args):
            return None

        async def execute(self, args, ctx) -> ToolResult:
            return ToolResult(output={"echo": args.get("msg", "")})
''').strip()


def _stage_plugin(plugins_dir: Path, *, name: str = "demo_plugin",
                  version: str = "1", spec_overrides=None,
                  tool_py: str = _TOOL_PY,
                  drop_spec: bool = False,
                  drop_tool: bool = False) -> Path:
    sub = plugins_dir / f"{name}.v{version}"
    sub.mkdir(parents=True, exist_ok=True)
    if not drop_spec:
        spec = dict(_SPEC)
        spec["name"] = name
        spec["version"] = version
        if spec_overrides:
            spec.update(spec_overrides)
        (sub / "spec.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
    if not drop_tool:
        # Inject the right name/version into the tool source if non-default.
        py = tool_py.replace("demo_plugin", name).replace(
            'version = "1"', f'version = "{version}"'
        )
        (sub / "tool.py").write_text(py, encoding="utf-8")
    return sub


def _empty_catalog():
    return ToolCatalog(version="1", tools={}, archetypes={})


@pytest.fixture
def env(tmp_path):
    """Plugin-loader test fixture.

    Cleans up ``sys.modules`` before AND after the test so plugin
    modules loaded by earlier tests don't leak into the next one.
    Without this, ``unload_plugins`` (which scans sys.modules for the
    plugin namespace prefix) double-counts modules from prior fixtures
    and the unload-count assertion fails. Phase A audit 2026-04-30.
    """
    import sys
    _PREFIX = "forest_soul_forge.plugins."
    for mod_name in [n for n in sys.modules if n.startswith(_PREFIX)]:
        sys.modules.pop(mod_name, None)
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    yield {"plugins_dir": plugins_dir}
    for mod_name in [n for n in sys.modules if n.startswith(_PREFIX)]:
        sys.modules.pop(mod_name, None)


class TestLoadPlugins:
    def test_no_plugins_dir_returns_empty(self, env):
        registry = ToolRegistry()
        results, augmented = load_plugins(
            env["plugins_dir"] / "missing",
            registry=registry, catalog=_empty_catalog(),
        )
        assert results == []
        assert augmented.tools == {}

    def test_loads_one_plugin(self, env):
        _stage_plugin(env["plugins_dir"])
        registry = ToolRegistry()
        results, augmented = load_plugins(
            env["plugins_dir"], registry=registry, catalog=_empty_catalog(),
        )
        assert len(results) == 1
        assert results[0].error is None
        assert results[0].tool is not None
        assert registry.has("demo_plugin", "1")
        assert "demo_plugin.v1" in augmented.tools

    def test_skips_plugin_missing_spec(self, env):
        _stage_plugin(env["plugins_dir"], drop_spec=True)
        registry = ToolRegistry()
        results, _ = load_plugins(
            env["plugins_dir"], registry=registry, catalog=_empty_catalog(),
        )
        assert len(results) == 1
        assert results[0].error and "spec.yaml missing" in results[0].error
        assert not registry.has("demo_plugin", "1")

    def test_skips_plugin_missing_tool_py(self, env):
        _stage_plugin(env["plugins_dir"], drop_tool=True)
        registry = ToolRegistry()
        results, _ = load_plugins(
            env["plugins_dir"], registry=registry, catalog=_empty_catalog(),
        )
        assert results[0].error and "tool.py missing" in results[0].error

    def test_skips_invalid_side_effects(self, env):
        _stage_plugin(env["plugins_dir"], spec_overrides={"side_effects": "telekinesis"})
        registry = ToolRegistry()
        results, _ = load_plugins(
            env["plugins_dir"], registry=registry, catalog=_empty_catalog(),
        )
        assert results[0].error and "telekinesis" in results[0].error

    def test_skips_when_class_metadata_mismatches_spec(self, env):
        # Spec says side_effects=network, class declares read_only.
        _stage_plugin(env["plugins_dir"], spec_overrides={"side_effects": "network"})
        registry = ToolRegistry()
        results, _ = load_plugins(
            env["plugins_dir"], registry=registry, catalog=_empty_catalog(),
        )
        assert results[0].error and "side_effects mismatch" in results[0].error

    def test_skips_when_no_matching_class(self, env):
        bad = textwrap.dedent('''
            class WrongName:
                name = "different"
                version = "1"
                side_effects = "read_only"
                def validate(self, args): return None
                async def execute(self, args, ctx): return None
        ''').strip()
        _stage_plugin(env["plugins_dir"], tool_py=bad)
        registry = ToolRegistry()
        results, _ = load_plugins(
            env["plugins_dir"], registry=registry, catalog=_empty_catalog(),
        )
        assert results[0].error and "no class" in results[0].error

    def test_skips_when_already_registered(self, env):
        # Register a built-in first that shadows the plugin.
        from forest_soul_forge.tools.builtin.timestamp_window import TimestampWindowTool
        registry = ToolRegistry()
        registry.register(TimestampWindowTool())
        _stage_plugin(
            env["plugins_dir"],
            name="timestamp_window",  # collide with built-in
        )
        results, _ = load_plugins(
            env["plugins_dir"], registry=registry, catalog=_empty_catalog(),
        )
        assert results[0].error and "already registered" in results[0].error

    def test_one_broken_plugin_does_not_kill_the_others(self, env):
        _stage_plugin(env["plugins_dir"], name="good_one")
        _stage_plugin(env["plugins_dir"], name="broken_one", drop_tool=True)
        registry = ToolRegistry()
        results, augmented = load_plugins(
            env["plugins_dir"], registry=registry, catalog=_empty_catalog(),
        )
        ok = [r for r in results if r.error is None]
        err = [r for r in results if r.error is not None]
        assert len(ok) == 1 and ok[0].name == "good_one"
        assert len(err) == 1 and "broken_one" in err[0].error
        assert registry.has("good_one", "1")

    def test_dotfiles_ignored(self, env):
        # A subdirectory starting with `.` should be skipped silently.
        (env["plugins_dir"] / ".hidden").mkdir()
        registry = ToolRegistry()
        results, _ = load_plugins(
            env["plugins_dir"], registry=registry, catalog=_empty_catalog(),
        )
        assert results == []


class TestUnloadPlugins:
    def test_unload_drops_module_and_registration(self, env):
        _stage_plugin(env["plugins_dir"])
        registry = ToolRegistry()
        load_plugins(
            env["plugins_dir"], registry=registry, catalog=_empty_catalog(),
        )
        assert registry.has("demo_plugin", "1")
        n = unload_plugins(registry=registry, plugins_dir=env["plugins_dir"])
        assert n == 1
        assert not registry.has("demo_plugin", "1")
        # Re-load works after unload (idempotent).
        load_plugins(
            env["plugins_dir"], registry=registry, catalog=_empty_catalog(),
        )
        assert registry.has("demo_plugin", "1")
