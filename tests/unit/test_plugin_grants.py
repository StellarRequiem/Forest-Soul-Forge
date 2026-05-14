"""ADR-0043 follow-up #2 (Burst 113) — plugin grants tests.

Three coverage layers:

1. **Schema bump** — v15 stamp (was v14 at Burst 113a; bumped to v15
   for ADR-0045 agents.posture column at Burst 114), agent_plugin_grants
   table exists, the partial index covers active rows.

2. **PluginGrantsTable semantics** — grant/revoke/list/active_plugin_names
   with correct INSERT OR REPLACE on re-grant, NULL ↔ seq transitions
   on revoke, FK cascade behavior, trust_tier validation.

3. **Dispatcher integration** — _load_constitution_mcp_allowlist
   reads the top-level field (was the long-standing gap that mcp_call.v1
   documented but nothing populated). The dispatcher unions
   constitution servers with active grants and injects the result as
   ctx.constraints["allowed_mcp_servers"].

Audit-event integration + HTTP endpoints + CLI subcommand are out of
scope for Burst 113a (the substrate); they ship in Burst 113b.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from forest_soul_forge.registry import Registry, REGISTRY_SCHEMA_VERSION
from forest_soul_forge.registry.tables.plugin_grants import (
    PluginGrant,
    PluginGrantsTable,
)


# ---- helpers ---------------------------------------------------------------

def _seed_agent(registry: Registry, instance_id: str = "agent_a") -> None:
    """Insert a minimal agents row so FK constraints succeed."""
    registry._conn.execute(
        """
        INSERT INTO agents(instance_id, dna, dna_full, role, agent_name,
                           soul_path, constitution_path, constitution_hash,
                           created_at)
        VALUES(?, 'abc', 'abcdef', 'swarm', 'TestAgent',
               's.md', 'c.yaml', 'hash1', '2026-05-05T00:00:00Z')
        """,
        (instance_id,),
    )
    registry._conn.commit()


@pytest.fixture
def reg(tmp_path: Path):
    db = tmp_path / "r.db"
    r = Registry.bootstrap(db)
    yield r
    r.close()


# ---- schema layer ----------------------------------------------------------

def test_schema_version_is_21():
    """Locked at v20 post-Burst 255 (ADR-0063 T6, reality_anchor_corrections).
    Function name moved through v14 → v15 → v16 → v17 → v18 →
    v19 (Burst 243, agents.public_key) → v20 (Burst 255). Renamed
    each time for honesty."""
    assert REGISTRY_SCHEMA_VERSION == 21


def test_agent_plugin_grants_table_exists(reg: Registry):
    rows = reg._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='agent_plugin_grants';"
    ).fetchall()
    assert rows, "agent_plugin_grants table missing"


def test_active_grants_partial_index_exists(reg: Registry):
    rows = reg._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name='idx_plugin_grants_active';"
    ).fetchall()
    assert rows, "idx_plugin_grants_active partial index missing"


def test_trust_tier_check_constraint(reg: Registry):
    """invalid trust_tier value rejected at the DB layer."""
    _seed_agent(reg)
    with pytest.raises(sqlite3.IntegrityError):
        reg._conn.execute(
            "INSERT INTO agent_plugin_grants "
            "(instance_id, plugin_name, trust_tier, granted_at_seq, granted_at) "
            "VALUES('agent_a', 'p', 'magenta', 1, '2026-05-05T00:00:00Z');"
        )


# ---- table semantics --------------------------------------------------------

def test_grant_then_list_active(reg: Registry):
    _seed_agent(reg)
    reg.plugin_grants.grant(
        instance_id="agent_a",
        plugin_name="github",
        trust_tier="yellow",
        granted_at_seq=10,
        granted_by="alex",
        reason="initial install",
    )
    actives = reg.plugin_grants.list_active("agent_a")
    assert len(actives) == 1
    g = actives[0]
    assert g.plugin_name == "github"
    assert g.trust_tier == "yellow"
    assert g.granted_at_seq == 10
    assert g.granted_by == "alex"
    assert g.reason == "initial install"
    assert g.revoked_at_seq is None
    assert g.is_active


def test_active_plugin_names_returns_set(reg: Registry):
    _seed_agent(reg)
    reg.plugin_grants.grant(
        instance_id="agent_a", plugin_name="github",
        trust_tier="yellow", granted_at_seq=1,
    )
    reg.plugin_grants.grant(
        instance_id="agent_a", plugin_name="brave",
        trust_tier="green", granted_at_seq=2,
    )
    names = reg.plugin_grants.active_plugin_names("agent_a")
    assert names == {"github", "brave"}


def test_revoke_clears_active_keeps_history(reg: Registry):
    _seed_agent(reg)
    reg.plugin_grants.grant(
        instance_id="agent_a", plugin_name="github",
        trust_tier="yellow", granted_at_seq=1,
    )
    affected = reg.plugin_grants.revoke(
        instance_id="agent_a", plugin_name="github",
        revoked_at_seq=2, revoked_by="alex", reason="rotated keys",
    )
    assert affected is True
    assert reg.plugin_grants.list_active("agent_a") == []
    # Historical view still has the row.
    history = reg.plugin_grants.list_all("agent_a")
    assert len(history) == 1
    assert history[0].revoked_at_seq == 2
    assert history[0].revoked_by == "alex"
    assert history[0].reason == "rotated keys"


def test_revoke_returns_false_when_no_active_grant(reg: Registry):
    _seed_agent(reg)
    affected = reg.plugin_grants.revoke(
        instance_id="agent_a", plugin_name="never-granted",
        revoked_at_seq=99,
    )
    assert affected is False


def test_regrant_after_revoke_overwrites(reg: Registry):
    _seed_agent(reg)
    reg.plugin_grants.grant(
        instance_id="agent_a", plugin_name="github",
        trust_tier="yellow", granted_at_seq=1,
    )
    reg.plugin_grants.revoke(
        instance_id="agent_a", plugin_name="github", revoked_at_seq=2,
    )
    reg.plugin_grants.grant(
        instance_id="agent_a", plugin_name="github",
        trust_tier="green", granted_at_seq=3,
    )
    actives = reg.plugin_grants.list_active("agent_a")
    assert len(actives) == 1
    assert actives[0].trust_tier == "green"
    assert actives[0].granted_at_seq == 3
    assert actives[0].revoked_at_seq is None


def test_invalid_trust_tier_raises(reg: Registry):
    _seed_agent(reg)
    with pytest.raises(ValueError, match="trust_tier"):
        reg.plugin_grants.grant(
            instance_id="agent_a", plugin_name="github",
            trust_tier="purple", granted_at_seq=1,
        )


def test_get_active_returns_none_when_revoked(reg: Registry):
    _seed_agent(reg)
    reg.plugin_grants.grant(
        instance_id="agent_a", plugin_name="github",
        trust_tier="yellow", granted_at_seq=1,
    )
    reg.plugin_grants.revoke(
        instance_id="agent_a", plugin_name="github", revoked_at_seq=2,
    )
    assert reg.plugin_grants.get_active("agent_a", "github") is None


def test_get_active_returns_grant_when_active(reg: Registry):
    _seed_agent(reg)
    reg.plugin_grants.grant(
        instance_id="agent_a", plugin_name="github",
        trust_tier="yellow", granted_at_seq=1,
    )
    g = reg.plugin_grants.get_active("agent_a", "github")
    assert g is not None
    assert g.plugin_name == "github"


def test_grants_isolated_per_agent(reg: Registry):
    _seed_agent(reg, "agent_a")
    _seed_agent(reg, "agent_b")
    reg.plugin_grants.grant(
        instance_id="agent_a", plugin_name="github",
        trust_tier="yellow", granted_at_seq=1,
    )
    assert reg.plugin_grants.active_plugin_names("agent_a") == {"github"}
    assert reg.plugin_grants.active_plugin_names("agent_b") == set()


def test_fk_cascade_on_agent_archive(reg: Registry):
    """Deleting the agent (admin path) cascades grants — no orphans."""
    _seed_agent(reg)
    reg.plugin_grants.grant(
        instance_id="agent_a", plugin_name="github",
        trust_tier="yellow", granted_at_seq=1,
    )
    # Force-delete the agent row directly. In production agents are
    # archived (status=archived), not deleted; this test exercises
    # the FK cascade contract for the rebuild path.
    reg._conn.execute("DELETE FROM agents WHERE instance_id='agent_a';")
    reg._conn.commit()
    assert reg.plugin_grants.list_all("agent_a") == []


# ---- dispatcher integration -------------------------------------------------

def test_load_constitution_mcp_allowlist_reads_field(tmp_path: Path):
    from forest_soul_forge.tools.dispatcher import (
        _load_constitution_mcp_allowlist,
    )
    cpath = tmp_path / "c.yaml"
    cpath.write_text(
        "schema_version: 1\n"
        "constitution_hash: abc\n"
        "allowed_mcp_servers:\n"
        "  - github\n"
        "  - linear\n"
    )
    result = _load_constitution_mcp_allowlist(cpath)
    assert result == ("github", "linear")


def test_load_constitution_mcp_allowlist_missing_field(tmp_path: Path):
    from forest_soul_forge.tools.dispatcher import (
        _load_constitution_mcp_allowlist,
    )
    cpath = tmp_path / "c.yaml"
    cpath.write_text("schema_version: 1\nconstitution_hash: abc\n")
    assert _load_constitution_mcp_allowlist(cpath) == ()


def test_load_constitution_mcp_allowlist_missing_file(tmp_path: Path):
    from forest_soul_forge.tools.dispatcher import (
        _load_constitution_mcp_allowlist,
    )
    assert _load_constitution_mcp_allowlist(tmp_path / "nope.yaml") == ()


def test_load_constitution_mcp_allowlist_invalid_yaml(tmp_path: Path):
    from forest_soul_forge.tools.dispatcher import (
        _load_constitution_mcp_allowlist,
    )
    cpath = tmp_path / "c.yaml"
    cpath.write_text("schema_version: 1\n  bad: : :\n")
    # Defensive — corrupt YAML returns empty, doesn't raise.
    assert _load_constitution_mcp_allowlist(cpath) == ()


def test_load_constitution_mcp_allowlist_filters_non_strings(tmp_path: Path):
    """A list with mixed content (someone put an int or null in the YAML
    by mistake) should silently drop the bad entries rather than crash
    or include them."""
    from forest_soul_forge.tools.dispatcher import (
        _load_constitution_mcp_allowlist,
    )
    cpath = tmp_path / "c.yaml"
    cpath.write_text(
        "allowed_mcp_servers:\n"
        "  - github\n"
        "  - 42\n"
        "  - ''\n"
        "  - linear\n"
    )
    result = _load_constitution_mcp_allowlist(cpath)
    assert result == ("github", "linear")


# ============================================================================
# ADR-0053 T2 (Burst 237) — per-tool grant surface
# ============================================================================
# Coverage for the optional ``tool_name`` parameter on grant / revoke /
# get_active / list_active / list_active_for_plugin. Plugin-level
# semantics (tool_name=None, the ADR-0043 original) stay unchanged —
# the 32 pre-existing tests above already prove that path. These
# tests add the per-tool path: per-tool grants coexist with plugin-
# level grants for the same (agent, plugin), get_active distinguishes
# them by the triple key, revoke targets only the specified triple,
# active_plugin_names dedupes when both grant types exist.

def test_grant_per_tool_creates_distinct_row(reg: Registry):
    """A per-tool grant is a separate row from any plugin-level
    grant on the same (agent, plugin). list_active returns both."""
    _seed_agent(reg)

    reg.plugin_grants.grant(
        instance_id="agent_a",
        plugin_name="soulux-computer-control",
        trust_tier="yellow",
        granted_at_seq=10,
        granted_by="alex",
        reason="Restricted preset",
    )
    reg.plugin_grants.grant(
        instance_id="agent_a",
        plugin_name="soulux-computer-control",
        tool_name="computer_screenshot.v1",
        trust_tier="green",
        granted_at_seq=11,
        granted_by="alex",
        reason="elevate just the screenshot tool",
    )

    rows = reg.plugin_grants.list_active("agent_a")
    assert len(rows) == 2
    plugin_level = [r for r in rows if r.is_plugin_level]
    per_tool = [r for r in rows if r.is_per_tool]
    assert len(plugin_level) == 1
    assert len(per_tool) == 1
    assert per_tool[0].tool_name == "computer_screenshot.v1"
    assert per_tool[0].trust_tier == "green"
    assert plugin_level[0].trust_tier == "yellow"


def test_get_active_distinguishes_plugin_level_from_per_tool(reg: Registry):
    """get_active(..., tool_name=None) returns plugin-level only;
    get_active(..., tool_name='X') returns per-tool only. No
    fallback between them — that's the T4 resolver's job, not the
    storage layer's."""
    _seed_agent(reg)
    reg.plugin_grants.grant(
        instance_id="agent_a",
        plugin_name="slack",
        trust_tier="yellow",
        granted_at_seq=20,
    )
    reg.plugin_grants.grant(
        instance_id="agent_a",
        plugin_name="slack",
        tool_name="slack_send_message.v1",
        trust_tier="green",
        granted_at_seq=21,
    )

    plug = reg.plugin_grants.get_active("agent_a", "slack")
    assert plug is not None and plug.is_plugin_level
    assert plug.trust_tier == "yellow"

    per = reg.plugin_grants.get_active(
        "agent_a", "slack", tool_name="slack_send_message.v1",
    )
    assert per is not None and per.is_per_tool
    assert per.trust_tier == "green"

    # No fallback: asking for a tool that has no per-tool row
    # returns None even though the plugin-level row exists.
    missing = reg.plugin_grants.get_active(
        "agent_a", "slack", tool_name="slack_create_channel.v1",
    )
    assert missing is None


def test_revoke_per_tool_leaves_plugin_level_intact(reg: Registry):
    """Revoking the per-tool grant must not touch the plugin-level
    row on the same (agent, plugin). Mirrors the inverse case."""
    _seed_agent(reg)
    reg.plugin_grants.grant(
        instance_id="agent_a",
        plugin_name="github",
        trust_tier="yellow",
        granted_at_seq=30,
    )
    reg.plugin_grants.grant(
        instance_id="agent_a",
        plugin_name="github",
        tool_name="github_merge_pr.v1",
        trust_tier="red",  # extra-cautious — operator wants gating
        granted_at_seq=31,
    )

    affected = reg.plugin_grants.revoke(
        instance_id="agent_a",
        plugin_name="github",
        tool_name="github_merge_pr.v1",
        revoked_at_seq=32,
        revoked_by="alex",
        reason="too risky",
    )
    assert affected is True

    # Plugin-level survives.
    plug = reg.plugin_grants.get_active("agent_a", "github")
    assert plug is not None
    assert plug.is_active

    # Per-tool is gone from active set.
    per = reg.plugin_grants.get_active(
        "agent_a", "github", tool_name="github_merge_pr.v1",
    )
    assert per is None


def test_revoke_plugin_level_leaves_per_tool_intact(reg: Registry):
    """The inverse: revoking the plugin-level grant must not touch
    a per-tool row on the same (agent, plugin)."""
    _seed_agent(reg)
    reg.plugin_grants.grant(
        instance_id="agent_a",
        plugin_name="linear",
        trust_tier="yellow",
        granted_at_seq=40,
    )
    reg.plugin_grants.grant(
        instance_id="agent_a",
        plugin_name="linear",
        tool_name="linear_create_issue.v1",
        trust_tier="green",
        granted_at_seq=41,
    )

    affected = reg.plugin_grants.revoke(
        instance_id="agent_a",
        plugin_name="linear",
        # tool_name=None → revokes plugin-level
        revoked_at_seq=42,
        revoked_by="alex",
    )
    assert affected is True

    assert reg.plugin_grants.get_active("agent_a", "linear") is None
    per = reg.plugin_grants.get_active(
        "agent_a", "linear", tool_name="linear_create_issue.v1",
    )
    assert per is not None and per.is_active


def test_list_active_for_plugin_orders_plugin_level_first(reg: Registry):
    """The dispatcher's T4 resolver wants to scan plugin-level first
    (the fallback), then per-tool (the override). list_active_for_plugin
    returns rows in that order so the resolver can short-circuit."""
    _seed_agent(reg)
    # Insert per-tool first to prove the ordering is by query, not
    # insertion order.
    reg.plugin_grants.grant(
        instance_id="agent_a",
        plugin_name="slack",
        tool_name="slack_send_message.v1",
        trust_tier="green",
        granted_at_seq=50,
    )
    reg.plugin_grants.grant(
        instance_id="agent_a",
        plugin_name="slack",
        tool_name="slack_react.v1",
        trust_tier="yellow",
        granted_at_seq=51,
    )
    reg.plugin_grants.grant(
        instance_id="agent_a",
        plugin_name="slack",
        trust_tier="yellow",
        granted_at_seq=52,
    )

    rows = reg.plugin_grants.list_active_for_plugin("agent_a", "slack")
    assert len(rows) == 3
    # Plugin-level (NULL tool_name) sorts first.
    assert rows[0].is_plugin_level
    # Per-tool rows follow in tool_name alphabetical order.
    assert rows[1].tool_name == "slack_react.v1"
    assert rows[2].tool_name == "slack_send_message.v1"


def test_active_plugin_names_dedupes_when_per_tool_and_plugin_level_coexist(
    reg: Registry,
):
    """active_plugin_names is the dispatcher's cheap allowlist input.
    Per-tool + plugin-level rows on the same plugin should yield ONE
    plugin name, not two — the DISTINCT in the query enforces this."""
    _seed_agent(reg)
    reg.plugin_grants.grant(
        instance_id="agent_a",
        plugin_name="slack",
        trust_tier="yellow",
        granted_at_seq=60,
    )
    reg.plugin_grants.grant(
        instance_id="agent_a",
        plugin_name="slack",
        tool_name="slack_send_message.v1",
        trust_tier="green",
        granted_at_seq=61,
    )
    reg.plugin_grants.grant(
        instance_id="agent_a",
        plugin_name="github",
        tool_name="github_list_issues.v1",
        trust_tier="green",
        granted_at_seq=62,
    )

    names = reg.plugin_grants.active_plugin_names("agent_a")
    assert names == {"slack", "github"}  # not 3 entries — per-tool dedupes


def test_per_tool_grant_idempotent_on_redo(reg: Registry):
    """Re-granting the same triple (INSERT OR REPLACE) overwrites,
    so a duplicate grant at the same (agent, plugin, tool) leaves
    one active row, not two."""
    _seed_agent(reg)
    reg.plugin_grants.grant(
        instance_id="agent_a",
        plugin_name="slack",
        tool_name="slack_send_message.v1",
        trust_tier="yellow",
        granted_at_seq=70,
        granted_by="alex",
    )
    # Re-grant with a different trust_tier — should overwrite.
    reg.plugin_grants.grant(
        instance_id="agent_a",
        plugin_name="slack",
        tool_name="slack_send_message.v1",
        trust_tier="green",
        granted_at_seq=71,
        granted_by="alex",
    )

    rows = reg.plugin_grants.list_active("agent_a")
    assert len(rows) == 1
    assert rows[0].trust_tier == "green"  # newer tier wins
    assert rows[0].granted_at_seq == 71


def test_per_tool_grant_then_revoke_then_regrant_creates_fresh_active(
    reg: Registry,
):
    """Revoking flips the row to historical; re-granting at the
    same triple replaces the historical row with a fresh active
    one (INSERT OR REPLACE semantics). list_active sees only the
    fresh row; list_all still sees the historical revocation
    isn't there (it was overwritten — that's the documented
    semantic on grant()'s docstring)."""
    _seed_agent(reg)
    reg.plugin_grants.grant(
        instance_id="agent_a",
        plugin_name="github",
        tool_name="github_merge_pr.v1",
        trust_tier="red",
        granted_at_seq=80,
    )
    reg.plugin_grants.revoke(
        instance_id="agent_a",
        plugin_name="github",
        tool_name="github_merge_pr.v1",
        revoked_at_seq=81,
    )
    # Active list is empty
    assert reg.plugin_grants.list_active("agent_a") == []

    # Re-grant
    reg.plugin_grants.grant(
        instance_id="agent_a",
        plugin_name="github",
        tool_name="github_merge_pr.v1",
        trust_tier="yellow",
        granted_at_seq=82,
    )
    active = reg.plugin_grants.list_active("agent_a")
    assert len(active) == 1
    assert active[0].is_active
    assert active[0].trust_tier == "yellow"
    assert active[0].granted_at_seq == 82
