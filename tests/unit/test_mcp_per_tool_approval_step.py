"""ADR-0043 follow-up Burst 111 — per-tool ``requires_human_approval``
mirroring tests.

Two layers of coverage:

1. **Runtime view shape** — :meth:`PluginRuntime.mcp_servers_view`
   emits the new ``requires_human_approval_per_tool`` field straight
   from the manifest, alongside the existing per-server
   ``requires_human_approval`` bool kept for back-compat.

2. **Pipeline step semantics** — :class:`McpPerToolApprovalStep`
   forces ``requires_human_approval=True`` on
   ``dctx.resolved.constraints`` when the dispatched tool is
   ``mcp_call.v1`` AND the registry's per-tool map says the specific
   ``tool_name`` requires approval. No-ops otherwise (other tools,
   missing registry, missing per-tool map, ungated tool).

Each step test runs the step against a synthetic ``DispatchContext``
+ a stub ``resolved`` object that mimics the
``_ResolvedToolConstraints`` shape from dispatcher.py — keeps the
unit isolated from constitution loading + tool registry.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

from forest_soul_forge.daemon.plugins_runtime import PluginRuntime
from forest_soul_forge.plugins import PluginRepository
from forest_soul_forge.tools.governance_pipeline import (
    DispatchContext,
    McpPerToolApprovalStep,
    StepResult,
)


# ---------------------------------------------------------------------------
# Helpers — minimal plugin-on-disk + a stub resolved-constraints shape so the
# step can run without loading a real constitution.
# ---------------------------------------------------------------------------

def _write_plugin_with_per_tool_gating(
    base: Path,
    *,
    name: str = "fs-mcp",
    requires_approval: dict[str, bool] | None = None,
) -> Path:
    """Plant a plugin manifest with a specific per-tool approval map.

    Default map: read_file ungated, write_file gated. Mirrors the
    canonical filesystem-reference example in examples/plugins/.
    """
    src = base / f"src-{name}"
    src.mkdir(parents=True)
    binary = src / "server"
    binary.write_bytes(b"#!/bin/sh\necho hi\n")
    sha = hashlib.sha256(b"#!/bin/sh\necho hi\n").hexdigest()
    if requires_approval is None:
        requires_approval = {"read_file": False, "write_file": True}
    capabilities = [f"mcp.{name}.{tool}" for tool in requires_approval]
    (src / "plugin.yaml").write_text(yaml.safe_dump({
        "schema_version": 1,
        "name": name,
        "version": "0.1.0",
        "type": "mcp_server",
        "side_effects": "filesystem",
        "entry_point": {
            "type": "stdio",
            "command": "./server",
            "sha256": sha,
        },
        "capabilities": capabilities,
        "requires_human_approval": requires_approval,
    }))
    return src


def _runtime_with_plugin(
    tmp_path: Path,
    *,
    name: str = "fs-mcp",
    requires_approval: dict[str, bool] | None = None,
) -> PluginRuntime:
    repo = PluginRepository(root=tmp_path / "plugins")
    repo.install_from_dir(_write_plugin_with_per_tool_gating(
        tmp_path,
        name=name,
        requires_approval=requires_approval,
    ))
    rt = PluginRuntime(repo)
    rt.reload()
    return rt


@dataclass
class _StubResolved:
    """Minimal stand-in for _ResolvedToolConstraints. The step only
    reads/writes ``constraints`` and ``applied_rules``; nothing else
    matters."""
    constraints: dict[str, Any] = field(default_factory=dict)
    applied_rules: list[str] = field(default_factory=list)
    side_effects: str | None = None


def _make_dctx(
    *,
    tool_name: str,
    args: dict[str, Any],
    resolved: _StubResolved | None,
    mcp_registry: dict[str, Any] | None,
) -> DispatchContext:
    """Build a minimal DispatchContext for step testing."""
    return DispatchContext(
        instance_id="test-agent",
        agent_dna="testdna",
        role="test_role",
        genre=None,
        session_id="s1",
        constitution_path=Path("/dev/null"),
        tool_name=tool_name,
        tool_version="1",
        args=args,
        provider=None,
        task_caps=None,
        mcp_registry=mcp_registry,
        resolved=resolved,
    )


# ---------------------------------------------------------------------------
# Layer 1 — runtime view shape (mcp_servers_view emits the new field).
# ---------------------------------------------------------------------------

def test_runtime_view_emits_per_tool_map(tmp_path: Path):
    rt = _runtime_with_plugin(tmp_path)
    view = rt.mcp_servers_view()
    entry = view["fs-mcp"]
    assert "requires_human_approval_per_tool" in entry
    per_tool = entry["requires_human_approval_per_tool"]
    assert per_tool == {"read_file": False, "write_file": True}


def test_runtime_view_preserves_per_server_bool(tmp_path: Path):
    """The per-server bool field stays for back-compat — any True
    in the manifest map flips it. New per-tool field is additive."""
    rt = _runtime_with_plugin(tmp_path)  # default: write_file=True
    entry = rt.mcp_servers_view()["fs-mcp"]
    assert entry["requires_human_approval"] is True
    assert entry["requires_human_approval_per_tool"]["read_file"] is False


def test_runtime_view_per_server_bool_false_when_no_tool_gates(tmp_path: Path):
    rt = _runtime_with_plugin(
        tmp_path,
        name="search-mcp",
        requires_approval={"search": False, "lookup": False},
    )
    entry = rt.mcp_servers_view()["search-mcp"]
    assert entry["requires_human_approval"] is False
    assert entry["requires_human_approval_per_tool"] == {
        "search": False, "lookup": False,
    }


def test_runtime_view_per_tool_dict_is_defensive_copy(tmp_path: Path):
    """Mutating the returned per_tool dict must not corrupt the
    next view() call. Manifest is the source of truth."""
    rt = _runtime_with_plugin(tmp_path)
    view1 = rt.mcp_servers_view()
    view1["fs-mcp"]["requires_human_approval_per_tool"]["read_file"] = True
    view2 = rt.mcp_servers_view()
    # The fresh view shows the original manifest values.
    assert view2["fs-mcp"]["requires_human_approval_per_tool"]["read_file"] is False


# ---------------------------------------------------------------------------
# Layer 2 — pipeline step semantics.
# ---------------------------------------------------------------------------

def _registry_for(tool_map: dict[str, bool]) -> dict[str, Any]:
    return {
        "fs-mcp": {
            "url": "stdio:./server",
            "sha256": "0" * 64,
            "side_effects": "filesystem",
            "requires_human_approval": any(tool_map.values()),
            "requires_human_approval_per_tool": tool_map,
            "allowlisted_tools": list(tool_map.keys()),
            "description": "fs-mcp v0.1.0 (plugin)",
        },
    }


def test_step_forces_approval_for_gated_tool():
    """Per-tool True → step mutates resolved.constraints."""
    resolved = _StubResolved(constraints={"requires_human_approval": False})
    dctx = _make_dctx(
        tool_name="mcp_call",
        args={"server_name": "fs-mcp", "tool_name": "write_file"},
        resolved=resolved,
        mcp_registry=_registry_for({"read_file": False, "write_file": True}),
    )
    result = McpPerToolApprovalStep().evaluate(dctx)
    # Step itself returns GO — actual elevation happens downstream
    # in ApprovalGateStep, which now sees the forced constraint.
    assert result.verdict == "GO"
    assert dctx.resolved.constraints["requires_human_approval"] is True
    # Audit breadcrumb captures which (server, tool) tripped.
    assert any(
        rule.startswith("mcp_per_tool_approval[fs-mcp.write_file]")
        for rule in dctx.resolved.applied_rules
    )


def test_step_leaves_ungated_tool_alone():
    """Per-tool False → no mutation, GO."""
    resolved = _StubResolved(constraints={"requires_human_approval": False})
    dctx = _make_dctx(
        tool_name="mcp_call",
        args={"server_name": "fs-mcp", "tool_name": "read_file"},
        resolved=resolved,
        mcp_registry=_registry_for({"read_file": False, "write_file": True}),
    )
    result = McpPerToolApprovalStep().evaluate(dctx)
    assert result.verdict == "GO"
    assert dctx.resolved.constraints["requires_human_approval"] is False
    assert dctx.resolved.applied_rules == []


def test_step_noop_for_non_mcp_call_tool():
    """Per-tool gating is mcp-specific. Other tools are untouched."""
    resolved = _StubResolved(constraints={"requires_human_approval": False})
    dctx = _make_dctx(
        tool_name="memory_write",
        args={"key": "k", "value": "v"},
        resolved=resolved,
        mcp_registry=_registry_for({"write_file": True}),
    )
    result = McpPerToolApprovalStep().evaluate(dctx)
    assert result.verdict == "GO"
    assert dctx.resolved.constraints["requires_human_approval"] is False


def test_step_noop_when_registry_is_none():
    """No registry wired (test contexts) → step short-circuits to GO."""
    resolved = _StubResolved(constraints={"requires_human_approval": False})
    dctx = _make_dctx(
        tool_name="mcp_call",
        args={"server_name": "fs-mcp", "tool_name": "write_file"},
        resolved=resolved,
        mcp_registry=None,
    )
    result = McpPerToolApprovalStep().evaluate(dctx)
    assert result.verdict == "GO"
    assert dctx.resolved.constraints["requires_human_approval"] is False


def test_step_noop_when_server_missing_from_registry():
    """Server isn't in the merged registry — step doesn't crash, just
    GO. mcp_call.v1's own validation will refuse cleanly downstream."""
    resolved = _StubResolved(constraints={"requires_human_approval": False})
    dctx = _make_dctx(
        tool_name="mcp_call",
        args={"server_name": "unknown-mcp", "tool_name": "write_file"},
        resolved=resolved,
        mcp_registry=_registry_for({"write_file": True}),  # only fs-mcp
    )
    result = McpPerToolApprovalStep().evaluate(dctx)
    assert result.verdict == "GO"
    assert dctx.resolved.constraints["requires_human_approval"] is False


def test_step_noop_when_per_tool_map_absent():
    """YAML-registered server lacking the per-tool map → step is
    no-op (the YAML registry shape predates this field)."""
    resolved = _StubResolved(constraints={"requires_human_approval": False})
    yaml_only_registry = {
        "yaml-mcp": {
            "url": "stdio:./yaml",
            "sha256": "f" * 64,
            "side_effects": "network",
            "requires_human_approval": False,
            # NO requires_human_approval_per_tool — pre-Burst-111 shape
            "allowlisted_tools": ["search"],
        },
    }
    dctx = _make_dctx(
        tool_name="mcp_call",
        args={"server_name": "yaml-mcp", "tool_name": "search"},
        resolved=resolved,
        mcp_registry=yaml_only_registry,
    )
    result = McpPerToolApprovalStep().evaluate(dctx)
    assert result.verdict == "GO"
    assert dctx.resolved.constraints["requires_human_approval"] is False


def test_step_noop_when_resolved_is_none():
    """Earlier pipeline step refused / hasn't populated resolved →
    step doesn't try to mutate anything."""
    dctx = _make_dctx(
        tool_name="mcp_call",
        args={"server_name": "fs-mcp", "tool_name": "write_file"},
        resolved=None,
        mcp_registry=_registry_for({"write_file": True}),
    )
    result = McpPerToolApprovalStep().evaluate(dctx)
    assert result.verdict == "GO"


def test_step_noop_when_args_missing_keys():
    """Args lack server_name or tool_name (validator should catch
    upstream, but be defensive) → step doesn't crash."""
    resolved = _StubResolved(constraints={"requires_human_approval": False})
    dctx = _make_dctx(
        tool_name="mcp_call",
        args={"server_name": "fs-mcp"},  # missing tool_name
        resolved=resolved,
        mcp_registry=_registry_for({"write_file": True}),
    )
    result = McpPerToolApprovalStep().evaluate(dctx)
    assert result.verdict == "GO"


def test_step_preserves_existing_applied_rules():
    """A constraint resolution upstream already populated
    applied_rules — the step appends, doesn't clobber."""
    resolved = _StubResolved(
        constraints={"requires_human_approval": False},
        applied_rules=["upstream_rule_a", "upstream_rule_b"],
    )
    dctx = _make_dctx(
        tool_name="mcp_call",
        args={"server_name": "fs-mcp", "tool_name": "write_file"},
        resolved=resolved,
        mcp_registry=_registry_for({"write_file": True}),
    )
    McpPerToolApprovalStep().evaluate(dctx)
    assert "upstream_rule_a" in dctx.resolved.applied_rules
    assert "upstream_rule_b" in dctx.resolved.applied_rules
    assert any(
        rule.startswith("mcp_per_tool_approval[")
        for rule in dctx.resolved.applied_rules
    )


def test_step_preserves_constraint_already_true():
    """If something upstream already forced
    requires_human_approval=True, the step still appends the audit
    breadcrumb (per-tool match was the additional reason). Idempotent
    on the bool itself."""
    resolved = _StubResolved(constraints={"requires_human_approval": True})
    dctx = _make_dctx(
        tool_name="mcp_call",
        args={"server_name": "fs-mcp", "tool_name": "write_file"},
        resolved=resolved,
        mcp_registry=_registry_for({"write_file": True}),
    )
    McpPerToolApprovalStep().evaluate(dctx)
    assert dctx.resolved.constraints["requires_human_approval"] is True
    assert any(
        rule.startswith("mcp_per_tool_approval[fs-mcp.write_file]")
        for rule in dctx.resolved.applied_rules
    )
