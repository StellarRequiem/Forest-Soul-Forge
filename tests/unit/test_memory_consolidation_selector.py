"""ADR-0074 T2 (B302) — ConsolidationSelector tests.

Pure-function tests against ``select_consolidation_candidates``.
The selector is a SQL query over the B294 schema-v23 columns;
tests build an in-memory SQLite, apply migrations[23], seed rows
with controlled (age, layer, claim_type, state, deleted) tuples,
and verify the filter + ordering + batch-size cap.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from forest_soul_forge.core.memory_consolidation import (
    ConsolidationPolicy,
    select_consolidation_candidates,
)
from forest_soul_forge.registry.schema import MIGRATIONS


NOW = datetime(2026, 5, 14, tzinfo=timezone.utc)


def _fresh_db() -> sqlite3.Connection:
    """Build an in-memory SQLite at schema v23 with a single
    pre-seeded agent 'a1'."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("CREATE TABLE agents (instance_id TEXT PRIMARY KEY)")
    # Pre-v23 shape of memory_entries (we apply MIGRATIONS[23]
    # below to bring it forward).
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
    conn.execute("INSERT INTO agents (instance_id) VALUES ('a1')")
    return conn


def _insert(
    conn: sqlite3.Connection,
    entry_id: str,
    *,
    age_days: int = 20,
    layer: str = "episodic",
    claim_type: str = "observation",
    state: str = "pending",
    deleted: bool = False,
) -> None:
    """Seed one row with the chosen filter inputs. created_at is
    derived from `age_days` relative to NOW so tests stay
    deterministic."""
    created = (NOW - timedelta(days=age_days)).isoformat()
    deleted_at = (NOW - timedelta(days=1)).isoformat() if deleted else None
    conn.execute(
        "INSERT INTO memory_entries ("
        "  entry_id, instance_id, agent_dna, layer, content, "
        "  content_digest, created_at, deleted_at, claim_type, "
        "  consolidation_state"
        ") VALUES (?, 'a1', 'dna', ?, 'c', 'd', ?, ?, ?, ?)",
        (entry_id, layer, created, deleted_at, claim_type, state),
    )


# ---------------------------------------------------------------------------
# Empty / happy path
# ---------------------------------------------------------------------------

def test_empty_db_returns_empty_list():
    conn = _fresh_db()
    assert select_consolidation_candidates(
        conn, policy=ConsolidationPolicy(), now=NOW,
    ) == []


def test_aged_episodic_observation_is_eligible():
    conn = _fresh_db()
    _insert(conn, "e_old", age_days=20)
    assert select_consolidation_candidates(
        conn, policy=ConsolidationPolicy(), now=NOW,
    ) == ["e_old"]


# ---------------------------------------------------------------------------
# Filter gates
# ---------------------------------------------------------------------------

def test_young_entries_below_min_age_filtered():
    """Default min_age_days=14; a 5-day-old entry is excluded."""
    conn = _fresh_db()
    _insert(conn, "e_young", age_days=5)
    assert select_consolidation_candidates(
        conn, policy=ConsolidationPolicy(), now=NOW,
    ) == []


def test_min_age_zero_includes_brand_new_entries():
    """Setting min_age_days=0 means anything pending is eligible —
    use case is operator forcing a one-off pass after a backfill."""
    conn = _fresh_db()
    _insert(conn, "e_now", age_days=0)
    out = select_consolidation_candidates(
        conn, policy=ConsolidationPolicy(min_age_days=0), now=NOW,
    )
    # The created_at-strict-less-than-cutoff filter still requires
    # the row to be strictly older than `now`, so age=0 (exactly NOW)
    # is excluded. age_days=1 with min_age_days=0 would pass.
    assert out == []
    # Confirm the 1-day case works.
    _insert(conn, "e_1d", age_days=1)
    out = select_consolidation_candidates(
        conn, policy=ConsolidationPolicy(min_age_days=0), now=NOW,
    )
    assert out == ["e_1d"]


def test_non_eligible_layers_filtered():
    conn = _fresh_db()
    _insert(conn, "e_working", age_days=30, layer="working")
    assert select_consolidation_candidates(
        conn, policy=ConsolidationPolicy(), now=NOW,
    ) == []


def test_custom_eligible_layers_honored():
    """Operator widens the policy to include working-layer entries."""
    conn = _fresh_db()
    _insert(conn, "e_working", age_days=30, layer="working")
    out = select_consolidation_candidates(
        conn,
        policy=ConsolidationPolicy(
            eligible_layers=("episodic", "working"),
        ),
        now=NOW,
    )
    assert out == ["e_working"]


def test_non_eligible_claim_types_filtered():
    """Default policy excludes promise + preference + agent_inference."""
    conn = _fresh_db()
    for ct in ("promise", "preference", "agent_inference", "external_fact"):
        _insert(conn, f"e_{ct}", age_days=30, claim_type=ct)
    assert select_consolidation_candidates(
        conn, policy=ConsolidationPolicy(), now=NOW,
    ) == []


def test_non_pending_states_all_filtered():
    """consolidated / summary / pinned / purged states all excluded —
    only 'pending' rows are eligible for the next pass."""
    conn = _fresh_db()
    for state in ("consolidated", "summary", "pinned", "purged"):
        _insert(conn, f"e_{state}", age_days=30, state=state)
    assert select_consolidation_candidates(
        conn, policy=ConsolidationPolicy(), now=NOW,
    ) == []


def test_deleted_entries_filtered():
    """ADR-0022 deleted_at marker excludes the row even when it's
    pending. The runner doesn't fold tombstones."""
    conn = _fresh_db()
    _insert(conn, "e_dead", age_days=30, deleted=True)
    assert select_consolidation_candidates(
        conn, policy=ConsolidationPolicy(), now=NOW,
    ) == []


# ---------------------------------------------------------------------------
# Ordering + batch-size
# ---------------------------------------------------------------------------

def test_returns_oldest_first():
    """ASC created_at — FIFO. Operators expect first-in-first-folded."""
    conn = _fresh_db()
    _insert(conn, "e_30d", age_days=30)
    _insert(conn, "e_25d", age_days=25)
    _insert(conn, "e_60d", age_days=60)
    out = select_consolidation_candidates(
        conn, policy=ConsolidationPolicy(), now=NOW,
    )
    assert out == ["e_60d", "e_30d", "e_25d"]


def test_batch_size_cap_honored():
    """max_batch_size caps the result length even when more rows
    are eligible. Picks the oldest N."""
    conn = _fresh_db()
    for i in range(10):
        _insert(conn, f"e_{i:02}", age_days=30 + i)
    out = select_consolidation_candidates(
        conn,
        policy=ConsolidationPolicy(max_batch_size=3),
        now=NOW,
    )
    assert len(out) == 3
    # Oldest 3: i=9 (39d), i=8 (38d), i=7 (37d).
    assert out == ["e_09", "e_08", "e_07"]


# ---------------------------------------------------------------------------
# Policy validation
# ---------------------------------------------------------------------------

def test_policy_rejects_negative_min_age():
    with pytest.raises(ValueError, match="min_age_days"):
        ConsolidationPolicy(min_age_days=-1)


def test_policy_rejects_zero_batch_size():
    with pytest.raises(ValueError, match="max_batch_size"):
        ConsolidationPolicy(max_batch_size=0)


def test_policy_rejects_empty_eligible_layers():
    with pytest.raises(ValueError, match="eligible_layers"):
        ConsolidationPolicy(eligible_layers=())


def test_policy_rejects_empty_eligible_claim_types():
    with pytest.raises(ValueError, match="eligible_claim_types"):
        ConsolidationPolicy(eligible_claim_types=())


def test_policy_defaults_match_adr_specification():
    """Pin the ADR-0074 T2 defaults at the construction site so a
    refactor that changes them becomes a visible diff."""
    p = ConsolidationPolicy()
    assert p.min_age_days == 14
    assert p.max_batch_size == 200
    assert p.eligible_layers == ("episodic",)
    assert p.eligible_claim_types == ("observation", "user_statement")
