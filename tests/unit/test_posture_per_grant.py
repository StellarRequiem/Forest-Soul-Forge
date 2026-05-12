"""ADR-0045 T3+T4 (Burst 115) — per-grant trust_tier enforcement.

Burst 114 shipped agent-only enforcement; Burst 115 flips
``enforce_per_grant=True`` on PostureGateStep so the per-grant
trust_tier from agent_plugin_grants starts overriding agent posture
for mcp_call.v1 dispatches.

Precedence rule from ADR-0045 §"Interaction with per-grant
trust_tier": **red dominates > yellow > green** across (agent
posture, per-grant tier). The strongest signal wins.

The 3×3 precedence matrix:

    agent →    │ green       │ yellow      │ red
    grant ↓    │             │             │
    ───────────┼─────────────┼─────────────┼────────────
    green      │ GO          │ GO (1)      │ REFUSE (red)
    yellow     │ PENDING     │ PENDING     │ REFUSE (red)
    red        │ REFUSE      │ REFUSE      │ REFUSE
    none       │ GO          │ PENDING     │ REFUSE

(1) Per ADR-0045 §"Interaction with per-grant trust_tier":
    "agent-yellow + grant-green for the specific plugin = ungated
    for that mcp_call". The per-grant green downgrades the agent
    posture for THIS specific mcp_call, but only when the grant is
    explicitly green (not just absent).

Read-only short-circuits all 9 cases — posture only gates non-
read-only side_effects.

Non-mcp_call dispatches don't consult per-grant tier at all (it's
plugin-specific) — they fall back to agent-only enforcement,
matching Burst 114 semantics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from forest_soul_forge.tools.governance_pipeline import (
    DispatchContext,
    PostureGateStep,
)


# ---- helpers ---------------------------------------------------------------

@dataclass
class _StubResolved:
    constraints: dict[str, Any] = field(default_factory=dict)
    applied_rules: list[str] = field(default_factory=list)
    side_effects: str | None = None


@dataclass
class _StubTool:
    side_effects: str = "external"


def _mcp_dctx(
    *,
    posture: str,
    grant_tier: str | None,
    side_effects: str = "external",
    server_name: str = "github",
) -> DispatchContext:
    """DispatchContext for an mcp_call.v1 dispatch with the
    given posture + grant tier."""
    grants_view = (
        {server_name: grant_tier} if grant_tier is not None else {}
    )
    return DispatchContext(
        instance_id="agent_a",
        agent_dna="dna",
        role="role",
        genre=None,
        session_id="s1",
        constitution_path=Path("/dev/null"),
        tool_name="mcp_call",
        tool_version="1",
        args={"server_name": server_name, "tool_name": "search"},
        agent_posture=posture,
        plugin_grants_view=grants_view,
        tool=_StubTool(side_effects=side_effects),
        resolved=_StubResolved(),
    )


def _step():
    """Step with per-grant enforcement on (Burst 115 default at
    the dispatcher level)."""
    return PostureGateStep(enforce_per_grant=True)


# ---- precedence matrix: 9 (agent × grant) cases ----------------------------

class TestPrecedenceMatrix:
    """All 9 combinations of agent posture × per-grant tier for an
    mcp_call.v1 dispatch with side_effects=external."""

    # green agent
    def test_green_agent_green_grant(self):
        result = _step().evaluate(_mcp_dctx(posture="green", grant_tier="green"))
        assert result.verdict == "GO"

    def test_green_agent_yellow_grant(self):
        # Yellow grant ELEVATES from green agent's no-override.
        result = _step().evaluate(_mcp_dctx(posture="green", grant_tier="yellow"))
        assert result.verdict == "PENDING"
        assert result.gate_source == "posture_yellow"

    def test_green_agent_red_grant(self):
        result = _step().evaluate(_mcp_dctx(posture="green", grant_tier="red"))
        assert result.verdict == "REFUSE"
        assert result.reason == "agent_posture_red"

    # yellow agent
    def test_yellow_agent_green_grant(self):
        """Per-grant green DOWNGRADES the agent-yellow gate for
        this specific mcp_call. ADR-0045 §'Interaction with
        per-grant trust_tier': agent-yellow + grant-green = ungated
        for that mcp_call."""
        result = _step().evaluate(_mcp_dctx(posture="yellow", grant_tier="green"))
        assert result.verdict == "GO"

    def test_yellow_agent_yellow_grant(self):
        result = _step().evaluate(_mcp_dctx(posture="yellow", grant_tier="yellow"))
        assert result.verdict == "PENDING"

    def test_yellow_agent_red_grant(self):
        result = _step().evaluate(_mcp_dctx(posture="yellow", grant_tier="red"))
        assert result.verdict == "REFUSE"
        assert result.reason == "agent_posture_red"

    # red agent — red dominates everything
    def test_red_agent_green_grant(self):
        """Red agent dominates even a green grant. Red is the
        strongest signal regardless of source."""
        result = _step().evaluate(_mcp_dctx(posture="red", grant_tier="green"))
        assert result.verdict == "REFUSE"

    def test_red_agent_yellow_grant(self):
        result = _step().evaluate(_mcp_dctx(posture="red", grant_tier="yellow"))
        assert result.verdict == "REFUSE"

    def test_red_agent_red_grant(self):
        result = _step().evaluate(_mcp_dctx(posture="red", grant_tier="red"))
        assert result.verdict == "REFUSE"


# ---- no-grant fallback (matches Burst 114 agent-only behavior) -------------

class TestNoGrantFallback:
    """When no per-grant tier exists for the plugin being called,
    PostureGateStep falls back to agent-only behavior (matches
    Burst 114). 'no grant' = the plugin name isn't in
    plugin_grants_view at all."""

    def test_green_agent_no_grant(self):
        result = _step().evaluate(_mcp_dctx(posture="green", grant_tier=None))
        assert result.verdict == "GO"

    def test_yellow_agent_no_grant(self):
        result = _step().evaluate(_mcp_dctx(posture="yellow", grant_tier=None))
        assert result.verdict == "PENDING"

    def test_red_agent_no_grant(self):
        result = _step().evaluate(_mcp_dctx(posture="red", grant_tier=None))
        assert result.verdict == "REFUSE"


# ---- read-only short-circuits all 9 ----------------------------------------

class TestReadOnlyShortCircuits:
    """Read-only side_effects bypasses posture entirely — even a
    red agent + red grant lets a read-only mcp_call through."""

    @pytest.mark.parametrize("posture", ["green", "yellow", "red"])
    @pytest.mark.parametrize("grant_tier", ["green", "yellow", "red", None])
    def test_read_only_always_passes(self, posture, grant_tier):
        result = _step().evaluate(_mcp_dctx(
            posture=posture, grant_tier=grant_tier,
            side_effects="read_only",
        ))
        assert result.verdict == "GO"


# ---- non-mcp_call ignores per-grant tier ------------------------------------

class TestNonMcpCallIgnoresPerGrant:
    """Per-grant tier is plugin-specific — only consulted when the
    dispatched tool is mcp_call.v1. Other tools fall back to
    agent-only enforcement."""

    def test_memory_write_with_yellow_agent_green_grant(self):
        """Yellow agent + (irrelevant) green grant on github — but
        the dispatch is memory_write, not mcp_call. Agent-only
        posture wins → PENDING."""
        dctx = DispatchContext(
            instance_id="a", agent_dna="d", role="r", genre=None,
            session_id="s", constitution_path=Path("/dev/null"),
            tool_name="memory_write", tool_version="1",
            args={"key": "k", "value": "v"},
            agent_posture="yellow",
            plugin_grants_view={"github": "green"},
            tool=_StubTool(side_effects="external"),
            resolved=_StubResolved(),
        )
        result = _step().evaluate(dctx)
        assert result.verdict == "PENDING"

    def test_memory_write_with_red_agent_green_grant(self):
        dctx = DispatchContext(
            instance_id="a", agent_dna="d", role="r", genre=None,
            session_id="s", constitution_path=Path("/dev/null"),
            tool_name="memory_write", tool_version="1",
            args={"key": "k", "value": "v"},
            agent_posture="red",
            plugin_grants_view={"github": "green"},
            tool=_StubTool(side_effects="external"),
            resolved=_StubResolved(),
        )
        result = _step().evaluate(dctx)
        assert result.verdict == "REFUSE"


# ---- grant for a DIFFERENT plugin doesn't apply ----------------------------

class TestGrantPluginIsolation:
    """Per-grant tier matches by server_name. A green grant on
    'github' doesn't downgrade gating for 'brave-search'."""

    def test_yellow_agent_green_grant_on_other_plugin(self):
        dctx = _mcp_dctx(
            posture="yellow", grant_tier=None,  # no grant for 'brave'
            server_name="brave",
        )
        # Inject a green grant for github (different plugin) —
        # shouldn't affect dispatch to brave.
        dctx.plugin_grants_view = {"github": "green"}
        result = _step().evaluate(dctx)
        assert result.verdict == "PENDING"  # agent-yellow wins, no grant for brave

    def test_red_grant_on_other_plugin_doesnt_refuse_this(self):
        """A red grant on github shouldn't refuse a call to brave."""
        dctx = _mcp_dctx(
            posture="green", grant_tier=None,
            server_name="brave",
        )
        dctx.plugin_grants_view = {"github": "red"}
        result = _step().evaluate(dctx)
        assert result.verdict == "GO"


# ============================================================================
# ADR-0053 T4 (Burst 239) — specificity-wins per-tool resolver
# ============================================================================
# When the dispatcher wires plugin_grant_lookup_fn (the post-B239
# normal path), PostureGateStep prefers it over the flat
# plugin_grants_view. The resolver returns the per-tool grant's
# tier when the dispatched tool has its own grant, else the
# plugin-level grant's tier, else None.

class TestSpecificityWinsResolver:
    """ADR-0053 D3: per-tool grant overrides plugin-level when
    the dispatched (server, tool) has a per-tool grant."""

    def _dctx_with_resolver(
        self,
        *,
        posture: str,
        resolver,
        server_name: str = "github",
        sub_tool: str = "github_list_issues.v1",
    ) -> DispatchContext:
        return DispatchContext(
            instance_id="agent_a",
            agent_dna="dna",
            role="role",
            genre=None,
            session_id="s1",
            constitution_path=Path("/dev/null"),
            tool_name="mcp_call",
            tool_version="1",
            args={"server_name": server_name, "tool_name": sub_tool},
            agent_posture=posture,
            plugin_grants_view=None,  # force the resolver path
            plugin_grant_lookup_fn=resolver,
            tool=_StubTool(side_effects="external"),
            resolved=_StubResolved(),
        )

    def test_per_tool_grant_tier_used_when_resolver_returns_one(self):
        """A per-tool grant at tier "green" should downgrade an
        agent-yellow posture to green for THIS dispatch — same
        semantic as the plugin-level downgrade, just per-tool."""
        called = []

        def resolver(plugin, tool):
            called.append((plugin, tool))
            if plugin == "github" and tool == "github_list_issues.v1":
                return "green"
            return None

        dctx = self._dctx_with_resolver(
            posture="yellow",
            resolver=resolver,
            sub_tool="github_list_issues.v1",
        )
        result = _step().evaluate(dctx)
        assert result.verdict == "GO", result
        assert called == [("github", "github_list_issues.v1")]

    def test_resolver_returns_none_means_no_grant_input(self):
        """Resolver returning None → posture rules unchanged
        (yellow + external = PENDING)."""
        def resolver(plugin, tool):
            return None

        dctx = self._dctx_with_resolver(
            posture="yellow", resolver=resolver,
        )
        result = _step().evaluate(dctx)
        assert result.verdict == "PENDING", result

    def test_per_tool_red_grant_refuses_even_on_green_agent(self):
        """Red per-tool grant signals "this exact tool needs
        gating" — red-dominates per ADR-0045 applies to per-tool."""
        def resolver(plugin, tool):
            if tool == "github_merge_pr.v1":
                return "red"
            return None

        dctx = self._dctx_with_resolver(
            posture="green",
            resolver=resolver,
            sub_tool="github_merge_pr.v1",
        )
        result = _step().evaluate(dctx)
        assert result.verdict == "REFUSE", result

    def test_resolver_preferred_over_plugin_grants_view(self):
        """When both wires are present, the resolver wins."""
        def resolver(plugin, tool):
            return "green"

        dctx = self._dctx_with_resolver(
            posture="yellow",
            resolver=resolver,
        )
        dctx.plugin_grants_view = {"github": "red"}
        result = _step().evaluate(dctx)
        # Resolver says green → agent-yellow + grant-green → GO.
        assert result.verdict == "GO", result

    def test_flat_view_used_when_resolver_absent(self):
        """Back-compat: pre-B239 contexts still use the flat view."""
        dctx = _mcp_dctx(
            posture="yellow", grant_tier="green",
        )
        assert dctx.plugin_grant_lookup_fn is None
        result = _step().evaluate(dctx)
        assert result.verdict == "GO", result

    def test_resolver_called_with_none_tool_when_args_missing_tool(self):
        """Malformed mcp_call request (no tool_name arg) → resolver
        receives None as tool; falls back to plugin-level grant."""
        called = []

        def resolver(plugin, tool):
            called.append((plugin, tool))
            if plugin == "github":
                return "yellow"
            return None

        dctx = DispatchContext(
            instance_id="agent_a",
            agent_dna="dna",
            role="role",
            genre=None,
            session_id="s1",
            constitution_path=Path("/dev/null"),
            tool_name="mcp_call",
            tool_version="1",
            args={"server_name": "github"},  # no tool_name
            agent_posture="green",
            plugin_grants_view=None,
            plugin_grant_lookup_fn=resolver,
            tool=_StubTool(side_effects="external"),
            resolved=_StubResolved(),
        )
        _step().evaluate(dctx)
        assert called == [("github", None)]


# ============================================================================
# ADR-0053 T4 — the resolver itself
# ============================================================================
# Unit-test ToolDispatcher._resolve_plugin_grant_tier directly
# against a minimal Registry + PluginGrantsTable.

from forest_soul_forge.registry import Registry
from forest_soul_forge.tools.dispatcher import ToolDispatcher


def _seed_resolver_agent(reg, instance_id: str = "agent_a"):
    reg._conn.execute(
        """
        INSERT INTO agents(instance_id, dna, dna_full, role, agent_name,
                           soul_path, constitution_path, constitution_hash,
                           created_at)
        VALUES(?, 'abc', 'abcdef', 'swarm', 'TestAgent',
               's.md', 'c.yaml', 'hash1', '2026-05-12T00:00:00Z')
        """,
        (instance_id,),
    )
    reg._conn.commit()


@pytest.fixture
def resolver_setup(tmp_path):
    """Minimal Registry + sparse ToolDispatcher wired only with
    plugin_grants so the resolver can run. Avoids the heavy
    __init__ wiring not needed for this targeted test."""
    reg = Registry.bootstrap(tmp_path / "r.db")
    _seed_resolver_agent(reg)
    disp = ToolDispatcher.__new__(ToolDispatcher)
    disp.plugin_grants = reg.plugin_grants
    yield disp, reg
    reg.close()


class TestResolverMethod:
    def test_returns_none_when_no_grants(self, resolver_setup):
        disp, _ = resolver_setup
        assert disp._resolve_plugin_grant_tier(
            "agent_a", "github", "github_list_issues.v1",
        ) is None

    def test_plugin_level_grant_returned_when_no_per_tool(
        self, resolver_setup,
    ):
        disp, reg = resolver_setup
        reg.plugin_grants.grant(
            instance_id="agent_a",
            plugin_name="github",
            trust_tier="yellow",
            granted_at_seq=1,
        )
        assert disp._resolve_plugin_grant_tier(
            "agent_a", "github", "github_list_issues.v1",
        ) == "yellow"

    def test_per_tool_grant_overrides_plugin_level(self, resolver_setup):
        disp, reg = resolver_setup
        reg.plugin_grants.grant(
            instance_id="agent_a",
            plugin_name="github",
            trust_tier="yellow",
            granted_at_seq=1,
        )
        reg.plugin_grants.grant(
            instance_id="agent_a",
            plugin_name="github",
            tool_name="github_list_issues.v1",
            trust_tier="green",
            granted_at_seq=2,
        )
        assert disp._resolve_plugin_grant_tier(
            "agent_a", "github", "github_list_issues.v1",
        ) == "green"

    def test_per_tool_grant_doesnt_apply_to_different_tool(
        self, resolver_setup,
    ):
        disp, reg = resolver_setup
        reg.plugin_grants.grant(
            instance_id="agent_a",
            plugin_name="github",
            trust_tier="yellow",
            granted_at_seq=1,
        )
        reg.plugin_grants.grant(
            instance_id="agent_a",
            plugin_name="github",
            tool_name="github_list_issues.v1",
            trust_tier="green",
            granted_at_seq=2,
        )
        # Different tool — should get the plugin-level fallback.
        assert disp._resolve_plugin_grant_tier(
            "agent_a", "github", "github_merge_pr.v1",
        ) == "yellow"

    def test_only_per_tool_grants_no_plugin_level(self, resolver_setup):
        disp, reg = resolver_setup
        reg.plugin_grants.grant(
            instance_id="agent_a",
            plugin_name="github",
            tool_name="github_list_issues.v1",
            trust_tier="green",
            granted_at_seq=1,
        )
        # Matched tool: returns its tier.
        assert disp._resolve_plugin_grant_tier(
            "agent_a", "github", "github_list_issues.v1",
        ) == "green"
        # Unmatched tool: no fallback because there's no plugin-
        # level grant for github. Returns None.
        assert disp._resolve_plugin_grant_tier(
            "agent_a", "github", "github_merge_pr.v1",
        ) is None

    def test_resolver_returns_none_when_table_unwired(self):
        """Dispatcher without plugin_grants returns None
        defensively rather than raising."""
        disp = ToolDispatcher.__new__(ToolDispatcher)
        disp.plugin_grants = None
        assert disp._resolve_plugin_grant_tier(
            "agent_a", "github", "github_list_issues.v1",
        ) is None
