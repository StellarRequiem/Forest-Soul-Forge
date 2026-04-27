"""Unit tests for fsf install tool — Round 2b (partial)."""
from __future__ import annotations

import argparse
import textwrap
from pathlib import Path

import pytest
import yaml

from forest_soul_forge.cli.install import run_tool


_GOOD_SPEC = {
    "name": "install_test_tool",
    "version": "1",
    "description": "A test tool.",
    "side_effects": "read_only",
    "archetype_tags": ["test_role"],
    "input_schema": {"type": "object"},
    "output_schema": {"type": "object"},
    "risk_flags": [],
    "forged_at": "2026-04-27T00:00:00Z",
    "forged_by": "alex",
    "forge_provider": "local",
    "forge_prompt_digest": "sha256:abc",
}

_GOOD_TOOL_PY = textwrap.dedent('''
"""install_test_tool — a stub."""
class InstallTestTool:
    name = "install_test_tool"
    version = "1"
    side_effects = "read_only"
    def validate(self, args): return None
    async def execute(self, args, ctx): return None
''').strip()

_GOOD_DIFF = [{
    "name": "install_test_tool",
    "version": "1",
    "side_effects": "read_only",
    "description": "A test tool.",
    "archetype_tags": ["test_role"],
    "input_schema": {"type": "object"},
}]


def _stage_tool(tmp_path: Path, *, with_rejected: bool = False) -> Path:
    staged = tmp_path / "staged" / "install_test_tool.v1"
    staged.mkdir(parents=True)
    (staged / "spec.yaml").write_text(yaml.safe_dump(_GOOD_SPEC), encoding="utf-8")
    (staged / "tool.py").write_text(_GOOD_TOOL_PY, encoding="utf-8")
    (staged / "catalog-diff.yaml").write_text(
        yaml.safe_dump(_GOOD_DIFF, sort_keys=False), encoding="utf-8",
    )
    if with_rejected:
        (staged / "REJECTED.md").write_text("# REJECTED", encoding="utf-8")
    return staged


def _empty_catalog(tmp_path: Path) -> Path:
    p = tmp_path / "tool_catalog.yaml"
    p.write_text(
        yaml.safe_dump({"schema_version": 1, "tools": []}, sort_keys=False),
        encoding="utf-8",
    )
    return p


def _args(staged_dir: Path, builtin_dir: Path, catalog_path: Path, **overrides) -> argparse.Namespace:
    return argparse.Namespace(
        staged_dir=str(staged_dir),
        builtin_dir=str(builtin_dir),
        catalog_path=str(catalog_path),
        overwrite=overrides.get("overwrite", False),
        force=overrides.get("force", False),
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
               "audit_chain_path": tmp_path / "audit.jsonl"},
        ),
    )
    return tmp_path


class TestToolInstallCLI:
    def test_copies_tool_and_appends_catalog(self, install_env):
        tmp = install_env
        staged = _stage_tool(tmp)
        builtin = tmp / "builtin"
        catalog = _empty_catalog(tmp)
        rc = run_tool(_args(staged, builtin, catalog))
        assert rc == 0
        target = builtin / "install_test_tool.py"
        assert target.exists()
        assert "InstallTestTool" in target.read_text()
        # Catalog updated.
        catalog_data = yaml.safe_load(catalog.read_text())
        names = [t["name"] for t in catalog_data["tools"]]
        assert "install_test_tool" in names

    def test_rejected_blocked_without_force(self, install_env):
        tmp = install_env
        staged = _stage_tool(tmp, with_rejected=True)
        builtin = tmp / "builtin"
        catalog = _empty_catalog(tmp)
        rc = run_tool(_args(staged, builtin, catalog))
        assert rc == 1
        assert not (builtin / "install_test_tool.py").exists()

    def test_rejected_force_proceeds(self, install_env):
        tmp = install_env
        staged = _stage_tool(tmp, with_rejected=True)
        builtin = tmp / "builtin"
        catalog = _empty_catalog(tmp)
        rc = run_tool(_args(staged, builtin, catalog, force=True))
        assert rc == 0
        assert (builtin / "install_test_tool.py").exists()

    def test_idempotent_catalog_append(self, install_env):
        """Second install of the same tool doesn't duplicate the
        catalog entry."""
        tmp = install_env
        staged = _stage_tool(tmp)
        builtin = tmp / "builtin"
        catalog = _empty_catalog(tmp)
        run_tool(_args(staged, builtin, catalog))
        run_tool(_args(staged, builtin, catalog, overwrite=True))
        catalog_data = yaml.safe_load(catalog.read_text())
        names = [t["name"] for t in catalog_data["tools"]]
        assert names.count("install_test_tool") == 1

    def test_audit_event_emitted(self, install_env):
        tmp = install_env
        staged = _stage_tool(tmp)
        builtin = tmp / "builtin"
        catalog = _empty_catalog(tmp)
        run_tool(_args(staged, builtin, catalog))
        from forest_soul_forge.core.audit_chain import AuditChain
        chain = AuditChain(tmp / "audit.jsonl")
        events = chain.read_all()
        installed = [e for e in events if e.event_type == "forge_tool_installed"]
        assert len(installed) == 1
        assert installed[0].event_data["tool_name"] == "install_test_tool"
        assert installed[0].event_data["mode"] == "cli_direct"

    def test_existing_target_requires_overwrite(self, install_env):
        tmp = install_env
        staged = _stage_tool(tmp)
        builtin = tmp / "builtin"
        builtin.mkdir()
        (builtin / "install_test_tool.py").write_text("stub", encoding="utf-8")
        catalog = _empty_catalog(tmp)
        rc = run_tool(_args(staged, builtin, catalog, overwrite=False))
        assert rc == 1
        assert (builtin / "install_test_tool.py").read_text() == "stub"
        rc = run_tool(_args(staged, builtin, catalog, overwrite=True))
        assert rc == 0
        assert "InstallTestTool" in (builtin / "install_test_tool.py").read_text()
