"""ADR-0071 T1 (B289) — fsf plugin-new scaffold tests.

Covers:
  - bad name rejected (uppercase / starting with digit / etc.)
  - bad tool name rejected
  - happy path: all 5 files generated
  - plugin.yaml is valid YAML + has the right tier + tool name
  - tool stub Python file is parseable
  - test stub Python file is parseable
  - existing dir refused without --force
  - --force overwrites cleanly
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest
import yaml

from forest_soul_forge.cli.plugin_author import (
    VALID_TIERS,
    _run_new,
    add_subparser,
)


def _args(**kwargs):
    """Build a namespace mimicking what argparse would produce."""
    defaults = {
        "name": "test-plugin",
        "tier": "read_only",
        "tool": "hello_world",
        "target": None,
        "license": "Elastic License 2.0",
        "force": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_bad_name_uppercase_refused(tmp_path, capsys):
    rc = _run_new(_args(name="BadName", target=str(tmp_path / "x")))
    assert rc == 2
    assert "lowercase" in capsys.readouterr().err


def test_bad_name_starts_with_digit_refused(tmp_path, capsys):
    rc = _run_new(_args(name="1plugin", target=str(tmp_path / "x")))
    assert rc == 2


def test_bad_tool_name_refused(tmp_path, capsys):
    rc = _run_new(_args(
        target=str(tmp_path / "x"),
        tool="BadTool",  # not lowercase_underscores
    ))
    assert rc == 2
    assert "tool name" in capsys.readouterr().err


def test_scaffold_creates_all_files(tmp_path):
    target = tmp_path / "test-plugin"
    rc = _run_new(_args(
        name="test-plugin",
        tier="network",
        tool="fetch_thing",
        target=str(target),
    ))
    assert rc == 0
    assert (target / "plugin.yaml").exists()
    assert (target / "README.md").exists()
    assert (target / ".gitignore").exists()
    assert (target / "tools" / "fetch_thing.py").exists()
    assert (target / "tests" / "test_fetch_thing.py").exists()


def test_plugin_yaml_is_valid(tmp_path):
    target = tmp_path / "test-plugin"
    _run_new(_args(
        name="test-plugin",
        tier="filesystem",
        tool="write_log",
        target=str(target),
        license="MIT",
    ))
    data = yaml.safe_load((target / "plugin.yaml").read_text())
    assert data["name"] == "test-plugin"
    assert data["tier"] == "filesystem"
    assert data["license"] == "MIT"
    assert any(t["name"] == "write_log" for t in data["tools"])
    assert data["tools"][0]["side_effects"] == "filesystem"


def test_tool_module_is_parseable(tmp_path):
    import ast
    target = tmp_path / "test-plugin"
    _run_new(_args(
        target=str(target),
        tool="some_tool",
    ))
    tool_src = (target / "tools" / "some_tool.py").read_text()
    ast.parse(tool_src)
    # Class name is CamelCase from snake_case + "Tool"
    assert "class SomeToolTool" in tool_src


def test_test_module_is_parseable(tmp_path):
    import ast
    target = tmp_path / "test-plugin"
    _run_new(_args(
        target=str(target),
        tool="my_tool",
    ))
    test_src = (target / "tests" / "test_my_tool.py").read_text()
    ast.parse(test_src)
    assert "def test_my_tool_" in test_src


def test_existing_dir_refused_without_force(tmp_path, capsys):
    target = tmp_path / "test-plugin"
    _run_new(_args(target=str(target)))  # creates the dir
    rc = _run_new(_args(target=str(target)))  # second call
    assert rc == 2
    assert "already exists" in capsys.readouterr().err


def test_force_overwrites_existing(tmp_path):
    target = tmp_path / "test-plugin"
    _run_new(_args(target=str(target), tool="first_tool"))
    rc = _run_new(_args(
        target=str(target), tool="second_tool", force=True,
    ))
    assert rc == 0
    # Second tool's file now present.
    assert (target / "tools" / "second_tool.py").exists()


def test_all_valid_tiers_accepted(tmp_path):
    """All four tier values pass validation."""
    for tier in VALID_TIERS:
        target = tmp_path / f"plugin-{tier}"
        rc = _run_new(_args(target=str(target), tier=tier))
        assert rc == 0


def test_add_subparser_registers(tmp_path):
    """add_subparser hook registers a parsable command without errors."""
    root = argparse.ArgumentParser(prog="fsf")
    sub = root.add_subparsers(dest="cmd")
    add_subparser(sub)
    # Args must parse cleanly via the registered subcommand.
    args = root.parse_args([
        "plugin-new", "my-plugin", "--tier", "read_only",
        "--tool", "do_thing",
    ])
    assert args.name == "my-plugin"
    assert args.tier == "read_only"
    assert args.tool == "do_thing"
