"""ADR-0060 T4 / T5 — exhaustive matrix tests for PostureGateStep
catalog-grant interaction (Burst 222).

Verifies the 9-cell matrix from ADR-0060 D4:

    | Agent  | green grant | yellow grant | red grant |
    |--------|-------------|--------------|-----------|
    | green  | GO          | GO           | GO        |
    | yellow | GO          | GO           | PENDING   |
    | red    | PENDING     | PENDING      | REFUSE    |

These tests exercise the step in isolation with synthetic
DispatchContext + mocked tool / resolved fields, so they're
fast and don't require a daemon.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from forest_soul_forge.tools.governance_pipeline import (
    DispatchContext,
    PostureGateStep,
)


def _ctx(agent_posture: str, grant_tier: str | None) -> DispatchContext:
    """Build a minimal DispatchContext exercising the grant matrix
    branch. Tool has external side_effects so the read_only
    short-circuit doesn't fire."""
    tool = MagicMock(side_effects="external")
    resolved = MagicMock(side_effects="external")
    dctx = DispatchContext(
        instance_id="a1",
        agent_dna="dna",
        role="translator",
        genre=None,
        session_id="s",
        constitution_path=Path("/tmp/no"),
        tool_name="translate_to_french",
        tool_version="1",
        args={},
        agent_posture=agent_posture,
        granted_via="catalog_grant" if grant_tier else None,
        granted_trust_tier=grant_tier,
    )
    dctx.tool = tool
    dctx.resolved = resolved
    return dctx


class TestGreenAgent:
    """All three grant tiers ALLOWED on a green agent."""
    def test_green_grant(self):
        r = PostureGateStep().evaluate(_ctx("green", "green"))
        assert r.verdict == "GO"

    def test_yellow_grant(self):
        r = PostureGateStep().evaluate(_ctx("green", "yellow"))
        assert r.verdict == "GO"

    def test_red_grant(self):
        r = PostureGateStep().evaluate(_ctx("green", "red"))
        assert r.verdict == "GO"


class TestYellowAgent:
    """green/yellow grants ALLOWED, red grant requires approval."""
    def test_green_grant(self):
        r = PostureGateStep().evaluate(_ctx("yellow", "green"))
        assert r.verdict == "GO"

    def test_yellow_grant(self):
        r = PostureGateStep().evaluate(_ctx("yellow", "yellow"))
        assert r.verdict == "GO"

    def test_red_grant_pending(self):
        r = PostureGateStep().evaluate(_ctx("yellow", "red"))
        assert r.verdict == "PENDING"
        # Gate source identifies which matrix cell fired so an
        # auditor reading pending_approval events can distinguish
        # "yellow agent + red grant" from other PENDING causes.
        assert r.gate_source == "posture_yellow_grant_red"


class TestRedAgent:
    """green/yellow grants require approval, red grant refused outright."""
    def test_green_grant_pending(self):
        r = PostureGateStep().evaluate(_ctx("red", "green"))
        assert r.verdict == "PENDING"
        assert r.gate_source == "posture_red_grant_lower"

    def test_yellow_grant_pending(self):
        r = PostureGateStep().evaluate(_ctx("red", "yellow"))
        assert r.verdict == "PENDING"
        assert r.gate_source == "posture_red_grant_lower"

    def test_red_grant_refused(self):
        r = PostureGateStep().evaluate(_ctx("red", "red"))
        assert r.verdict == "REFUSE"
        assert r.reason == "agent_posture_red_grant_red"


class TestNonGrantedDispatchUnaffected:
    """When dispatch is NOT grant-sourced (granted_trust_tier is None),
    the matrix branch is skipped and legacy posture logic runs."""
    def test_yellow_agent_no_grant_pending(self):
        # No grant_tier → falls through to legacy yellow → PENDING.
        r = PostureGateStep().evaluate(_ctx("yellow", None))
        assert r.verdict == "PENDING"

    def test_red_agent_no_grant_refuse(self):
        r = PostureGateStep().evaluate(_ctx("red", None))
        assert r.verdict == "REFUSE"
        # Legacy refuse reason, not the new grant-aware one.
        assert r.reason == "agent_posture_red"

    def test_green_agent_no_grant_go(self):
        r = PostureGateStep().evaluate(_ctx("green", None))
        assert r.verdict == "GO"


class TestReadOnlyAlwaysBypassesPosture:
    """ADR-0045: read_only side_effects bypass posture regardless
    of agent posture OR grant tier. The agent can always think
    and inspect."""
    def _ctx_read_only(self, agent: str, grant: str | None):
        tool = MagicMock(side_effects="read_only")
        resolved = MagicMock(side_effects="read_only")
        dctx = DispatchContext(
            instance_id="a1", agent_dna="dna", role="translator",
            genre=None, session_id="s",
            constitution_path=Path("/tmp/no"),
            tool_name="llm_think", tool_version="1", args={},
            agent_posture=agent,
            granted_via="catalog_grant" if grant else None,
            granted_trust_tier=grant,
        )
        dctx.tool = tool
        dctx.resolved = resolved
        return dctx

    def test_red_agent_red_grant_read_only_still_passes(self):
        # Even the doubly-defended case lets read_only through.
        r = PostureGateStep().evaluate(self._ctx_read_only("red", "red"))
        assert r.verdict == "GO"
