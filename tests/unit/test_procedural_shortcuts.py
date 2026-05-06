"""ADR-0054 T1 (Burst 178) — ProceduralShortcutsTable tests.

Coverage:
- v15 → v16 migration creates the table on existing DBs
- Fresh-install path also creates the table (DDL_STATEMENTS parity)
- put / get / list / strengthen / weaken / record_match / delete
- search_by_cosine: cosine + reinforcement gate; combined-score
  ranking; empty-result fall-through; mixed-dimension skip
- Embedding round-trip (float32 BLOB encoding) is byte-stable
- _normalize handles zero-vector without crashing

Tests use the conftest seed_stub_agent helper to satisfy the FK
constraint on memory_procedural_shortcuts.instance_id →
agents.instance_id.
"""
from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import numpy as np
import pytest

from forest_soul_forge.registry import Registry
from tests.unit.conftest import seed_stub_agent
from forest_soul_forge.registry.schema import SCHEMA_VERSION
from forest_soul_forge.registry.tables.procedural_shortcuts import (
    ProceduralShortcut,
    ProceduralShortcutsTable,
    _decode_embedding,
    _encode_embedding,
    _normalize,
)


# ---------------------------------------------------------------------------
# Schema version + table presence
# ---------------------------------------------------------------------------

def test_schema_version_is_16():
    """v16 was introduced by ADR-0054 T1. If a future migration bumps
    it, that landing should add a clear entry in MIGRATIONS[N] +
    update DDL_STATEMENTS — the test itself bumps to track."""
    assert SCHEMA_VERSION == 16


def test_fresh_install_creates_procedural_shortcuts_table(tmp_path: Path):
    """Fresh-install (DDL_STATEMENTS path) must create the new
    table. Catches drift between MIGRATIONS[16] and DDL_STATEMENTS."""
    reg = Registry.bootstrap(tmp_path / "fresh.db")
    try:
        cur = reg._conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='memory_procedural_shortcuts';"
        )
        assert cur.fetchone() is not None
    finally:
        reg.close()


def test_table_has_expected_columns(tmp_path: Path):
    reg = Registry.bootstrap(tmp_path / "cols.db")
    try:
        cur = reg._conn.execute("PRAGMA table_info(memory_procedural_shortcuts);")
        columns = {row[1] for row in cur.fetchall()}
        expected = {
            "shortcut_id", "instance_id", "created_at",
            "last_matched_at", "last_matched_seq",
            "situation_text", "situation_embedding",
            "action_kind", "action_payload",
            "success_count", "failure_count",
            "learned_from_seq", "learned_from_kind",
        }
        assert expected.issubset(columns)
    finally:
        reg.close()


def test_action_kind_check_constraint(tmp_path: Path):
    """Any action_kind outside the documented enum must be rejected
    at SQL level — defense-in-depth in case Python validation gets
    bypassed."""
    reg = Registry.bootstrap(tmp_path / "constraint.db")
    try:
        seed_stub_agent(reg, "agent_a")
        with pytest.raises(sqlite3.IntegrityError):
            reg._conn.execute(
                """
                INSERT INTO memory_procedural_shortcuts (
                    shortcut_id, instance_id, created_at,
                    situation_text, situation_embedding,
                    action_kind, action_payload,
                    learned_from_seq, learned_from_kind
                ) VALUES (
                    'sc-1', 'agent_a', '2026-05-06T00:00:00Z',
                    'hi', X'00000000',
                    'BOGUS', '{}',
                    1, 'auto'
                );
                """
            )
    finally:
        reg.close()


def test_learned_from_kind_check_constraint(tmp_path: Path):
    reg = Registry.bootstrap(tmp_path / "constraint2.db")
    try:
        seed_stub_agent(reg, "agent_a")
        with pytest.raises(sqlite3.IntegrityError):
            reg._conn.execute(
                """
                INSERT INTO memory_procedural_shortcuts (
                    shortcut_id, instance_id, created_at,
                    situation_text, situation_embedding,
                    action_kind, action_payload,
                    learned_from_seq, learned_from_kind
                ) VALUES (
                    'sc-2', 'agent_a', '2026-05-06T00:00:00Z',
                    'hi', X'00000000',
                    'response', '{}',
                    1, 'BOGUS'
                );
                """
            )
    finally:
        reg.close()


def test_fk_to_agents_enforced(tmp_path: Path):
    """FOREIGN KEY (instance_id) → agents(instance_id). Inserting a
    shortcut for an agent that doesn't exist must error."""
    reg = Registry.bootstrap(tmp_path / "fk.db")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            reg._conn.execute(
                """
                INSERT INTO memory_procedural_shortcuts (
                    shortcut_id, instance_id, created_at,
                    situation_text, situation_embedding,
                    action_kind, action_payload,
                    learned_from_seq, learned_from_kind
                ) VALUES (
                    'sc-3', 'nonexistent_agent', '2026-05-06T00:00:00Z',
                    'hi', X'00000000',
                    'response', '{}',
                    1, 'auto'
                );
                """
            )
    finally:
        reg.close()


# ---------------------------------------------------------------------------
# Embedding encoding round-trip
# ---------------------------------------------------------------------------

def test_encode_decode_round_trip():
    arr = np.array([0.1, -0.5, 0.3, 0.0, 1.0], dtype=np.float32)
    blob = _encode_embedding(arr)
    out = _decode_embedding(blob)
    np.testing.assert_array_equal(out, arr)


def test_encode_rejects_2d():
    with pytest.raises(ValueError):
        _encode_embedding(np.zeros((2, 3), dtype=np.float32))


def test_encode_rejects_int():
    with pytest.raises(ValueError):
        _encode_embedding(np.array([1, 2, 3]))


def test_normalize_zero_vector_safe():
    """Zero vector → returned as-is (cosine undefined; the search
    path naturally excludes it)."""
    z = np.zeros(5, dtype=np.float32)
    out = _normalize(z)
    np.testing.assert_array_equal(out, z)


def test_normalize_unit():
    a = np.array([3.0, 4.0], dtype=np.float32)
    out = _normalize(a)
    assert abs(np.linalg.norm(out) - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# put / get / list / strengthen / weaken / record_match / delete
# ---------------------------------------------------------------------------

def _emb(values: list[float]) -> np.ndarray:
    return np.array(values, dtype=np.float32)


def test_put_then_get_round_trip(tmp_path: Path):
    reg = Registry.bootstrap(tmp_path / "putget.db")
    try:
        seed_stub_agent(reg, "agent_a")
        table = ProceduralShortcutsTable(reg._conn)
        sc = table.put(
            shortcut_id="sc-1",
            instance_id="agent_a",
            situation_text="hello",
            situation_embedding=_emb([0.1, 0.2, 0.3]),
            action_kind="response",
            action_payload={"text": "hi back"},
            learned_from_seq=42,
        )
        assert sc.shortcut_id == "sc-1"
        assert sc.instance_id == "agent_a"
        assert sc.situation_text == "hello"
        assert sc.action_kind == "response"
        assert sc.action_payload == {"text": "hi back"}
        assert sc.success_count == 0
        assert sc.failure_count == 0
        assert sc.learned_from_seq == 42
        assert sc.learned_from_kind == "auto"
        np.testing.assert_array_almost_equal(
            sc.situation_embedding, _emb([0.1, 0.2, 0.3])
        )

        again = table.get("sc-1")
        assert again.shortcut_id == sc.shortcut_id
        np.testing.assert_array_almost_equal(
            again.situation_embedding, sc.situation_embedding
        )
    finally:
        reg.close()


def test_get_unknown_raises_key_error(tmp_path: Path):
    reg = Registry.bootstrap(tmp_path / "get-unknown.db")
    try:
        table = ProceduralShortcutsTable(reg._conn)
        with pytest.raises(KeyError):
            table.get("never-set")
    finally:
        reg.close()


def test_put_rejects_bad_action_kind(tmp_path: Path):
    reg = Registry.bootstrap(tmp_path / "bad-kind.db")
    try:
        seed_stub_agent(reg, "agent_a")
        table = ProceduralShortcutsTable(reg._conn)
        with pytest.raises(ValueError):
            table.put(
                shortcut_id="sc-1",
                instance_id="agent_a",
                situation_text="x",
                situation_embedding=_emb([0.1]),
                action_kind="not_a_real_kind",
                action_payload={},
                learned_from_seq=1,
            )
    finally:
        reg.close()


def test_strengthen_weaken_record_match(tmp_path: Path):
    reg = Registry.bootstrap(tmp_path / "swr.db")
    try:
        seed_stub_agent(reg, "agent_a")
        table = ProceduralShortcutsTable(reg._conn)
        table.put(
            shortcut_id="sc-1",
            instance_id="agent_a",
            situation_text="x",
            situation_embedding=_emb([0.1, 0.2]),
            action_kind="response",
            action_payload={"text": "ok"},
            learned_from_seq=1,
        )

        table.strengthen("sc-1")
        table.strengthen("sc-1", by=2)
        table.weaken("sc-1")
        table.record_match("sc-1", at_seq=99)

        sc = table.get("sc-1")
        assert sc.success_count == 3
        assert sc.failure_count == 1
        assert sc.reinforcement_score == 2
        assert sc.last_matched_seq == 99
        assert sc.last_matched_at is not None
    finally:
        reg.close()


def test_strengthen_rejects_zero_or_negative(tmp_path: Path):
    reg = Registry.bootstrap(tmp_path / "sz.db")
    try:
        seed_stub_agent(reg, "agent_a")
        table = ProceduralShortcutsTable(reg._conn)
        table.put(
            shortcut_id="sc-1", instance_id="agent_a",
            situation_text="x", situation_embedding=_emb([0.1]),
            action_kind="response", action_payload={}, learned_from_seq=1,
        )
        with pytest.raises(ValueError):
            table.strengthen("sc-1", by=0)
        with pytest.raises(ValueError):
            table.weaken("sc-1", by=-1)
    finally:
        reg.close()


def test_delete(tmp_path: Path):
    reg = Registry.bootstrap(tmp_path / "del.db")
    try:
        seed_stub_agent(reg, "agent_a")
        table = ProceduralShortcutsTable(reg._conn)
        table.put(
            shortcut_id="sc-1", instance_id="agent_a",
            situation_text="x", situation_embedding=_emb([0.1]),
            action_kind="response", action_payload={}, learned_from_seq=1,
        )
        table.delete("sc-1")
        with pytest.raises(KeyError):
            table.get("sc-1")
        # Idempotent on absent.
        table.delete("sc-1")
    finally:
        reg.close()


def test_list_by_instance_excludes_negative_by_default(
    tmp_path: Path,
):
    reg = Registry.bootstrap(tmp_path / "list.db")
    try:
        seed_stub_agent(reg, "agent_a")
        table = ProceduralShortcutsTable(reg._conn)

        # success > failure → kept by default
        table.put(
            shortcut_id="sc-good", instance_id="agent_a",
            situation_text="x", situation_embedding=_emb([0.1]),
            action_kind="response", action_payload={}, learned_from_seq=1,
        )
        table.strengthen("sc-good", by=3)
        table.weaken("sc-good", by=1)

        # failure > success → soft-deleted from default view
        table.put(
            shortcut_id="sc-bad", instance_id="agent_a",
            situation_text="x", situation_embedding=_emb([0.1]),
            action_kind="response", action_payload={}, learned_from_seq=1,
        )
        table.weaken("sc-bad", by=3)

        defaults = {sc.shortcut_id for sc in table.list_by_instance("agent_a")}
        assert defaults == {"sc-good"}

        full = {
            sc.shortcut_id
            for sc in table.list_by_instance("agent_a", include_negative=True)
        }
        assert full == {"sc-good", "sc-bad"}
    finally:
        reg.close()


def test_count_by_instance(tmp_path: Path):
    reg = Registry.bootstrap(tmp_path / "count.db")
    try:
        seed_stub_agent(reg, "agent_a")
        seed_stub_agent(reg, "agent_b")
        table = ProceduralShortcutsTable(reg._conn)
        for i in range(3):
            table.put(
                shortcut_id=f"a-{i}", instance_id="agent_a",
                situation_text="x", situation_embedding=_emb([0.1]),
                action_kind="response", action_payload={}, learned_from_seq=1,
            )
        for i in range(2):
            table.put(
                shortcut_id=f"b-{i}", instance_id="agent_b",
                situation_text="x", situation_embedding=_emb([0.1]),
                action_kind="response", action_payload={}, learned_from_seq=1,
            )
        assert table.count_by_instance("agent_a") == 3
        assert table.count_by_instance("agent_b") == 2
        assert table.count_by_instance("ghost") == 0
    finally:
        reg.close()


# ---------------------------------------------------------------------------
# search_by_cosine
# ---------------------------------------------------------------------------

def test_search_returns_match_above_threshold(tmp_path: Path):
    reg = Registry.bootstrap(tmp_path / "search.db")
    try:
        seed_stub_agent(reg, "agent_a")
        table = ProceduralShortcutsTable(reg._conn)

        # Stored: roughly along [1, 0]
        stored = _emb([1.0, 0.0])
        table.put(
            shortcut_id="sc-1", instance_id="agent_a",
            situation_text="east-pointing situation",
            situation_embedding=stored,
            action_kind="response", action_payload={"text": "east"},
            learned_from_seq=1,
        )
        # Reinforce so the row passes the reinforcement floor (default 2).
        table.strengthen("sc-1", by=3)

        # Query along the same direction — should match.
        query = _emb([0.999, 0.045])  # cosine ~0.999
        results = table.search_by_cosine("agent_a", query)
        assert len(results) == 1
        match, cos = results[0]
        assert match.shortcut_id == "sc-1"
        assert cos > 0.99
    finally:
        reg.close()


def test_search_excludes_below_cosine_floor(tmp_path: Path):
    reg = Registry.bootstrap(tmp_path / "below.db")
    try:
        seed_stub_agent(reg, "agent_a")
        table = ProceduralShortcutsTable(reg._conn)
        table.put(
            shortcut_id="sc-1", instance_id="agent_a",
            situation_text="east", situation_embedding=_emb([1.0, 0.0]),
            action_kind="response", action_payload={"text": "x"},
            learned_from_seq=1,
        )
        table.strengthen("sc-1", by=5)

        # Orthogonal query — cosine = 0 << 0.92.
        query = _emb([0.0, 1.0])
        assert table.search_by_cosine("agent_a", query) == []
    finally:
        reg.close()


def test_search_excludes_below_reinforcement_floor(
    tmp_path: Path,
):
    """Even with cosine = 1.0, an entry that hasn't been reinforced
    enough times shouldn't match. Conservative defaults — see
    ADR-0054 D2."""
    reg = Registry.bootstrap(tmp_path / "rein.db")
    try:
        seed_stub_agent(reg, "agent_a")
        table = ProceduralShortcutsTable(reg._conn)
        table.put(
            shortcut_id="sc-1", instance_id="agent_a",
            situation_text="x", situation_embedding=_emb([1.0, 0.0]),
            action_kind="response", action_payload={}, learned_from_seq=1,
        )
        # Only 1 success — below the default 2-floor.
        table.strengthen("sc-1", by=1)

        results = table.search_by_cosine(
            "agent_a", _emb([1.0, 0.0]),
        )
        assert results == []

        # Drop the floor + retry → match fires.
        results = table.search_by_cosine(
            "agent_a", _emb([1.0, 0.0]),
            reinforcement_floor=1,
        )
        assert len(results) == 1
    finally:
        reg.close()


def test_search_top_k_ranks_by_combined_score(
    tmp_path: Path,
):
    """Combined score = cosine + 0.05·log(success+1). With two
    near-identical cosine matches, the more-reinforced entry wins."""
    reg = Registry.bootstrap(tmp_path / "topk.db")
    try:
        seed_stub_agent(reg, "agent_a")
        table = ProceduralShortcutsTable(reg._conn)

        table.put(
            shortcut_id="sc-low", instance_id="agent_a",
            situation_text="x", situation_embedding=_emb([1.0, 0.0]),
            action_kind="response", action_payload={"label": "low"},
            learned_from_seq=1,
        )
        table.strengthen("sc-low", by=2)

        table.put(
            shortcut_id="sc-high", instance_id="agent_a",
            situation_text="x", situation_embedding=_emb([1.0, 0.0]),
            action_kind="response", action_payload={"label": "high"},
            learned_from_seq=2,
        )
        table.strengthen("sc-high", by=20)

        results = table.search_by_cosine(
            "agent_a", _emb([1.0, 0.0]), top_k=2,
        )
        assert len(results) == 2
        assert results[0][0].shortcut_id == "sc-high"
        assert results[1][0].shortcut_id == "sc-low"
    finally:
        reg.close()


def test_search_skips_mixed_dimension(tmp_path: Path):
    """A table that ended up with rows of different embedding
    dimensions is a configuration error. The search helper skips
    mismatched rows rather than crashing the whole search."""
    reg = Registry.bootstrap(tmp_path / "mix.db")
    try:
        seed_stub_agent(reg, "agent_a")
        table = ProceduralShortcutsTable(reg._conn)
        table.put(
            shortcut_id="sc-2d", instance_id="agent_a",
            situation_text="2d", situation_embedding=_emb([1.0, 0.0]),
            action_kind="response", action_payload={}, learned_from_seq=1,
        )
        table.strengthen("sc-2d", by=3)

        table.put(
            shortcut_id="sc-3d", instance_id="agent_a",
            situation_text="3d", situation_embedding=_emb([1.0, 0.0, 0.0]),
            action_kind="response", action_payload={}, learned_from_seq=1,
        )
        table.strengthen("sc-3d", by=3)

        # Query is 2-D. The 3-D row should be silently skipped.
        results = table.search_by_cosine("agent_a", _emb([1.0, 0.0]))
        assert len(results) == 1
        assert results[0][0].shortcut_id == "sc-2d"
    finally:
        reg.close()


def test_search_invalid_cosine_floor_raises(tmp_path: Path):
    reg = Registry.bootstrap(tmp_path / "inv.db")
    try:
        table = ProceduralShortcutsTable(reg._conn)
        with pytest.raises(ValueError):
            table.search_by_cosine("x", _emb([0.1]), cosine_floor=1.5)
        with pytest.raises(ValueError):
            table.search_by_cosine("x", _emb([0.1]), top_k=0)
    finally:
        reg.close()


# ---------------------------------------------------------------------------
# v15 → v16 upgrade path
# ---------------------------------------------------------------------------

def test_v15_to_v16_upgrade(tmp_path: Path):
    """Bootstrap a v15-shape DB by hand, reopen via Registry, and
    confirm the migration adds the new table without disturbing
    existing data. Catches drift between MIGRATIONS[16] and
    DDL_STATEMENTS."""
    db = tmp_path / "v15.db"

    # Open a fresh Registry then nuke the new table + schema_version
    # to simulate a v15 install. (Easier than hand-replicating all v15
    # DDL.)
    reg = Registry.bootstrap(db)
    reg._conn.execute("DROP TABLE memory_procedural_shortcuts;")
    reg._conn.execute(
        "UPDATE registry_meta SET value = '15' "
        "WHERE key = 'schema_version';"
    )
    reg._conn.commit()
    reg.close()

    # Reopen — should run MIGRATIONS[16] and re-create the table.
    reg = Registry.bootstrap(db)
    try:
        cur = reg._conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='memory_procedural_shortcuts';"
        )
        assert cur.fetchone() is not None
        assert reg.schema_version() == 16
    finally:
        reg.close()
