"""Unit tests for B428 constitution.build() tool_constraints merge.

B416 added `tool_constraints` to the code_reviewer role_base template
intending its per-tool keys to merge into the matching tool's
`constraints` dict at build time. The merge step was missing — template
data sat ignored. After ADR-0083 fixed the idempotency replay wedge,
the rebirth ran but produced an identical constitution_hash because
no overrides were being applied. B428 wires the merge.

These tests pin the contract:
  - Tools matching a `{name}.v{version}` key in the template's
    `tool_constraints` block get those keys merged into their
    `constraints` dict.
  - Tools without a matching override pass through unchanged.
  - Override wins for shared keys; new keys (e.g. `allowed_paths`)
    land alongside existing ones (e.g. `audit_every_call`).
  - Tools list is empty / overrides absent → no-op (no crash).
  - The merge happens regardless of which role's template is used.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from forest_soul_forge.core.constitution import build
from forest_soul_forge.core.trait_engine import TraitEngine


@pytest.fixture
def trait_engine_default() -> TraitEngine:
    """A real TraitEngine bootstrapped from the shipped trait tree."""
    return TraitEngine.from_yaml(Path("config/trait_tree.yaml"))


def _make_templates(tmp_path: Path, role_base: dict) -> Path:
    """Write a minimal templates YAML with a single role_base for testing."""
    templates = {
        "schema_version": 1,
        "role_base": {"network_watcher": role_base},
        "trait_modifiers": [],
        "flagged_combo_policy_template": {
            "rule": "forbid",
            "triggers": ["any_state_change"],
        },
    }
    p = tmp_path / "templates.yaml"
    p.write_text(yaml.safe_dump(templates))
    return p


def _baseline_role(extra: dict | None = None) -> dict:
    """Minimal valid role_base for testing. extra= merges additional keys."""
    base = {
        "policies": [],
        "out_of_scope": [],
        "operator_duties": [],
        "risk_thresholds": {
            "auto_halt_risk": 0.7,
            "escalate_risk": 0.35,
            "min_confidence_to_act": 0.5,
        },
        "drift_monitoring": {
            "profile_hash_check": "per_turn",
            "max_profile_deviation": 0,
            "on_drift": "halt",
        },
    }
    if extra:
        base.update(extra)
    return base


def test_tool_constraints_override_merges_into_matching_tool(
    tmp_path, trait_engine_default
):
    """The canonical B416 case: code_read.v1 in the template's
    tool_constraints block becomes part of the matching tool entry's
    constraints dict after build()."""
    templates_path = _make_templates(
        tmp_path,
        _baseline_role(
            {
                "tool_constraints": {
                    "code_read.v1": {
                        "allowed_paths": ["src/", "docs/"],
                    },
                },
            }
        ),
    )
    profile = trait_engine_default.profile_for("network_watcher", trait_values={})
    pre_tools = (
        {
            "name": "code_read",
            "version": "1",
            "side_effects": "read_only",
            "constraints": {
                "audit_every_call": True,
                "max_calls_per_session": 1000,
                "requires_human_approval": False,
            },
        },
    )
    c = build(
        profile,
        trait_engine_default,
        agent_name="TestAgent",
        templates_path=templates_path,
        tools=pre_tools,
    )
    matched = [t for t in c.tools if t["name"] == "code_read"]
    assert len(matched) == 1
    constraints = matched[0]["constraints"]
    # B416 keys land
    assert constraints["allowed_paths"] == ["src/", "docs/"]
    # Existing keys preserved
    assert constraints["audit_every_call"] is True
    assert constraints["max_calls_per_session"] == 1000
    assert constraints["requires_human_approval"] is False


def test_tool_without_matching_override_passes_through_unchanged(
    tmp_path, trait_engine_default
):
    """An override block exists, but the tool entry doesn't match
    its keys → that tool's constraints are unchanged."""
    templates_path = _make_templates(
        tmp_path,
        _baseline_role(
            {
                "tool_constraints": {
                    "code_read.v1": {"allowed_paths": ["src/"]},
                },
            }
        ),
    )
    profile = trait_engine_default.profile_for("network_watcher", trait_values={})
    pre_tools = (
        {
            "name": "memory_recall",
            "version": "1",
            "side_effects": "read_only",
            "constraints": {"audit_every_call": True},
        },
    )
    c = build(
        profile,
        trait_engine_default,
        agent_name="TestAgent",
        templates_path=templates_path,
        tools=pre_tools,
    )
    matched = [t for t in c.tools if t["name"] == "memory_recall"]
    assert len(matched) == 1
    # Unchanged
    assert matched[0]["constraints"] == {"audit_every_call": True}
    # No allowed_paths leaked
    assert "allowed_paths" not in matched[0]["constraints"]


def test_override_wins_for_shared_keys(tmp_path, trait_engine_default):
    """If the override and the existing constraint share a key, the
    override value wins (consistent with dict.update semantics)."""
    templates_path = _make_templates(
        tmp_path,
        _baseline_role(
            {
                "tool_constraints": {
                    "code_read.v1": {
                        "max_calls_per_session": 50,  # override the default 1000
                        "allowed_paths": ["src/"],
                    },
                },
            }
        ),
    )
    profile = trait_engine_default.profile_for("network_watcher", trait_values={})
    pre_tools = (
        {
            "name": "code_read",
            "version": "1",
            "side_effects": "read_only",
            "constraints": {
                "audit_every_call": True,
                "max_calls_per_session": 1000,
                "requires_human_approval": False,
            },
        },
    )
    c = build(
        profile,
        trait_engine_default,
        agent_name="TestAgent",
        templates_path=templates_path,
        tools=pre_tools,
    )
    constraints = [t for t in c.tools if t["name"] == "code_read"][0]["constraints"]
    assert constraints["max_calls_per_session"] == 50, "override should win"
    assert constraints["audit_every_call"] is True
    assert constraints["allowed_paths"] == ["src/"]


def test_no_tool_constraints_block_is_noop(tmp_path, trait_engine_default):
    """A template without a tool_constraints block leaves tools
    unchanged."""
    templates_path = _make_templates(tmp_path, _baseline_role())
    profile = trait_engine_default.profile_for("network_watcher", trait_values={})
    pre_tools = (
        {
            "name": "code_read",
            "version": "1",
            "side_effects": "read_only",
            "constraints": {"audit_every_call": True},
        },
    )
    c = build(
        profile,
        trait_engine_default,
        agent_name="TestAgent",
        templates_path=templates_path,
        tools=pre_tools,
    )
    constraints = [t for t in c.tools if t["name"] == "code_read"][0]["constraints"]
    assert constraints == {"audit_every_call": True}


def test_empty_tools_with_overrides_is_noop(tmp_path, trait_engine_default):
    """Overrides present + tools list empty: build() does not crash
    and the resulting Constitution has no tools."""
    templates_path = _make_templates(
        tmp_path,
        _baseline_role(
            {
                "tool_constraints": {
                    "code_read.v1": {"allowed_paths": ["src/"]},
                },
            }
        ),
    )
    profile = trait_engine_default.profile_for("network_watcher", trait_values={})
    c = build(
        profile,
        trait_engine_default,
        agent_name="TestAgent",
        templates_path=templates_path,
        tools=(),
    )
    assert c.tools == ()
