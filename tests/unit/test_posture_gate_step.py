"""ADR-0045 T1 (Burst 114) — PostureGateStep tests.

Three coverage layers:

1. **Schema column** — agents.posture exists at v15 with the
   correct CHECK constraint and 'yellow' default.

2. **Step semantics (agent-only, T1)** — green is no-op, red
   refuses non-read-only, yellow elevates non-read-only to PENDING.
   Read-only short-circuits regardless of posture. None posture is
   no-op (test-context safety).

3. **T3 forward-compat hook** — when ``enforce_per_grant=False``
   (T1 default), the per-grant tier is recorded on dctx but never
   consulted. The Burst 115 enforcement enable + per-grant
   precedence-matrix tests live in test_posture_per_grant.py.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from forest_soul_forge.registry import Registry
from forest_soul_forge.tools.governance_pipeline import (
    DispatchContext,
    PostureGateStep,
    StepResult,
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


def _dctx(
    *,
    posture: str | None,
    side_effects: str = "external",
    tool_name: str = "memory_write",
    args: dict[str, Any] | None = None,
    plugin_grants_view: dict[str, str] | None = None,
    resolved_side_effects: str | None = None,
) -> DispatchContext:
    return DispatchContext(
        instance_id="test_agent",
        agent_dna="testdna",
        role="test_role",
        genre=None,
        session_id="s1",
        constitution_path=Path("/dev/null"),
        tool_name=tool_name,
        tool_version="1",
        args=args or {},
        agent_posture=posture,
        plugin_grants_view=plugin_grants_view,
        tool=_StubTool(side_effects=side_effects),
        resolved=_StubResolved(side_effects=resolved_side_effects),
    )


# ---- schema ----------------------------------------------------------------

def test_agents_posture_column_exists(tmp_path: Path):
    db = tmp_path / "r.db"
    Registry.bootstrap(db).close()
    raw = sqlite3.connect(str(db))
    cols = [r[1] for r in raw.execute("PRAGMA table_info(agents);").fetchall()]
    assert "posture" in cols


def test_agents_posture_defaults_to_yellow(tmp_path: Path):
    db = tmp_path / "r.db"
    r = Registry.bootstrap(db)
    r._conn.execute(
        "INSERT INTO agents(instance_id, dna, dna_full, role, agent_name, "
        "soul_path, constitution_path, constitution_hash, created_at) "
        "VALUES('a', 'x', 'xx', 'r', 'n', 's', 'c', 'h', '2026-05-05T00:00:00Z')"
    )
    r._conn.commit()
    row = r._conn.execute(
        "SELECT posture FROM agents WHERE instance_id='a';"
    ).fetchone()
    assert row[0] == "yellow"


def test_agents_posture_check_constraint(tmp_path: Path):
    db = tmp_path / "r.db"
    r = Registry.bootstrap(db)
    with pytest.raises(sqlite3.IntegrityError):
        r._conn.execute(
            "INSERT INTO agents(instance_id, dna, dna_full, role, agent_name, "
            "soul_path, constitution_path, constitution_hash, created_at, "
            "posture) "
            "VALUES('a', 'x', 'xx', 'r', 'n', 's', 'c', 'h', "
            "'2026-05-05T00:00:00Z', 'magenta')"
        )


# ---- step semantics --------------------------------------------------------

class TestPostureGateStep:
    def test_none_posture_is_noop(self):
        # Test contexts (no agent_registry wired) → step short-circuits.
        result = PostureGateStep().evaluate(_dctx(posture=None))
        assert result.verdict == "GO"

    def test_green_is_noop(self):
        result = PostureGateStep().evaluate(
            _dctx(posture="green", side_effects="external"),
        )
        assert result.verdict == "GO"

    def test_yellow_elevates_external_to_pending(self):
        result = PostureGateStep().evaluate(
            _dctx(posture="yellow", side_effects="external"),
        )
        assert result.verdict == "PENDING"
        assert result.gate_source == "posture_yellow"
        assert result.side_effects == "external"

    def test_yellow_elevates_filesystem_to_pending(self):
        result = PostureGateStep().evaluate(
            _dctx(posture="yellow", side_effects="filesystem"),
        )
        assert result.verdict == "PENDING"
        assert result.gate_source == "posture_yellow"

    def test_yellow_elevates_network_to_pending(self):
        result = PostureGateStep().evaluate(
            _dctx(posture="yellow", side_effects="network"),
        )
        assert result.verdict == "PENDING"

    def test_yellow_passes_read_only(self):
        result = PostureGateStep().evaluate(
            _dctx(posture="yellow", side_effects="read_only"),
        )
        # Read-only ALWAYS bypasses posture — agent can still
        # think + read regardless of trust state.
        assert result.verdict == "GO"

    def test_red_refuses_external(self):
        result = PostureGateStep().evaluate(
            _dctx(posture="red", side_effects="external"),
        )
        assert result.verdict == "REFUSE"
        assert result.reason == "agent_posture_red"

    def test_red_refuses_filesystem(self):
        result = PostureGateStep().evaluate(
            _dctx(posture="red", side_effects="filesystem"),
        )
        assert result.verdict == "REFUSE"

    def test_red_refuses_network(self):
        result = PostureGateStep().evaluate(
            _dctx(posture="red", side_effects="network"),
        )
        assert result.verdict == "REFUSE"

    def test_red_passes_read_only(self):
        """Red blocks ACTIONS, not THINKING. Read-only tools flow
        through so the agent can still observe + reason."""
        result = PostureGateStep().evaluate(
            _dctx(posture="red", side_effects="read_only"),
        )
        assert result.verdict == "GO"

    def test_resolved_side_effects_overrides_tool_default(self):
        """When the constitution tightens side_effects, the step
        consults the resolved value, not the tool's static default."""
        # Tool says external; resolved says read_only → bypass.
        result = PostureGateStep().evaluate(_dctx(
            posture="red",
            side_effects="external",
            resolved_side_effects="read_only",
        ))
        assert result.verdict == "GO"
        # Tool says read_only; resolved says external → red refuses.
        result = PostureGateStep().evaluate(_dctx(
            posture="red",
            side_effects="read_only",
            resolved_side_effects="external",
        ))
        assert result.verdict == "REFUSE"

    def test_t3_per_grant_disabled_by_default(self):
        """T1 default has enforce_per_grant=False — the per-grant
        view is loaded but never consulted. A red agent stays red
        even if grant is green; a yellow agent doesn't downgrade."""
        # Yellow agent + green grant on this MCP server. With T3
        # disabled (T1 default), agent posture wins → PENDING.
        result = PostureGateStep(enforce_per_grant=False).evaluate(
            _dctx(
                posture="yellow",
                side_effects="external",
                tool_name="mcp_call",
                args={"server_name": "github", "tool_name": "search"},
                plugin_grants_view={"github": "green"},
            ),
        )
        assert result.verdict == "PENDING"
        assert result.gate_source == "posture_yellow"
