"""Unit tests for `fsf install tool` plugin mode (default) — Round B3."""
from __future__ import annotations

import argparse
import textwrap
from pathlib import Path

import pytest
import yaml

from forest_soul_forge.cli.install import run_tool


_GOOD_SPEC = {
    "name": "plugin_test_tool",
    "version": "1",
    "description": "Plugin install test.",
    "side_effects": "read_only",
    "archetype_tags": ["test_role"],
    "input_schema": {"type": "object"},
}

_GOOD_TOOL_PY = textwrap.dedent('''
"""plugin_test_tool — a stub."""
class PluginTestTool:
    name = "plugin_test_tool"
    version = "1"
    side_effects = "read_only"
    def validate(self, args): return None
    async def execute(self, args, ctx): return None
''').strip()


def _stage(tmp_path: Path, *, with_rejected=False, with_test=True) -> Path:
    staged = tmp_path / "staged" / "plugin_test_tool.v1"
    staged.mkdir(parents=True)
    (staged / "spec.yaml").write_text(
        yaml.safe_dump(_GOOD_SPEC, sort_keys=False), encoding="utf-8",
    )
    (staged / "tool.py").write_text(_GOOD_TOOL_PY, encoding="utf-8")
    if with_test:
        (staged / "test_plugin_test_tool.py").write_text(
            "def test_truth(): assert True\n", encoding="utf-8",
        )
    if with_rejected:
        (staged / "REJECTED.md").write_text("# REJECTED", encoding="utf-8")
    return staged


def _args(staged_dir: Path, plugins_dir: Path, **overrides) -> argparse.Namespace:
    return argparse.Namespace(
        staged_dir=str(staged_dir),
        builtin=overrides.get("builtin", False),
        plugins_dir=str(plugins_dir),
        builtin_dir=None,
        catalog_path=None,
        overwrite=overrides.get("overwrite", False),
        force=overrides.get("force", False),
        no_reload=overrides.get("no_reload", True),
    )


@pytest.fixture
def install_env(tmp_path, monkeypatch):
    """Patch DaemonSettings so audit-chain writes land in tmp_path."""
    from forest_soul_forge.daemon.config import build_settings
    real_settings = build_settings()
    monkeypatch.setattr(
        "forest_soul_forge.daemon.config.build_settings",
        lambda: type(real_settings)(
            **{**real_settings.model_dump(),
               "audit_chain_path": tmp_path / "audit.jsonl",
               "plugins_dir": tmp_path / "plugins"},
        ),
    )
    return tmp_path


class TestPluginInstallCLI:
    def test_copies_to_plugins_dir(self, install_env):
        tmp = install_env
        staged = _stage(tmp)
        plugins = tmp / "plugins"
        rc = run_tool(_args(staged, plugins))
        assert rc == 0
        target_dir = plugins / "plugin_test_tool.v1"
        assert (target_dir / "spec.yaml").exists()
        assert (target_dir / "tool.py").exists()
        # Test file copied if present.
        assert (target_dir / "test_plugin_test_tool.py").exists()

    def test_test_file_optional(self, install_env):
        tmp = install_env
        staged = _stage(tmp, with_test=False)
        plugins = tmp / "plugins"
        rc = run_tool(_args(staged, plugins))
        assert rc == 0
        target_dir = plugins / "plugin_test_tool.v1"
        assert (target_dir / "spec.yaml").exists()
        assert not (target_dir / "test_plugin_test_tool.py").exists()

    def test_rejected_blocked_without_force(self, install_env):
        tmp = install_env
        staged = _stage(tmp, with_rejected=True)
        plugins = tmp / "plugins"
        rc = run_tool(_args(staged, plugins))
        assert rc == 1
        assert not (plugins / "plugin_test_tool.v1").exists()

    def test_rejected_force_proceeds(self, install_env):
        tmp = install_env
        staged = _stage(tmp, with_rejected=True)
        plugins = tmp / "plugins"
        rc = run_tool(_args(staged, plugins, force=True))
        assert rc == 0
        assert (plugins / "plugin_test_tool.v1").exists()

    def test_existing_target_requires_overwrite(self, install_env):
        tmp = install_env
        staged = _stage(tmp)
        plugins = tmp / "plugins"
        rc = run_tool(_args(staged, plugins))
        assert rc == 0
        # Second install without --overwrite should fail.
        rc = run_tool(_args(staged, plugins, overwrite=False))
        assert rc == 1
        # With --overwrite, succeeds.
        rc = run_tool(_args(staged, plugins, overwrite=True))
        assert rc == 0

    def test_audit_event_emitted_with_plugin_mode(self, install_env):
        tmp = install_env
        staged = _stage(tmp)
        plugins = tmp / "plugins"
        run_tool(_args(staged, plugins))
        from forest_soul_forge.core.audit_chain import AuditChain
        chain = AuditChain(tmp / "audit.jsonl")
        events = chain.read_all()
        installed = [e for e in events if e.event_type == "forge_tool_installed"]
        assert len(installed) == 1
        assert installed[0].event_data["mode"] == "cli_plugin"
        assert installed[0].event_data["tool_name"] == "plugin_test_tool"

    def test_loaded_by_plugin_loader_after_install(self, install_env):
        """Round-trip — install + then run plugin_loader against the
        plugins dir + verify the tool registers."""
        tmp = install_env
        staged = _stage(tmp)
        plugins = tmp / "plugins"
        run_tool(_args(staged, plugins))

        from forest_soul_forge.core.tool_catalog import ToolCatalog
        from forest_soul_forge.tools import ToolRegistry
        from forest_soul_forge.tools.plugin_loader import load_plugins
        registry = ToolRegistry()
        catalog = ToolCatalog(version="1", tools={}, archetypes={})
        results, augmented = load_plugins(plugins, registry=registry, catalog=catalog)
        assert len(results) == 1
        assert results[0].error is None
        assert registry.has("plugin_test_tool", "1")
        assert "plugin_test_tool.v1" in augmented.tools
