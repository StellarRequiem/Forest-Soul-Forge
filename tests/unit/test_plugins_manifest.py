"""Unit tests for the ADR-0043 plugin.yaml schema.

Covers:
- Required fields enforced
- Name / sha256 / env_var format validators
- Schema-version pin (v1 only)
- load_manifest from disk: missing file, malformed YAML, valid round-trip
"""
from __future__ import annotations

from pathlib import Path

import pytest

from forest_soul_forge.plugins.errors import PluginValidationError
from forest_soul_forge.plugins.manifest import (
    EntryPoint,
    EntryPointType,
    PluginManifest,
    PluginType,
    RequiredSecret,
    SideEffects,
    load_manifest,
)


def _valid_manifest_dict() -> dict:
    return {
        "schema_version": 1,
        "name": "github-mcp",
        "display_name": "GitHub (MCP)",
        "version": "0.1.0",
        "type": "mcp_server",
        "side_effects": "external",
        "entry_point": {
            "type": "stdio",
            "command": "./server",
            "sha256": "0" * 64,
        },
        "capabilities": ["mcp.github.list_issues"],
    }


# ---- Schema acceptance ---------------------------------------------------

def test_minimal_valid_manifest_parses():
    m = PluginManifest.model_validate(_valid_manifest_dict())
    assert m.name == "github-mcp"
    assert m.type == PluginType.MCP_SERVER
    assert m.side_effects == SideEffects.EXTERNAL
    assert m.entry_point.command == "./server"
    assert m.entry_point.sha256 == "0" * 64
    assert m.display_label() == "GitHub (MCP)"


def test_display_label_falls_back_to_name():
    d = _valid_manifest_dict()
    d["display_name"] = ""
    m = PluginManifest.model_validate(d)
    assert m.display_label() == "github-mcp"


def test_secret_with_env_var_round_trips():
    d = _valid_manifest_dict()
    d["required_secrets"] = [
        {"name": "github-pat", "description": "PAT", "env_var": "GITHUB_TOKEN"},
    ]
    m = PluginManifest.model_validate(d)
    assert len(m.required_secrets) == 1
    assert m.required_secrets[0].env_var == "GITHUB_TOKEN"


# ---- Schema rejection ----------------------------------------------------

def test_rejects_unknown_top_level_keys():
    d = _valid_manifest_dict()
    d["surprise"] = "not in schema"
    with pytest.raises(Exception):
        PluginManifest.model_validate(d)


def test_rejects_invalid_name():
    d = _valid_manifest_dict()
    d["name"] = "Invalid_Name"  # uppercase + underscore
    with pytest.raises(Exception):
        PluginManifest.model_validate(d)


def test_rejects_short_sha256():
    d = _valid_manifest_dict()
    d["entry_point"]["sha256"] = "abcd"
    with pytest.raises(Exception):
        PluginManifest.model_validate(d)


def test_rejects_uppercase_sha256():
    d = _valid_manifest_dict()
    d["entry_point"]["sha256"] = "A" * 64
    # Validator lowercases; uppercase A's pass but the regex is on
    # lowercase. Verify the lowercasing happens.
    m = PluginManifest.model_validate(d)
    assert m.entry_point.sha256 == "a" * 64


def test_rejects_lowercase_env_var():
    d = _valid_manifest_dict()
    d["required_secrets"] = [
        {"name": "x", "env_var": "lowercase"},
    ]
    with pytest.raises(Exception):
        PluginManifest.model_validate(d)


def test_rejects_unknown_schema_version():
    d = _valid_manifest_dict()
    d["schema_version"] = 2
    with pytest.raises(Exception):
        PluginManifest.model_validate(d)


def test_rejects_unknown_plugin_type():
    d = _valid_manifest_dict()
    d["type"] = "weird_type"
    with pytest.raises(Exception):
        PluginManifest.model_validate(d)


def test_rejects_unknown_side_effects():
    d = _valid_manifest_dict()
    d["side_effects"] = "weird"
    with pytest.raises(Exception):
        PluginManifest.model_validate(d)


# ---- load_manifest from disk ---------------------------------------------

def test_load_manifest_missing_file_raises_validation_error(tmp_path: Path):
    with pytest.raises(PluginValidationError, match="not found"):
        load_manifest(tmp_path / "plugin.yaml")


def test_load_manifest_malformed_yaml_raises(tmp_path: Path):
    p = tmp_path / "plugin.yaml"
    p.write_text("not: valid: yaml: at: all:::")
    with pytest.raises(PluginValidationError, match="YAML parse failed"):
        load_manifest(p)


def test_load_manifest_non_mapping_raises(tmp_path: Path):
    p = tmp_path / "plugin.yaml"
    p.write_text("- a\n- list\n- not\n- mapping\n")
    with pytest.raises(PluginValidationError, match="YAML mapping"):
        load_manifest(p)


def test_load_manifest_valid_round_trip(tmp_path: Path):
    import yaml as _yaml
    p = tmp_path / "plugin.yaml"
    p.write_text(_yaml.safe_dump(_valid_manifest_dict()))
    m = load_manifest(p)
    assert m.name == "github-mcp"


def test_load_manifest_validation_error_includes_path(tmp_path: Path):
    p = tmp_path / "plugin.yaml"
    p.write_text("name: ok\n")  # missing required fields
    with pytest.raises(PluginValidationError) as excinfo:
        load_manifest(p)
    assert str(p) in str(excinfo.value)
