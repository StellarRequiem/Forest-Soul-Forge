"""Unit tests for fsf install skill + POST /skills/reload — Round 2a."""
from __future__ import annotations

import argparse
import textwrap
from pathlib import Path

import pytest

from forest_soul_forge.cli.install import run_skill


_GOOD_MANIFEST = textwrap.dedent("""
schema_version: 1
name: install_test_skill
version: '1'
description: Install test.
requires: [timestamp_window.v1]
inputs: {type: object}
steps:
  - id: w
    tool: timestamp_window.v1
    args:
      expression: 'last 1 minutes'
output:
  end: ${w.end}
""").strip()


def _stage(tmp_path: Path, manifest_text: str = _GOOD_MANIFEST) -> Path:
    staged = tmp_path / "staged" / "install_test_skill.v1"
    staged.mkdir(parents=True)
    (staged / "manifest.yaml").write_text(manifest_text, encoding="utf-8")
    return staged


def _args(staged_dir: Path, install_dir: Path, **overrides) -> argparse.Namespace:
    return argparse.Namespace(
        staged_dir=str(staged_dir),
        install_dir=str(install_dir),
        overwrite=overrides.get("overwrite", False),
        no_reload=overrides.get("no_reload", True),
    )


class TestSkillInstallCLI:
    def test_copies_manifest_to_install_dir(self, tmp_path, monkeypatch):
        # Point audit chain at tmp_path so the install command's
        # AuditChain.append doesn't write to the real audit/.
        from forest_soul_forge.daemon.config import build_settings
        real_settings = build_settings()
        monkeypatch.setattr(
            "forest_soul_forge.daemon.config.build_settings",
            lambda: type(real_settings)(
                **{**real_settings.model_dump(),
                   "audit_chain_path": tmp_path / "audit.jsonl",
                   "skill_install_dir": tmp_path / "installed"},
            ),
        )
        staged = _stage(tmp_path)
        install_dir = tmp_path / "installed"
        rc = run_skill(_args(staged, install_dir))
        assert rc == 0
        target = install_dir / "install_test_skill.v1.yaml"
        assert target.exists()
        assert "install_test_skill" in target.read_text()

    def test_invalid_manifest_does_not_install(self, tmp_path, monkeypatch):
        from forest_soul_forge.daemon.config import build_settings
        real_settings = build_settings()
        monkeypatch.setattr(
            "forest_soul_forge.daemon.config.build_settings",
            lambda: type(real_settings)(
                **{**real_settings.model_dump(),
                   "audit_chain_path": tmp_path / "audit.jsonl",
                   "skill_install_dir": tmp_path / "installed"},
            ),
        )
        staged = _stage(tmp_path, "name: NotSnakeCase\n")
        install_dir = tmp_path / "installed"
        rc = run_skill(_args(staged, install_dir))
        assert rc == 1
        assert not (install_dir / "install_test_skill.v1.yaml").exists()

    def test_overwrite_required_for_existing_target(self, tmp_path, monkeypatch):
        from forest_soul_forge.daemon.config import build_settings
        real_settings = build_settings()
        monkeypatch.setattr(
            "forest_soul_forge.daemon.config.build_settings",
            lambda: type(real_settings)(
                **{**real_settings.model_dump(),
                   "audit_chain_path": tmp_path / "audit.jsonl",
                   "skill_install_dir": tmp_path / "installed"},
            ),
        )
        staged = _stage(tmp_path)
        install_dir = tmp_path / "installed"
        install_dir.mkdir()
        # Pre-create the target.
        (install_dir / "install_test_skill.v1.yaml").write_text(
            "stub", encoding="utf-8",
        )
        rc = run_skill(_args(staged, install_dir, overwrite=False))
        assert rc == 1
        assert (install_dir / "install_test_skill.v1.yaml").read_text() == "stub"

        rc = run_skill(_args(staged, install_dir, overwrite=True))
        assert rc == 0
        assert "install_test_skill" in (install_dir / "install_test_skill.v1.yaml").read_text()

    def test_audit_event_emitted(self, tmp_path, monkeypatch):
        from forest_soul_forge.daemon.config import build_settings
        real_settings = build_settings()
        monkeypatch.setattr(
            "forest_soul_forge.daemon.config.build_settings",
            lambda: type(real_settings)(
                **{**real_settings.model_dump(),
                   "audit_chain_path": tmp_path / "audit.jsonl",
                   "skill_install_dir": tmp_path / "installed"},
            ),
        )
        staged = _stage(tmp_path)
        install_dir = tmp_path / "installed"
        run_skill(_args(staged, install_dir))
        from forest_soul_forge.core.audit_chain import AuditChain
        chain = AuditChain(tmp_path / "audit.jsonl")
        events = chain.read_all()
        installed = [e for e in events if e.event_type == "forge_skill_installed"]
        assert len(installed) == 1
        assert installed[0].event_data["skill_name"] == "install_test_skill"
