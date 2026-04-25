"""Unit tests for the ADR-0018 T2.5 tool constraint policy.

Verifies: defaults are applied; trait conditions match correctly;
side-effects targeting works; later rules layer on earlier ones; the
"always" filesystem and external rules can't be bypassed by trait
values; rule_names() returns every rule.
"""
from __future__ import annotations

import pytest

from forest_soul_forge.core.tool_catalog import ToolDef
from forest_soul_forge.core.tool_policy import (
    DEFAULT_CONSTRAINTS,
    resolve_constraints,
    resolve_kit_constraints,
    rule_names,
)


# ---------------------------------------------------------------------------
# Fixtures — minimal trait profiles + tool defs
# ---------------------------------------------------------------------------
class _StubProfile:
    """Trait profile stub. We only need ``trait_values``."""

    def __init__(self, trait_values: dict[str, int]) -> None:
        self.trait_values = dict(trait_values)


def _tool(name: str, side_effects: str = "read_only") -> ToolDef:
    return ToolDef(
        name=name,
        version="1",
        description="x",
        input_schema={"type": "object"},
        side_effects=side_effects,
        archetype_tags=("watcher",),
    )


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
class TestDefaults:
    def test_low_caution_read_only_uses_defaults(self):
        profile = _StubProfile({"caution": 30, "thoroughness": 30})
        rc = resolve_constraints(profile, _tool("packet_query"))
        # No rules match a low-caution agent on a read-only tool.
        assert rc.applied_rules == ()
        assert rc.constraints == DEFAULT_CONSTRAINTS

    def test_default_audit_every_call_is_true(self):
        profile = _StubProfile({"caution": 30})
        rc = resolve_constraints(profile, _tool("packet_query"))
        assert rc.constraints["audit_every_call"] is True

    def test_default_max_calls_is_generous(self):
        profile = _StubProfile({"caution": 30})
        rc = resolve_constraints(profile, _tool("packet_query"))
        assert rc.constraints["max_calls_per_session"] == 1000


# ---------------------------------------------------------------------------
# Trait-conditioned rules
# ---------------------------------------------------------------------------
class TestTraitRules:
    def test_high_caution_requires_approval_on_network(self):
        profile = _StubProfile({"caution": 85, "thoroughness": 50})
        rc = resolve_constraints(profile, _tool("dns_lookup", side_effects="network"))
        assert rc.constraints["requires_human_approval"] is True
        assert "high_caution_approval_on_side_effects" in rc.applied_rules

    def test_high_caution_does_not_affect_read_only(self):
        profile = _StubProfile({"caution": 85, "thoroughness": 50})
        rc = resolve_constraints(profile, _tool("packet_query", side_effects="read_only"))
        # The caution rule targets non-read_only side effects; read-only
        # tools stay at defaults.
        assert rc.constraints["requires_human_approval"] is False
        assert "high_caution_approval_on_side_effects" not in rc.applied_rules

    def test_high_thoroughness_caps_network_calls(self):
        profile = _StubProfile({"caution": 30, "thoroughness": 90})
        rc = resolve_constraints(profile, _tool("dns_lookup", side_effects="network"))
        assert rc.constraints["max_calls_per_session"] == 50
        assert "high_thoroughness_caps_external_calls" in rc.applied_rules

    def test_high_thoroughness_does_not_cap_read_only(self):
        profile = _StubProfile({"caution": 30, "thoroughness": 90})
        rc = resolve_constraints(profile, _tool("packet_query", side_effects="read_only"))
        assert rc.constraints["max_calls_per_session"] == 1000
        assert "high_thoroughness_caps_external_calls" not in rc.applied_rules

    def test_high_caution_and_thoroughness_layer(self):
        """Rules are independent: a high-caution + high-thoroughness
        agent gets BOTH approval-required and the call-cap on a network
        tool. Order doesn't matter — both apply."""
        profile = _StubProfile({"caution": 85, "thoroughness": 90})
        rc = resolve_constraints(profile, _tool("dns_lookup", side_effects="network"))
        assert rc.constraints["requires_human_approval"] is True
        assert rc.constraints["max_calls_per_session"] == 50
        assert "high_caution_approval_on_side_effects" in rc.applied_rules
        assert "high_thoroughness_caps_external_calls" in rc.applied_rules


# ---------------------------------------------------------------------------
# Always-on safety floor — can't be bypassed by trait values
# ---------------------------------------------------------------------------
class TestSafetyFloor:
    def test_filesystem_always_human_approval_low_caution(self):
        """Even a low-caution agent gets approval-required on
        filesystem tools. The safety floor doesn't depend on traits."""
        profile = _StubProfile({"caution": 10, "thoroughness": 10})
        rc = resolve_constraints(profile, _tool("file_write", side_effects="filesystem"))
        assert rc.constraints["requires_human_approval"] is True
        assert "filesystem_always_human_approval" in rc.applied_rules

    def test_external_always_human_approval_low_caution(self):
        profile = _StubProfile({"caution": 10, "thoroughness": 10})
        rc = resolve_constraints(profile, _tool("send_email", side_effects="external"))
        assert rc.constraints["requires_human_approval"] is True
        assert "external_always_human_approval" in rc.applied_rules

    def test_external_high_thoroughness_layers_with_safety_floor(self):
        """A high-thoroughness agent on an external tool gets BOTH the
        always-rule's approval AND the thoroughness rule's call-cap."""
        profile = _StubProfile({"caution": 10, "thoroughness": 95})
        rc = resolve_constraints(profile, _tool("send_email", side_effects="external"))
        assert rc.constraints["requires_human_approval"] is True
        assert rc.constraints["max_calls_per_session"] == 50
        assert "external_always_human_approval" in rc.applied_rules
        assert "high_thoroughness_caps_external_calls" in rc.applied_rules


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------
class TestEdgeCases:
    def test_missing_trait_does_not_match_rule(self):
        """If the agent's profile lacks a trait the rule references,
        the rule reports a non-match rather than crashing — the daemon
        keeps producing constitutions."""
        profile = _StubProfile({})  # no caution, no thoroughness
        rc = resolve_constraints(profile, _tool("dns_lookup", side_effects="network"))
        # No trait-conditioned rules matched. Defaults preserved.
        assert "high_caution_approval_on_side_effects" not in rc.applied_rules
        assert "high_thoroughness_caps_external_calls" not in rc.applied_rules
        assert rc.constraints["requires_human_approval"] is False

    def test_resolved_constraints_to_dict_is_sorted(self):
        """Sorted-keys output means two equivalent resolutions produce
        byte-identical dicts — important for constitution_hash
        determinism."""
        profile = _StubProfile({"caution": 85})
        rc = resolve_constraints(profile, _tool("dns_lookup", side_effects="network"))
        d = rc.to_dict()
        assert list(d["constraints"].keys()) == sorted(d["constraints"].keys())

    def test_rule_names_returns_all_four(self):
        names = rule_names()
        assert len(names) == 4
        assert "high_caution_approval_on_side_effects" in names
        assert "high_thoroughness_caps_external_calls" in names
        assert "filesystem_always_human_approval" in names
        assert "external_always_human_approval" in names

    def test_resolve_kit_constraints_preserves_order(self):
        profile = _StubProfile({"caution": 85})
        kit = [
            _tool("a"),
            _tool("b", side_effects="network"),
            _tool("c"),
        ]
        results = resolve_kit_constraints(profile, kit)
        assert [r.tool_name for r in results] == ["a", "b", "c"]
        # Only b (network) is touched by the caution rule.
        assert results[0].constraints["requires_human_approval"] is False
        assert results[1].constraints["requires_human_approval"] is True
        assert results[2].constraints["requires_human_approval"] is False
