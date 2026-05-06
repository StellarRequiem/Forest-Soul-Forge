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

def test_schema_version_is_16():
    """Locked at v16 post-Burst 178 (ADR-0054 T1, memory_procedural_shortcuts).
    Function name moved through v14 (Burst 113a, agent_plugin_grants)
    → v15 (Burst 114, agents.posture) → v16 (Burst 178,
    memory_procedural_shortcuts). Renamed each time for honesty."""
    assert REGISTRY_SCHEMA_VERSION == 16


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
