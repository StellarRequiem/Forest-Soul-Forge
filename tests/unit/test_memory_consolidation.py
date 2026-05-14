"""ADR-0074 T1 (B294) — memory consolidation substrate tests.

Pins the schema-additive substrate this tranche ships:

* Schema v23 adds three columns on `memory_entries`:
    - `consolidation_state TEXT NOT NULL DEFAULT 'pending'`
      with CHECK over the 5-state enum.
    - `consolidated_into TEXT` self-FK to memory_entries.entry_id.
    - `consolidation_run TEXT`.
* Schema v23 adds two partial indexes:
    - `idx_memory_consolidation_pending` on
      `(consolidation_state, created_at) WHERE state='pending'`.
    - `idx_memory_consolidated_into` on
      `consolidated_into WHERE consolidated_into IS NOT NULL`.
* `KNOWN_EVENT_TYPES` gains three bookend/per-entry event types:
    - `memory_consolidation_run_started`
    - `memory_consolidated`
    - `memory_consolidation_run_completed`

The consolidation runner itself (selector, summarizer, scheduler
hook) is queued for T2-T5; those bursts carry their own tests.
T1 just locks the data model + event vocabulary.
"""
from __future__ import annotations

import sqlite3

import pytest

from forest_soul_forge.core.audit_chain import KNOWN_EVENT_TYPES
from forest_soul_forge.registry.schema import (
    DDL_STATEMENTS,
    MIGRATIONS,
    SCHEMA_VERSION,
)


# ---------------------------------------------------------------------------
# Schema substrate sanity
# ---------------------------------------------------------------------------

def test_schema_version_is_v23():
    """Schema bumped to v23 for the ADR-0074 T1 migration."""
    assert SCHEMA_VERSION == 23


def test_v23_migration_present():
    """MIGRATIONS[23] exists and ships the expected statements.

    Three ALTER TABLE ADD COLUMN statements + two CREATE INDEX statements.
    """
    assert 23 in MIGRATIONS
    stmts = MIGRATIONS[23]
    assert len(stmts) == 5
    joined = "\n".join(stmts)
    assert "ADD COLUMN consolidation_state" in joined
    assert "DEFAULT 'pending'" in joined
    assert "ADD COLUMN consolidated_into" in joined
    assert "REFERENCES memory_entries(entry_id)" in joined
    assert "ADD COLUMN consolidation_run" in joined
    assert "idx_memory_consolidation_pending" in joined
    assert "WHERE consolidation_state = 'pending'" in joined
    assert "idx_memory_consolidated_into" in joined
    assert "WHERE consolidated_into IS NOT NULL" in joined


def test_canonical_ddl_includes_consolidation_substrate():
    """Fresh-DB DDL matches the post-migration shape — no
    fresh-vs-migrated drift."""
    joined = "\n".join(DDL_STATEMENTS)
    assert "consolidation_state TEXT NOT NULL DEFAULT 'pending'" in joined
    assert "consolidated_into" in joined
    assert "consolidation_run" in joined
    assert "idx_memory_consolidation_pending" in joined
    assert "idx_memory_consolidated_into" in joined


# ---------------------------------------------------------------------------
# v23 migration applied to a v22-shaped DB
# ---------------------------------------------------------------------------

def _v22_memory_entries(conn: sqlite3.Connection) -> None:
    """Build the pre-v23 (v21-era) shape of memory_entries with its
    parent `agents` table so the FK references resolve."""
    conn.execute("CREATE TABLE agents (instance_id TEXT PRIMARY KEY)")
    conn.execute(
        """
        CREATE TABLE memory_entries (
            entry_id TEXT PRIMARY KEY,
            instance_id TEXT NOT NULL,
            agent_dna TEXT NOT NULL,
            layer TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT 'private',
            content TEXT NOT NULL,
            content_digest TEXT NOT NULL,
            tags_json TEXT NOT NULL DEFAULT '[]',
            consented_to_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            deleted_at TEXT,
            disclosed_from_entry TEXT,
            disclosed_summary TEXT,
            disclosed_at TEXT,
            claim_type TEXT NOT NULL DEFAULT 'observation',
            confidence TEXT NOT NULL DEFAULT 'medium',
            last_challenged_at TEXT,
            content_encrypted INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (instance_id) REFERENCES agents(instance_id)
        )
        """
    )


def _v23_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    _v22_memory_entries(conn)
    for stmt in MIGRATIONS[23]:
        conn.execute(stmt)
    conn.commit()
    return conn


def test_v23_migration_applies_cleanly_on_v22_table():
    """Running MIGRATIONS[23] against a v22-shaped table adds the
    columns + indexes without disturbing existing rows. Pre-existing
    rows migrate to consolidation_state='pending' (their actual state —
    never consolidated, so pending fits)."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    _v22_memory_entries(conn)
    conn.execute("INSERT INTO agents (instance_id) VALUES ('a1')")
    conn.execute(
        "INSERT INTO memory_entries "
        "(entry_id, instance_id, agent_dna, layer, content, content_digest, "
        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("e_legacy", "a1", "dna_x", "episodic", "obs", "d1g3st",
         "2026-05-13T00:00:00+00:00"),
    )
    conn.commit()

    for stmt in MIGRATIONS[23]:
        conn.execute(stmt)
    conn.commit()

    row = conn.execute(
        "SELECT consolidation_state, consolidated_into, consolidation_run "
        "FROM memory_entries WHERE entry_id = 'e_legacy'"
    ).fetchone()
    assert row == ("pending", None, None)

    # Both indexes registered.
    idxs = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='memory_entries'"
        )
    }
    assert "idx_memory_consolidation_pending" in idxs
    assert "idx_memory_consolidated_into" in idxs


# ---------------------------------------------------------------------------
# CHECK constraint + valid state enum
# ---------------------------------------------------------------------------

def _seed_entry(conn: sqlite3.Connection, entry_id: str, state: str = "pending") -> None:
    """Insert one memory_entry with the given state. Caller must have
    already seeded an `agents` row with instance_id='a1'."""
    conn.execute(
        "INSERT INTO memory_entries "
        "(entry_id, instance_id, agent_dna, layer, content, content_digest, "
        " created_at, consolidation_state) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (entry_id, "a1", "dna_x", "episodic", "content", "digest",
         "2026-05-14T00:00:00+00:00", state),
    )


def test_consolidation_state_check_rejects_garbage():
    """The CHECK constraint blocks any value outside the locked enum."""
    conn = _v23_db()
    conn.execute("INSERT INTO agents (instance_id) VALUES ('a1')")
    with pytest.raises(sqlite3.IntegrityError):
        _seed_entry(conn, "e_bad", state="garbage_state")


def test_all_five_states_accepted():
    """All five enum values from ADR Decision 1 land without error."""
    conn = _v23_db()
    conn.execute("INSERT INTO agents (instance_id) VALUES ('a1')")
    for state in ["pending", "consolidated", "summary", "pinned", "purged"]:
        _seed_entry(conn, f"e_{state}", state=state)
    conn.commit()
    rows = conn.execute(
        "SELECT consolidation_state FROM memory_entries ORDER BY entry_id"
    ).fetchall()
    assert {r[0] for r in rows} == {
        "pending", "consolidated", "summary", "pinned", "purged",
    }


# ---------------------------------------------------------------------------
# Self-FK enforcement on consolidated_into
# ---------------------------------------------------------------------------

def test_consolidated_into_self_fk_accepts_valid_pointer():
    """A child can point at an existing summary entry."""
    conn = _v23_db()
    conn.execute("INSERT INTO agents (instance_id) VALUES ('a1')")
    _seed_entry(conn, "e_summary", state="summary")
    _seed_entry(conn, "e_child", state="pending")
    conn.execute(
        "UPDATE memory_entries SET consolidation_state='consolidated', "
        "consolidated_into='e_summary' WHERE entry_id='e_child'"
    )
    conn.commit()
    row = conn.execute(
        "SELECT consolidation_state, consolidated_into "
        "FROM memory_entries WHERE entry_id='e_child'"
    ).fetchone()
    assert row == ("consolidated", "e_summary")


def test_consolidated_into_self_fk_rejects_dangling_pointer():
    """A child pointing at a non-existent entry_id is refused.

    Self-FK enforcement matters because the T2/T3 runner trusts that
    every consolidated row has a real summary row on the other end of
    the pointer — operator lineage queries walk this graph.
    """
    conn = _v23_db()
    conn.execute("INSERT INTO agents (instance_id) VALUES ('a1')")
    _seed_entry(conn, "e_orphan", state="pending")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE memory_entries SET consolidated_into='nonexistent_id' "
            "WHERE entry_id='e_orphan'"
        )


# ---------------------------------------------------------------------------
# Index presence
# ---------------------------------------------------------------------------

def test_partial_indexes_present_and_partial():
    """Both indexes register and ARE partial — verified by reading
    the index DDL out of sqlite_master."""
    conn = _v23_db()
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='index' AND name LIKE 'idx_memory_consolidat%'"
    ).fetchall()
    names = {r[0] for r in rows}
    assert names == {
        "idx_memory_consolidation_pending",
        "idx_memory_consolidated_into",
    }
    by_name = dict(rows)
    assert "WHERE consolidation_state = 'pending'" in by_name[
        "idx_memory_consolidation_pending"
    ]
    assert "WHERE consolidated_into IS NOT NULL" in by_name[
        "idx_memory_consolidated_into"
    ]


# ---------------------------------------------------------------------------
# Audit event registration
# ---------------------------------------------------------------------------

def test_consolidation_audit_events_registered():
    """All three event types from ADR Decision 4 are in
    KNOWN_EVENT_TYPES so T2-T5 emits don't trip the verifier."""
    assert "memory_consolidation_run_started" in KNOWN_EVENT_TYPES
    assert "memory_consolidated" in KNOWN_EVENT_TYPES
    assert "memory_consolidation_run_completed" in KNOWN_EVENT_TYPES
