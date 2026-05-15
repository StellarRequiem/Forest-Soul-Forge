"""ADR-0074 T5 (B308) — fsf memory pin/unpin CLI tests.

Each test builds a fresh in-memory SQLite at schema v23 with one
seed entry, exercises the CLI through argparse, and verifies
state transitions + rc codes match the spec.
"""
from __future__ import annotations

import argparse
import io
import sqlite3
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from forest_soul_forge.cli.memory_cmd import add_subparser
from forest_soul_forge.registry.schema import MIGRATIONS


@pytest.fixture
def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="fsf")
    sub = root.add_subparsers(dest="cmd")
    add_subparser(sub)
    return root


def _build_db(path: Path, *, entry_state: str = "pending") -> None:
    """Create a v23 registry on disk with one seed memory_entry."""
    conn = sqlite3.connect(str(path))
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
    conn.execute("INSERT INTO agents (instance_id) VALUES ('a1')")
    conn.execute(
        "INSERT INTO memory_entries ("
        "  entry_id, instance_id, agent_dna, layer, content, "
        "  content_digest, created_at, consolidation_state"
        ") VALUES ('e_test', 'a1', 'dna', 'episodic', 'c', 'd', "
        "'2026-05-01T00:00:00+00:00', ?)",
        (entry_state,),
    )
    conn.commit()
    conn.close()


def _read_state(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute(
            "SELECT consolidation_state FROM memory_entries "
            "WHERE entry_id='e_test'"
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------

def test_pin_flips_pending_to_pinned(tmp_path, parser):
    db = tmp_path / "registry.sqlite"
    _build_db(db, entry_state="pending")

    args = parser.parse_args([
        "memory", "pin", "e_test", "--registry-path", str(db),
    ])
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = args._run(args)
    assert rc == 0
    assert "ok:" in buf.getvalue()
    assert _read_state(db) == "pinned"


def test_unpin_flips_pinned_to_pending(tmp_path, parser):
    db = tmp_path / "registry.sqlite"
    _build_db(db, entry_state="pinned")

    args = parser.parse_args([
        "memory", "unpin", "e_test", "--registry-path", str(db),
    ])
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = args._run(args)
    assert rc == 0
    assert _read_state(db) == "pending"


# ---------------------------------------------------------------------------
# Refusal paths
# ---------------------------------------------------------------------------

def test_pin_refuses_already_pinned(tmp_path, parser):
    """A re-pin doesn't error harmlessly — it refuses with rc=2 so
    operators see "this row isn't in a flippable state" instead of
    a silent no-op."""
    db = tmp_path / "registry.sqlite"
    _build_db(db, entry_state="pinned")

    args = parser.parse_args([
        "memory", "pin", "e_test", "--registry-path", str(db),
    ])
    err = io.StringIO()
    with redirect_stderr(err):
        rc = args._run(args)
    assert rc == 2
    assert "refuse to flip" in err.getvalue()
    assert _read_state(db) == "pinned"  # no change


def test_pin_refuses_consolidated_entry(tmp_path, parser):
    """Pinning a consolidated row would corrupt lineage (the
    summary doesn't know to update). Refuse."""
    db = tmp_path / "registry.sqlite"
    _build_db(db, entry_state="consolidated")

    args = parser.parse_args([
        "memory", "pin", "e_test", "--registry-path", str(db),
    ])
    err = io.StringIO()
    with redirect_stderr(err):
        rc = args._run(args)
    assert rc == 2
    assert _read_state(db) == "consolidated"


def test_unpin_refuses_pending_entry(tmp_path, parser):
    """Unpinning a non-pinned row is a no-op state-machine-wise;
    refuse with rc=2 rather than silently succeed."""
    db = tmp_path / "registry.sqlite"
    _build_db(db, entry_state="pending")

    args = parser.parse_args([
        "memory", "unpin", "e_test", "--registry-path", str(db),
    ])
    err = io.StringIO()
    with redirect_stderr(err):
        rc = args._run(args)
    assert rc == 2
    assert _read_state(db) == "pending"


def test_pin_refuses_unknown_entry_id(tmp_path, parser):
    db = tmp_path / "registry.sqlite"
    _build_db(db)
    args = parser.parse_args([
        "memory", "pin", "no_such_id", "--registry-path", str(db),
    ])
    err = io.StringIO()
    with redirect_stderr(err):
        rc = args._run(args)
    assert rc == 2
    assert "no memory entry" in err.getvalue()


def test_pin_refuses_missing_registry(tmp_path, parser):
    args = parser.parse_args([
        "memory", "pin", "e_test",
        "--registry-path", str(tmp_path / "does_not_exist.sqlite"),
    ])
    err = io.StringIO()
    with redirect_stderr(err):
        rc = args._run(args)
    assert rc == 2
    assert "not found" in err.getvalue()


# ---------------------------------------------------------------------------
# Subparser registration
# ---------------------------------------------------------------------------

def test_add_subparser_registers_pin_and_unpin(parser):
    """Both subcommands must be addressable + carry runner refs."""
    args_pin = parser.parse_args([
        "memory", "pin", "x",
    ])
    assert hasattr(args_pin, "_run")
    args_unpin = parser.parse_args([
        "memory", "unpin", "x",
    ])
    assert hasattr(args_unpin, "_run")
