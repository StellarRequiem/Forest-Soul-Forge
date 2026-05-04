"""Smoke tests for ``fsf plugin`` argparse wiring + exit codes.

We don't re-test the repository's filesystem ops (covered in
test_plugins_repository); these tests just confirm the CLI:

  - dispatches to the right runner
  - returns the documented exit code per error class
  - emits expected lines on stdout / stderr
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from forest_soul_forge.cli.main import main as cli_main


def _write_plugin_dir(base: Path, *, name: str = "github-mcp") -> Path:
    src = base / f"src-{name}"
    src.mkdir()
    binary = src / "server"
    binary.write_bytes(b"#!/bin/sh\necho hi\n")
    sha = hashlib.sha256(b"#!/bin/sh\necho hi\n").hexdigest()
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
        "capabilities": [f"mcp.{name}.do_thing"],
    }))
    return src


def test_list_empty_root(tmp_path: Path, capsys):
    rc = cli_main([
        "plugin", "list",
        "--plugin-root", str(tmp_path / "plugins"),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "(no plugins installed)" in out


def test_install_then_list(tmp_path: Path, capsys):
    src = _write_plugin_dir(tmp_path)
    root = tmp_path / "plugins"
    rc = cli_main([
        "plugin", "install", str(src),
        "--plugin-root", str(root),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "installed: github-mcp" in out

    rc = cli_main([
        "plugin", "list",
        "--plugin-root", str(root),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "github-mcp" in out
    assert "installed" in out


def test_list_json_output(tmp_path: Path, capsys):
    src = _write_plugin_dir(tmp_path)
    root = tmp_path / "plugins"
    cli_main(["plugin", "install", str(src), "--plugin-root", str(root)])
    capsys.readouterr()  # discard
    rc = cli_main([
        "plugin", "list", "--json",
        "--plugin-root", str(root),
    ])
    assert rc == 0
    import json
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 1
    assert data[0]["name"] == "github-mcp"
    assert data[0]["state"] == "installed"


def test_install_validation_error_exits_6(tmp_path: Path, capsys):
    bad = tmp_path / "no-yaml"
    bad.mkdir()
    rc = cli_main([
        "plugin", "install", str(bad),
        "--plugin-root", str(tmp_path / "plugins"),
    ])
    assert rc == 6
    err = capsys.readouterr().err
    assert "fsf plugin install" in err


def test_install_already_installed_exits_5(tmp_path: Path, capsys):
    src = _write_plugin_dir(tmp_path)
    root = tmp_path / "plugins"
    cli_main(["plugin", "install", str(src), "--plugin-root", str(root)])
    capsys.readouterr()  # discard
    rc = cli_main([
        "plugin", "install", str(src),
        "--plugin-root", str(root),
    ])
    assert rc == 5


def test_info_unknown_exits_4(tmp_path: Path, capsys):
    rc = cli_main([
        "plugin", "info", "ghost",
        "--plugin-root", str(tmp_path / "plugins"),
    ])
    assert rc == 4


def test_info_round_trip(tmp_path: Path, capsys):
    src = _write_plugin_dir(tmp_path)
    root = tmp_path / "plugins"
    cli_main(["plugin", "install", str(src), "--plugin-root", str(root)])
    capsys.readouterr()
    rc = cli_main([
        "plugin", "info", "github-mcp",
        "--plugin-root", str(root),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "name:          github-mcp" in out
    assert "type:          mcp_server" in out


def test_disable_enable_round_trip(tmp_path: Path, capsys):
    src = _write_plugin_dir(tmp_path)
    root = tmp_path / "plugins"
    cli_main(["plugin", "install", str(src), "--plugin-root", str(root)])
    capsys.readouterr()
    assert cli_main(["plugin", "disable", "github-mcp", "--plugin-root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "disabled: github-mcp" in out
    assert cli_main(["plugin", "enable", "github-mcp", "--plugin-root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "enabled: github-mcp" in out


def test_uninstall_unknown_exits_4(tmp_path: Path, capsys):
    rc = cli_main([
        "plugin", "uninstall", "ghost",
        "--plugin-root", str(tmp_path / "plugins"),
    ])
    assert rc == 4


def test_verify_match_exits_0(tmp_path: Path, capsys):
    src = _write_plugin_dir(tmp_path)
    root = tmp_path / "plugins"
    cli_main(["plugin", "install", str(src), "--plugin-root", str(root)])
    capsys.readouterr()
    rc = cli_main([
        "plugin", "verify", "github-mcp",
        "--plugin-root", str(root),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "sha256 matches" in out


def test_verify_mismatch_exits_1(tmp_path: Path, capsys):
    src = _write_plugin_dir(tmp_path)
    root = tmp_path / "plugins"
    cli_main(["plugin", "install", str(src), "--plugin-root", str(root)])
    capsys.readouterr()
    # Tamper.
    (root / "installed" / "github-mcp" / "server").write_bytes(b"tampered")
    rc = cli_main([
        "plugin", "verify", "github-mcp",
        "--plugin-root", str(root),
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "MISMATCH" in err
