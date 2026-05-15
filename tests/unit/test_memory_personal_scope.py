"""ADR-0068 T3 (B313) — personal memory scope tests.

Covers:
  - SCOPES + RECALL_MODES + PERSONAL_SCOPE_ALLOWED_GENRES constants
  - Write-side allow-list enforcement (only listed genres can
    write scope='personal'; others raise MemoryScopeViolation)
  - Read-side semantics for mode='personal' (returns only
    personal-scope rows across instance boundaries, doesn't
    layer on top of the reader's private/lineage surface)
  - Other modes (private/lineage/consented) untouched by the
    personal-scope addition
"""
from __future__ import annotations

import sqlite3

import pytest

from forest_soul_forge.core.memory import Memory, MemoryScopeViolation
from forest_soul_forge.core.memory._helpers import (
    PERSONAL_SCOPE_ALLOWED_GENRES,
    RECALL_MODES,
    SCOPES,
)
from forest_soul_forge.registry.schema import MIGRATIONS


def _fresh_db() -> sqlite3.Connection:
    """In-memory v23 SQLite with three pre-seeded agents."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
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
            content_encrypted INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    for stmt in MIGRATIONS[23]:
        conn.execute(stmt)
    for inst in ("comp_1", "asst_1", "obs_1"):
        conn.execute("INSERT INTO agents (instance_id) VALUES (?)", (inst,))
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

def test_personal_is_in_scopes():
    assert "personal" in SCOPES


def test_personal_is_in_recall_modes():
    assert "personal" in RECALL_MODES


def test_personal_scope_allowed_genres_includes_canonical_four():
    """The default allow-list covers companion, assistant,
    operator_steward, and domain_orchestrator. Anything else
    is refused at write time."""
    assert PERSONAL_SCOPE_ALLOWED_GENRES == frozenset({
        "companion",
        "assistant",
        "operator_steward",
        "domain_orchestrator",
    })


# ---------------------------------------------------------------------------
# Write-side allow-list
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("allowed_genre", sorted(PERSONAL_SCOPE_ALLOWED_GENRES))
def test_allowed_genre_can_write_personal_scope(allowed_genre):
    conn = _fresh_db()
    mem = Memory(conn=conn)
    entry = mem.append(
        instance_id="comp_1", agent_dna="dna",
        content="some operator fact",
        layer="episodic", scope="personal",
        genre=allowed_genre,
    )
    assert entry.scope == "personal"


@pytest.mark.parametrize("forbidden_genre", [
    "observer", "investigator", "researcher", "communicator",
])
def test_forbidden_genre_refused_for_personal_scope(forbidden_genre):
    conn = _fresh_db()
    mem = Memory(conn=conn)
    with pytest.raises(MemoryScopeViolation, match="scope 'personal'"):
        mem.append(
            instance_id="obs_1", agent_dna="dna",
            content="should fail",
            layer="episodic", scope="personal",
            genre=forbidden_genre,
        )


def test_unknown_genre_refused_for_personal_scope():
    """A genre not in the allow-list AND not in GENRE_CEILINGS
    still refuses personal writes — the allow-list is exhaustive."""
    conn = _fresh_db()
    mem = Memory(conn=conn)
    with pytest.raises(MemoryScopeViolation):
        mem.append(
            instance_id="obs_1", agent_dna="dna",
            content="x", layer="episodic", scope="personal",
            genre="some-custom-genre",
        )


# ---------------------------------------------------------------------------
# Read-side semantics for mode='personal'
# ---------------------------------------------------------------------------

def test_personal_mode_returns_all_personal_scope_rows():
    """mode='personal' surfaces personal-scope rows across instance
    boundaries — operator-context isn't agent-private."""
    conn = _fresh_db()
    mem = Memory(conn=conn)
    mem.append(instance_id="comp_1", agent_dna="d", content="A",
               layer="episodic", scope="personal", genre="companion")
    mem.append(instance_id="asst_1", agent_dna="d", content="B",
               layer="episodic", scope="personal", genre="assistant")

    rows = mem.recall_visible_to(
        reader_instance_id="comp_1", lineage_chain=[], mode="personal",
    )
    contents = sorted(r.content for r in rows)
    assert contents == ["A", "B"]
    assert all(r.scope == "personal" for r in rows)


def test_personal_mode_excludes_readers_private_entries():
    """The reader's own private entries DON'T bleed into personal mode.
    Personal is non-additive — the operator-context view, not the
    reader's view stacked with operator-context."""
    conn = _fresh_db()
    mem = Memory(conn=conn)
    mem.append(instance_id="comp_1", agent_dna="d", content="personal_row",
               layer="episodic", scope="personal", genre="companion")
    mem.append(instance_id="comp_1", agent_dna="d", content="private_row",
               layer="episodic", scope="private", genre="companion")

    personal = mem.recall_visible_to(
        reader_instance_id="comp_1", lineage_chain=[], mode="personal",
    )
    private = mem.recall_visible_to(
        reader_instance_id="comp_1", lineage_chain=[], mode="private",
    )
    assert [r.content for r in personal] == ["personal_row"]
    assert [r.content for r in private] == ["private_row"]


def test_lineage_mode_does_not_see_personal_rows():
    """Even though _SCOPE_RANK['personal']=4 is numerically wider
    than lineage=1, lineage mode does NOT pick up personal rows.
    The ceiling-rank ladder doesn't apply to personal — scope
    enums are filtered by literal value, not by rank."""
    conn = _fresh_db()
    mem = Memory(conn=conn)
    mem.append(instance_id="comp_1", agent_dna="d", content="personal_only",
               layer="episodic", scope="personal", genre="companion")

    rows = mem.recall_visible_to(
        reader_instance_id="comp_1", lineage_chain=["comp_1"], mode="lineage",
    )
    # Lineage mode returns reader's private + lineage entries; we
    # wrote a personal-scope row, so lineage should see zero.
    assert rows == []


def test_consented_mode_does_not_see_personal_rows():
    """Same orthogonality for consented mode."""
    conn = _fresh_db()
    mem = Memory(conn=conn)
    mem.append(instance_id="comp_1", agent_dna="d", content="personal_only",
               layer="episodic", scope="personal", genre="companion")

    rows = mem.recall_visible_to(
        reader_instance_id="comp_1", lineage_chain=[], mode="consented",
    )
    assert rows == []


def test_personal_rows_carry_personal_scope_in_db():
    """Sanity: the write actually persists scope='personal' on disk
    so a separate Memory instance reading the same DB sees it."""
    conn = _fresh_db()
    mem = Memory(conn=conn)
    entry = mem.append(
        instance_id="comp_1", agent_dna="d", content="ground truth",
        layer="episodic", scope="personal", genre="companion",
    )
    row = conn.execute(
        "SELECT scope FROM memory_entries WHERE entry_id = ?",
        (entry.entry_id,),
    ).fetchone()
    assert row["scope"] == "personal"
