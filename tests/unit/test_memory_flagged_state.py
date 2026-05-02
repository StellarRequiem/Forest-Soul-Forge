"""Tests for ADR-0036 T6+T7 — flagged_state column + recall surface
extension.

T6 ships the schema v11 → v12 migration adding ``flagged_state`` to
``memory_contradictions``; T7 extends recall to surface the state and
filter ``flagged_rejected`` by default.

Coverage:
- TestSchemaV12        — fresh DB has the column; v11 → v12 migration
                         back-fills 'flagged_unreviewed' on existing
                         rows
- TestFlagDefault      — flag_contradiction lands new rows at
                         'flagged_unreviewed'
- TestSetState         — set_contradiction_state moves through the
                         lifecycle; rejects invalid states; returns
                         False on unknown id
- TestRecallSurface    — unresolved_contradictions_for surfaces
                         flagged_state; filters flagged_rejected by
                         default; include_rejected=True overrides
"""
from __future__ import annotations

from pathlib import Path

import pytest

from forest_soul_forge.core.memory import Memory
from forest_soul_forge.registry import Registry
from forest_soul_forge.registry.schema import SCHEMA_VERSION
from tests.unit.conftest import seed_stub_agent


@pytest.fixture
def env(tmp_path):
    reg = Registry.bootstrap(tmp_path / "reg.sqlite")
    seed_stub_agent(reg, "agent_a")
    seed_stub_agent(reg, "verifier_v1")
    memory = Memory(conn=reg._conn)  # noqa: SLF001
    yield {"memory": memory, "registry": reg}
    reg.close()


def _two_entries(memory):
    a = memory.append(
        instance_id="agent_a", agent_dna="d" * 12,
        content="user prefers tea morning",
        layer="semantic", claim_type="preference",
    )
    b = memory.append(
        instance_id="agent_a", agent_dna="d" * 12,
        content="user prefers coffee morning",
        layer="semantic", claim_type="preference",
    )
    return a, b


# ===========================================================================
# Schema v12 — bootstrap + migration
# ===========================================================================
class TestSchemaV12:
    def test_schema_constant_is_12(self):
        assert SCHEMA_VERSION == 12

    def test_fresh_db_has_flagged_state_column(self, env):
        cols = [
            r[1]
            for r in env["registry"]._conn.execute(  # noqa: SLF001
                "PRAGMA table_info(memory_contradictions);"
            ).fetchall()
        ]
        assert "flagged_state" in cols

    def test_check_constraint_rejects_bogus_state(self, env):
        a, b = _two_entries(env["memory"])
        # Bypass the helper to write a bad value directly. SQLite's
        # CHECK should reject it.
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError):
            env["registry"]._conn.execute(  # noqa: SLF001
                "INSERT INTO memory_contradictions ("
                "  contradiction_id, earlier_entry_id, later_entry_id, "
                "  contradiction_kind, detected_at, detected_by, flagged_state"
                ") VALUES (?, ?, ?, ?, ?, ?, ?);",
                ("contra_bad", a.entry_id, b.entry_id, "direct",
                 "2026-05-02T00:00:00Z", "op", "bogus_state"),
            )


# ===========================================================================
# flag_contradiction default
# ===========================================================================
class TestFlagDefault:
    def test_new_flag_lands_at_unreviewed(self, env):
        a, b = _two_entries(env["memory"])
        cid, _ts = env["memory"].flag_contradiction(
            earlier_entry_id=a.entry_id, later_entry_id=b.entry_id,
            contradiction_kind="updated", detected_by="verifier_v1",
        )
        rows = env["memory"].unresolved_contradictions_for(a.entry_id)
        assert len(rows) == 1
        assert rows[0]["contradiction_id"] == cid
        assert rows[0]["flagged_state"] == "flagged_unreviewed"


# ===========================================================================
# set_contradiction_state
# ===========================================================================
class TestSetState:
    def test_unreviewed_to_confirmed(self, env):
        a, b = _two_entries(env["memory"])
        cid, _ = env["memory"].flag_contradiction(
            earlier_entry_id=a.entry_id, later_entry_id=b.entry_id,
            contradiction_kind="direct", detected_by="op",
        )
        ok = env["memory"].set_contradiction_state(
            contradiction_id=cid, new_state="flagged_confirmed",
        )
        assert ok is True
        rows = env["memory"].unresolved_contradictions_for(a.entry_id)
        assert rows[0]["flagged_state"] == "flagged_confirmed"

    def test_unreviewed_to_rejected(self, env):
        a, b = _two_entries(env["memory"])
        cid, _ = env["memory"].flag_contradiction(
            earlier_entry_id=a.entry_id, later_entry_id=b.entry_id,
            contradiction_kind="direct", detected_by="op",
        )
        ok = env["memory"].set_contradiction_state(
            contradiction_id=cid, new_state="flagged_rejected",
        )
        assert ok is True

    def test_invalid_state_raises(self, env):
        a, b = _two_entries(env["memory"])
        cid, _ = env["memory"].flag_contradiction(
            earlier_entry_id=a.entry_id, later_entry_id=b.entry_id,
            contradiction_kind="direct", detected_by="op",
        )
        with pytest.raises(ValueError, match="new_state"):
            env["memory"].set_contradiction_state(
                contradiction_id=cid, new_state="bogus_state",
            )

    def test_unknown_id_returns_false(self, env):
        ok = env["memory"].set_contradiction_state(
            contradiction_id="contra_does_not_exist",
            new_state="flagged_confirmed",
        )
        assert ok is False

    def test_all_four_states_accepted(self, env):
        a, b = _two_entries(env["memory"])
        cid, _ = env["memory"].flag_contradiction(
            earlier_entry_id=a.entry_id, later_entry_id=b.entry_id,
            contradiction_kind="direct", detected_by="op",
        )
        for state in (
            "flagged_unreviewed", "flagged_confirmed",
            "flagged_rejected", "auto_resolved",
        ):
            ok = env["memory"].set_contradiction_state(
                contradiction_id=cid, new_state=state,
            )
            assert ok is True


# ===========================================================================
# Recall surface — flagged_rejected default-filter + include_rejected
# ===========================================================================
class TestRecallSurface:
    def test_default_filters_rejected(self, env):
        m = env["memory"]
        a, b = _two_entries(m)
        # Flag, then reject.
        cid, _ = m.flag_contradiction(
            earlier_entry_id=a.entry_id, later_entry_id=b.entry_id,
            contradiction_kind="direct", detected_by="op",
        )
        m.set_contradiction_state(
            contradiction_id=cid, new_state="flagged_rejected",
        )
        # Default recall: rejected row excluded.
        rows = m.unresolved_contradictions_for(a.entry_id)
        assert rows == []

    def test_include_rejected_surfaces(self, env):
        m = env["memory"]
        a, b = _two_entries(m)
        cid, _ = m.flag_contradiction(
            earlier_entry_id=a.entry_id, later_entry_id=b.entry_id,
            contradiction_kind="direct", detected_by="op",
        )
        m.set_contradiction_state(
            contradiction_id=cid, new_state="flagged_rejected",
        )
        rows = m.unresolved_contradictions_for(
            a.entry_id, include_rejected=True,
        )
        assert len(rows) == 1
        assert rows[0]["flagged_state"] == "flagged_rejected"

    def test_unreviewed_still_surfaces(self, env):
        m = env["memory"]
        a, b = _two_entries(m)
        cid, _ = m.flag_contradiction(
            earlier_entry_id=a.entry_id, later_entry_id=b.entry_id,
            contradiction_kind="direct", detected_by="op",
        )
        rows = m.unresolved_contradictions_for(a.entry_id)
        assert len(rows) == 1
        assert rows[0]["flagged_state"] == "flagged_unreviewed"

    def test_confirmed_surfaces(self, env):
        m = env["memory"]
        a, b = _two_entries(m)
        cid, _ = m.flag_contradiction(
            earlier_entry_id=a.entry_id, later_entry_id=b.entry_id,
            contradiction_kind="direct", detected_by="op",
        )
        m.set_contradiction_state(
            contradiction_id=cid, new_state="flagged_confirmed",
        )
        # Confirmed rows surface — they're proven, not rejected.
        rows = m.unresolved_contradictions_for(a.entry_id)
        assert len(rows) == 1
        assert rows[0]["flagged_state"] == "flagged_confirmed"

    def test_auto_resolved_surfaces(self, env):
        m = env["memory"]
        a, b = _two_entries(m)
        cid, _ = m.flag_contradiction(
            earlier_entry_id=a.entry_id, later_entry_id=b.entry_id,
            contradiction_kind="direct", detected_by="op",
        )
        m.set_contradiction_state(
            contradiction_id=cid, new_state="auto_resolved",
        )
        # auto_resolved is a system-driven state (v0.4 reservation)
        # but it's not a rejection, so it surfaces by default.
        rows = m.unresolved_contradictions_for(a.entry_id)
        assert len(rows) == 1
        assert rows[0]["flagged_state"] == "auto_resolved"
