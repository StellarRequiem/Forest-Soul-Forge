"""Unit tests for the ADR-0043 PluginRepository (filesystem ops)."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from forest_soul_forge.plugins.errors import (
    PluginAlreadyInstalled,
    PluginNotFound,
    PluginValidationError,
)
from forest_soul_forge.plugins.repository import (
    PluginRepository,
    PluginState,
)


def _write_plugin_dir(
    base: Path,
    *,
    name: str = "github-mcp",
    binary_bytes: bytes = b"#!/bin/sh\necho hi\n",
    overrides: dict | None = None,
) -> Path:
    """Build a directory shaped like a plugin install — manifest +
    fake binary at ./server with a real sha256."""
    src = base / f"src-{name}"
    src.mkdir(parents=True)
    binary_path = src / "server"
    binary_path.write_bytes(binary_bytes)
    sha = hashlib.sha256(binary_bytes).hexdigest()

    manifest = {
        "schema_version": 1,
        "name": name,
        "display_name": name.replace("-", " ").title(),
        "version": "0.1.0",
        "type": "mcp_server",
        "side_effects": "external",
        "entry_point": {
            "type": "stdio",
            "command": "./server",
            "sha256": sha,
        },
        "capabilities": [f"mcp.{name}.do_thing"],
    }
    if overrides:
        manifest.update(overrides)
    (src / "plugin.yaml").write_text(yaml.safe_dump(manifest))
    return src


# ---- Layout --------------------------------------------------------------

def test_init_creates_layout(tmp_path: Path):
    repo = PluginRepository(root=tmp_path / "plugins")
    assert repo.directories.installed.is_dir()
    assert repo.directories.disabled.is_dir()
    assert repo.directories.secrets.is_dir()
    # registry-cache.json is NOT pre-created
    assert not repo.directories.registry_cache.exists()


def test_init_idempotent(tmp_path: Path):
    """Second construction over an existing layout doesn't fail."""
    PluginRepository(root=tmp_path)
    PluginRepository(root=tmp_path)  # no-op


# ---- Install -------------------------------------------------------------

def test_install_from_dir_round_trips(tmp_path: Path):
    src = _write_plugin_dir(tmp_path)
    repo = PluginRepository(root=tmp_path / "plugins")
    info = repo.install_from_dir(src)
    assert info.name == "github-mcp"
    assert info.state == PluginState.INSTALLED
    assert info.directory == repo.directories.installed / "github-mcp"
    assert (info.directory / "server").exists()


def test_install_rejects_non_directory(tmp_path: Path):
    bogus = tmp_path / "not-a-dir.txt"
    bogus.write_text("file, not directory")
    repo = PluginRepository(root=tmp_path / "plugins")
    with pytest.raises(PluginValidationError, match="not a directory"):
        repo.install_from_dir(bogus)


def test_install_rejects_dir_without_manifest(tmp_path: Path):
    src = tmp_path / "no-manifest"
    src.mkdir()
    repo = PluginRepository(root=tmp_path / "plugins")
    with pytest.raises(PluginValidationError, match="not found"):
        repo.install_from_dir(src)


def test_install_refuses_overwrite_without_force(tmp_path: Path):
    src = _write_plugin_dir(tmp_path)
    repo = PluginRepository(root=tmp_path / "plugins")
    repo.install_from_dir(src)
    with pytest.raises(PluginAlreadyInstalled):
        repo.install_from_dir(src)


def test_install_force_overwrites(tmp_path: Path):
    src = _write_plugin_dir(tmp_path, binary_bytes=b"v1")
    repo = PluginRepository(root=tmp_path / "plugins")
    repo.install_from_dir(src)

    src2 = _write_plugin_dir(tmp_path / "v2", binary_bytes=b"v2-different")
    info = repo.install_from_dir(src2, force=True)
    assert info.name == "github-mcp"
    # The new binary won out — sha256 differs from v1.
    new_sha = hashlib.sha256(b"v2-different").hexdigest()
    assert info.manifest.entry_point.sha256 == new_sha


def test_install_force_overwrites_disabled_plugin(tmp_path: Path):
    """If a plugin is sitting in disabled/ when install runs, force
    should clean it out before staging fresh."""
    src = _write_plugin_dir(tmp_path)
    repo = PluginRepository(root=tmp_path / "plugins")
    repo.install_from_dir(src)
    repo.disable("github-mcp")

    src2 = _write_plugin_dir(tmp_path / "v2", binary_bytes=b"new")
    info = repo.install_from_dir(src2, force=True)
    assert info.state == PluginState.INSTALLED


# ---- List + load ---------------------------------------------------------

def test_list_empty(tmp_path: Path):
    repo = PluginRepository(root=tmp_path / "plugins")
    assert repo.list() == []


def test_list_returns_installed_and_disabled(tmp_path: Path):
    repo = PluginRepository(root=tmp_path / "plugins")
    repo.install_from_dir(_write_plugin_dir(tmp_path, name="alpha"))
    repo.install_from_dir(_write_plugin_dir(tmp_path / "b", name="beta"))
    repo.disable("beta")
    infos = {i.name: i for i in repo.list()}
    assert set(infos) == {"alpha", "beta"}
    assert infos["alpha"].state == PluginState.INSTALLED
    assert infos["beta"].state == PluginState.DISABLED


def test_list_skips_invalid_manifests(tmp_path: Path):
    repo = PluginRepository(root=tmp_path / "plugins")
    repo.install_from_dir(_write_plugin_dir(tmp_path, name="good"))
    # Stage a bogus directory in installed/ that will fail validation.
    bad = repo.directories.installed / "bad"
    bad.mkdir()
    (bad / "plugin.yaml").write_text("not: a valid: manifest:\n")
    listed = {i.name for i in repo.list()}
    assert listed == {"good"}  # bad/ silently skipped


def test_load_returns_full_info(tmp_path: Path):
    repo = PluginRepository(root=tmp_path / "plugins")
    repo.install_from_dir(_write_plugin_dir(tmp_path))
    info = repo.load("github-mcp")
    assert info.name == "github-mcp"
    assert info.state == PluginState.INSTALLED
    assert info.manifest.version == "0.1.0"


def test_load_unknown_raises(tmp_path: Path):
    repo = PluginRepository(root=tmp_path / "plugins")
    with pytest.raises(PluginNotFound):
        repo.load("ghost")


# ---- Enable / disable ----------------------------------------------------

def test_disable_then_enable_round_trip(tmp_path: Path):
    repo = PluginRepository(root=tmp_path / "plugins")
    repo.install_from_dir(_write_plugin_dir(tmp_path))
    info = repo.disable("github-mcp")
    assert info.state == PluginState.DISABLED
    info = repo.enable("github-mcp")
    assert info.state == PluginState.INSTALLED


def test_enable_already_enabled_is_noop(tmp_path: Path):
    repo = PluginRepository(root=tmp_path / "plugins")
    repo.install_from_dir(_write_plugin_dir(tmp_path))
    info = repo.enable("github-mcp")
    assert info.state == PluginState.INSTALLED


def test_disable_already_disabled_is_noop(tmp_path: Path):
    repo = PluginRepository(root=tmp_path / "plugins")
    repo.install_from_dir(_write_plugin_dir(tmp_path))
    repo.disable("github-mcp")
    info = repo.disable("github-mcp")  # again
    assert info.state == PluginState.DISABLED


def test_enable_unknown_raises(tmp_path: Path):
    repo = PluginRepository(root=tmp_path / "plugins")
    with pytest.raises(PluginNotFound):
        repo.enable("ghost")


# ---- Uninstall -----------------------------------------------------------

def test_uninstall_removes_directory(tmp_path: Path):
    repo = PluginRepository(root=tmp_path / "plugins")
    repo.install_from_dir(_write_plugin_dir(tmp_path))
    repo.uninstall("github-mcp")
    assert not (repo.directories.installed / "github-mcp").exists()
    assert repo.list() == []


def test_uninstall_works_for_disabled(tmp_path: Path):
    repo = PluginRepository(root=tmp_path / "plugins")
    repo.install_from_dir(_write_plugin_dir(tmp_path))
    repo.disable("github-mcp")
    repo.uninstall("github-mcp")
    assert repo.list() == []


def test_uninstall_unknown_raises(tmp_path: Path):
    repo = PluginRepository(root=tmp_path / "plugins")
    with pytest.raises(PluginNotFound):
        repo.uninstall("ghost")


# ---- Verify --------------------------------------------------------------

def test_verify_binary_matches(tmp_path: Path):
    repo = PluginRepository(root=tmp_path / "plugins")
    repo.install_from_dir(_write_plugin_dir(tmp_path, binary_bytes=b"original"))
    assert repo.verify_binary("github-mcp") is True


def test_verify_binary_mismatch_when_tampered(tmp_path: Path):
    repo = PluginRepository(root=tmp_path / "plugins")
    info = repo.install_from_dir(_write_plugin_dir(tmp_path, binary_bytes=b"original"))
    # Tamper with the installed binary.
    (info.directory / "server").write_bytes(b"tampered")
    assert repo.verify_binary("github-mcp") is False


def test_verify_binary_missing_file_raises(tmp_path: Path):
    repo = PluginRepository(root=tmp_path / "plugins")
    info = repo.install_from_dir(_write_plugin_dir(tmp_path))
    (info.directory / "server").unlink()
    with pytest.raises(PluginValidationError, match="does not exist"):
        repo.verify_binary("github-mcp")


def test_verify_unknown_raises(tmp_path: Path):
    repo = PluginRepository(root=tmp_path / "plugins")
    with pytest.raises(PluginNotFound):
        repo.verify_binary("ghost")
