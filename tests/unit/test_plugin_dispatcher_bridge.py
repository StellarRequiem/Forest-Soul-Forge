"""ADR-0043 T4.5 — dispatcher bridge tests.

Verifies that ToolDispatcher injects PluginRuntime.mcp_servers_view()
into ctx.constraints["mcp_registry"] at dispatch time, with merge
semantics (YAML base, plugins override).

We don't run a full dispatch end-to-end here (covered in
test_tool_dispatch + test_dispatcher); we focus on the merge logic
in isolation, since that's the new behavior introduced by T4.5.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from forest_soul_forge.daemon.plugins_runtime import PluginRuntime
from forest_soul_forge.plugins import PluginRepository


def _write_plugin_dir(
    base: Path,
    *,
    name: str = "github-mcp",
    capabilities: list[str] | None = None,
) -> Path:
    src = base / f"src-{name}"
    src.mkdir(parents=True)
    binary = src / "server"
    binary.write_bytes(b"#!/bin/sh\necho hi\n")
    sha = hashlib.sha256(b"#!/bin/sh\necho hi\n").hexdigest()
    if capabilities is None:
        capabilities = [f"mcp.{name}.do_thing"]
    (src / "plugin.yaml").write_text(yaml.safe_dump({
        "schema_version": 1,
        "name": name,
        "version": "0.1.0",
        "type": "mcp_server",
        "side_effects": "external",
        "entry_point": {
            "type": "stdio",
            "command": "./server",
            "sha256": sha,
        },
        "capabilities": capabilities,
    }))
    return src


def _runtime_with_plugin(tmp_path: Path, name: str = "github-mcp") -> PluginRuntime:
    repo = PluginRepository(root=tmp_path / "plugins")
    repo.install_from_dir(_write_plugin_dir(tmp_path, name=name))
    rt = PluginRuntime(repo)
    rt.reload()
    return rt


# ---- Plugin runtime view (the input the dispatcher merges) -------------

def test_runtime_emits_view_for_dispatcher(tmp_path: Path):
    rt = _runtime_with_plugin(tmp_path, name="github-mcp")
    view = rt.mcp_servers_view()
    assert "github-mcp" in view
    # Shape that mcp_call.v1's _load_registry produces.
    entry = view["github-mcp"]
    assert "url" in entry
    assert "sha256" in entry
    assert "side_effects" in entry
    assert "allowlisted_tools" in entry


# ---- Merge logic -------------------------------------------------------

def _merge(yaml_view: dict, plugin_view: dict) -> dict:
    """Replicates dispatcher.py's merge semantics: YAML base,
    plugins override by name."""
    merged = dict(yaml_view)
    merged.update(plugin_view)
    return merged


def test_yaml_only_when_no_plugins():
    yaml_view = {"foo": {"url": "stdio:./foo", "sha256": "x" * 64}}
    plugin_view: dict = {}
    merged = _merge(yaml_view, plugin_view)
    assert merged == yaml_view


def test_plugins_only_when_no_yaml():
    yaml_view: dict = {}
    plugin_view = {"bar": {"url": "stdio:./bar", "sha256": "y" * 64}}
    merged = _merge(yaml_view, plugin_view)
    assert merged == plugin_view


def test_plugin_overrides_yaml_on_name_conflict():
    yaml_view = {
        "github": {"url": "stdio:./old-github", "sha256": "a" * 64},
    }
    plugin_view = {
        "github": {"url": "stdio:./new-github", "sha256": "b" * 64},
    }
    merged = _merge(yaml_view, plugin_view)
    # Plugin wins.
    assert merged["github"]["url"] == "stdio:./new-github"
    assert merged["github"]["sha256"] == "b" * 64


def test_merge_preserves_disjoint_keys():
    yaml_view = {"yaml-only": {"url": "stdio:./y"}}
    plugin_view = {"plugin-only": {"url": "stdio:./p"}}
    merged = _merge(yaml_view, plugin_view)
    assert set(merged.keys()) == {"yaml-only", "plugin-only"}


# ---- Dispatcher injection paths ----------------------------------------

def test_dispatcher_field_default_is_none():
    """ToolDispatcher's plugin_runtime field defaults to None — when
    plugin support isn't wired, mcp_call falls back to YAML-only."""
    from forest_soul_forge.tools.dispatcher import ToolDispatcher
    # Spot-check the dataclass declares the field.
    fields = {f.name for f in ToolDispatcher.__dataclass_fields__.values()}
    assert "plugin_runtime" in fields


def test_dispatcher_skips_injection_when_runtime_is_none():
    """When the dispatcher has no plugin_runtime, ctx.constraints
    must NOT carry an mcp_registry key derived from plugins.

    Burst 111 refactor: the merge logic moved out of dispatch() into
    the _build_merged_mcp_registry() helper so the same merged view
    is consulted by both the pipeline (per-tool gating) and the
    execute leg. Verify the helper's source still gates on
    plugin_runtime presence and applies plugin-wins-on-conflict.
    """
    import inspect
    from forest_soul_forge.tools import dispatcher as disp_mod
    helper_src = inspect.getsource(
        disp_mod.ToolDispatcher._build_merged_mcp_registry,
    )
    # The plugin_runtime guard moved into the helper.
    assert "if self.plugin_runtime is not None:" in helper_src
    # Plugin view still wins on name conflict (`merged.update(plugin_view)`).
    assert "merged.update(plugin_view)" in helper_src


def test_dispatcher_injection_swallows_runtime_errors():
    """If plugin_runtime.mcp_servers_view() raises, the dispatcher
    falls back to whatever was already in ctx_constraints rather
    than crashing the dispatch.

    Burst 111 refactor: try/except + mcp_servers_view() call live in
    _build_merged_mcp_registry() now."""
    import inspect
    from forest_soul_forge.tools import dispatcher as disp_mod
    helper_src = inspect.getsource(
        disp_mod.ToolDispatcher._build_merged_mcp_registry,
    )
    assert "try:" in helper_src
    assert "self.plugin_runtime.mcp_servers_view()" in helper_src
    # Defensive empty-fallback when the runtime call raises.
    assert "plugin_view = {}" in helper_src


# ---- End-to-end: runtime view shape matches what mcp_call expects ------

def test_runtime_view_keys_match_mcp_call_expectations(tmp_path: Path):
    """mcp_call.v1 reads url, sha256, side_effects, allowlisted_tools
    from each registry entry. The runtime view must produce the same
    keys, otherwise the merged registry would be broken at call time.
    """
    rt = _runtime_with_plugin(tmp_path, name="alpha")
    view = rt.mcp_servers_view()
    entry = view["alpha"]
    required_keys = {"url", "sha256", "side_effects", "allowlisted_tools"}
    assert required_keys <= set(entry.keys())
    # Plus the optional approval gate
    assert "requires_human_approval" in entry


def test_runtime_view_url_prefix_normalization(tmp_path: Path):
    """A bare path command like './server' must become 'stdio:./server'
    so mcp_call.v1's URL parsing works."""
    rt = _runtime_with_plugin(tmp_path)
    view = rt.mcp_servers_view()
    for entry in view.values():
        # Either a recognized scheme or stdio: prefix.
        url = entry["url"]
        assert (
            url.startswith("stdio:")
            or url.startswith("http://")
            or url.startswith("https://")
        ), f"unrecognized URL shape: {url!r}"
