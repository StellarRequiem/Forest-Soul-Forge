"""Smoke tests for the canonical example plugins shipped in
``examples/plugins/`` (ADR-0043 T5).

Each example is meant to be a starting template + a authoring
reference. The tests confirm:

  - Each example's plugin.yaml parses cleanly via load_manifest
  - Required fields are populated (no missing-field accidents
    in the example sources)
  - Names match their directory names (operator install relies
    on this)

This catches a broken example before an operator copies it as
a template.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from forest_soul_forge.plugins.manifest import (
    PluginType,
    SideEffects,
    load_manifest,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_ROOT / "examples" / "plugins"


def _example_dirs() -> list[Path]:
    """All directories under examples/plugins/ that contain a
    plugin.yaml."""
    return sorted(
        p.parent for p in EXAMPLES_DIR.glob("*/plugin.yaml")
    )


def test_examples_dir_exists():
    assert EXAMPLES_DIR.is_dir(), f"missing {EXAMPLES_DIR}"


def test_at_least_three_example_plugins():
    """ADR-0043 T5 calls for 3-5 canonical examples."""
    dirs = _example_dirs()
    assert len(dirs) >= 3, f"only {len(dirs)} examples found in {EXAMPLES_DIR}"


@pytest.mark.parametrize("plugin_dir", _example_dirs(), ids=lambda p: p.name)
def test_example_manifest_parses(plugin_dir: Path):
    """Every example's plugin.yaml validates against the
    PluginManifest schema."""
    manifest = load_manifest(plugin_dir / "plugin.yaml")
    assert manifest.schema_version == 1
    assert manifest.name == plugin_dir.name, (
        f"manifest name {manifest.name!r} doesn't match dir {plugin_dir.name!r}"
    )


@pytest.mark.parametrize("plugin_dir", _example_dirs(), ids=lambda p: p.name)
def test_example_has_documented_metadata(plugin_dir: Path):
    """Examples should include the optional metadata fields so
    they're useful as authoring templates."""
    manifest = load_manifest(plugin_dir / "plugin.yaml")
    assert manifest.version, f"{plugin_dir.name} missing version"
    assert manifest.license, f"{plugin_dir.name} missing license"
    # display_label() falls back to name; just confirm the call works
    assert manifest.display_label()


@pytest.mark.parametrize("plugin_dir", _example_dirs(), ids=lambda p: p.name)
def test_example_uses_known_type(plugin_dir: Path):
    manifest = load_manifest(plugin_dir / "plugin.yaml")
    assert manifest.type in PluginType


@pytest.mark.parametrize("plugin_dir", _example_dirs(), ids=lambda p: p.name)
def test_example_uses_known_side_effects(plugin_dir: Path):
    manifest = load_manifest(plugin_dir / "plugin.yaml")
    assert manifest.side_effects in SideEffects


@pytest.mark.parametrize("plugin_dir", _example_dirs(), ids=lambda p: p.name)
def test_example_capabilities_non_empty(plugin_dir: Path):
    """An example with no capabilities is meaningless — it
    contributes nothing to the agent's tool catalog."""
    manifest = load_manifest(plugin_dir / "plugin.yaml")
    assert len(manifest.capabilities) >= 1


@pytest.mark.parametrize("plugin_dir", _example_dirs(), ids=lambda p: p.name)
def test_example_capabilities_match_namespace_convention(plugin_dir: Path):
    """ADR-0043 §plugin.yaml schema documents the
    mcp.<plugin-name>.<tool> namespace convention. Examples
    should follow it so they serve as good templates."""
    manifest = load_manifest(plugin_dir / "plugin.yaml")
    expected_prefix = f"mcp.{manifest.name}."
    for cap in manifest.capabilities:
        assert cap.startswith(expected_prefix), (
            f"{plugin_dir.name}: capability {cap!r} doesn't follow "
            f"the {expected_prefix}* convention"
        )


@pytest.mark.parametrize("plugin_dir", _example_dirs(), ids=lambda p: p.name)
def test_example_entry_point_sha256_format(plugin_dir: Path):
    """Examples ship with a placeholder all-zeros sha256
    (operator updates before installing). Confirm the format
    is structurally valid even though the value is bogus."""
    manifest = load_manifest(plugin_dir / "plugin.yaml")
    assert len(manifest.entry_point.sha256) == 64
    assert all(c in "0123456789abcdef" for c in manifest.entry_point.sha256)


def test_readme_present():
    """examples/plugins/README.md is the manifest format
    reference — load-bearing for new authors."""
    assert (EXAMPLES_DIR / "README.md").is_file()


def test_contributing_present():
    """CONTRIBUTING.md describes the registry submission flow."""
    assert (EXAMPLES_DIR / "CONTRIBUTING.md").is_file()
