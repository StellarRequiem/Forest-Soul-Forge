"""ADR-0076 T5 (B323) — fsf index CLI tests.

Coverage:
  status:
    - missing registry → rc=2 + stderr message
    - empty registry → "0 entries eligible" + rc=0
    - populated registry shows layer breakdown
    - encrypted rows are skipped from the eligible count

  rebuild:
    - missing registry → rc=2
    - empty registry → "nothing to rebuild" + rc=0
    - --dry-run skips embedder load + shows sample
    - real rebuild populates the index + rc=0
    - encrypted rows are skipped from rebuild
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import pytest

from forest_soul_forge.cli.index_cmd import (
    _load_personal_entries,
    add_subparser,
)


def _make_parser():
    root = argparse.ArgumentParser(prog="fsf")
    sub = root.add_subparsers(dest="cmd")
    add_subparser(sub)
    return root


def _seed_registry(path: Path, entries: list[dict]) -> None:
    """Build a minimal memory_entries table + insert the given
    rows. We don't need the full schema — _load_personal_entries
    only reads (entry_id, content, layer, tags_json,
    content_encrypted)."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("""
            CREATE TABLE memory_entries (
                entry_id TEXT PRIMARY KEY,
                content TEXT,
                layer TEXT,
                scope TEXT,
                tags_json TEXT,
                content_encrypted INTEGER NOT NULL DEFAULT 0,
                deleted_at TEXT
            );
        """)
        for e in entries:
            conn.execute(
                "INSERT INTO memory_entries (entry_id, content, layer, "
                "scope, tags_json, content_encrypted, deleted_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?);",
                (
                    e["entry_id"], e["content"], e["layer"], e["scope"],
                    json.dumps(e.get("tags", [])),
                    e.get("content_encrypted", 0),
                    e.get("deleted_at"),
                ),
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# _load_personal_entries unit
# ---------------------------------------------------------------------------


def test_load_skips_non_personal_scope(tmp_path):
    db = tmp_path / "r.db"
    _seed_registry(db, [
        {"entry_id": "e1", "content": "private notes", "layer": "episodic",
         "scope": "private", "tags": []},
        {"entry_id": "e2", "content": "operator coffee", "layer": "episodic",
         "scope": "personal", "tags": ["habit"]},
    ])
    out = _load_personal_entries(db)
    assert len(out) == 1
    assert out[0][0] == "e2"
    assert out[0][1] == "operator coffee"
    assert out[0][2] == "episodic"
    assert out[0][3] == ("habit",)


def test_load_skips_deleted_entries(tmp_path):
    db = tmp_path / "r.db"
    _seed_registry(db, [
        {"entry_id": "e_active", "content": "x", "layer": "episodic",
         "scope": "personal", "tags": []},
        {"entry_id": "e_deleted", "content": "y", "layer": "episodic",
         "scope": "personal", "tags": [],
         "deleted_at": "2026-05-15T00:00:00Z"},
    ])
    out = _load_personal_entries(db)
    assert {row[0] for row in out} == {"e_active"}


def test_load_skips_encrypted_rows(tmp_path):
    """Encrypted rows need the daemon-resident master key to
    decrypt. The CLI is the offline plaintext path; skip them."""
    db = tmp_path / "r.db"
    _seed_registry(db, [
        {"entry_id": "e_plain", "content": "plain", "layer": "episodic",
         "scope": "personal", "tags": []},
        {"entry_id": "e_enc", "content": "ciphertext", "layer": "episodic",
         "scope": "personal", "tags": [], "content_encrypted": 1},
    ])
    out = _load_personal_entries(db)
    assert {row[0] for row in out} == {"e_plain"}


# ---------------------------------------------------------------------------
# status subcommand
# ---------------------------------------------------------------------------


def test_status_refuses_missing_registry(tmp_path, capsys):
    parser = _make_parser()
    args = parser.parse_args([
        "index", "status",
        "--registry-path", str(tmp_path / "nope.db"),
    ])
    rc = args._run(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "registry DB not found" in err


def test_status_empty_registry(tmp_path, capsys):
    db = tmp_path / "r.db"
    _seed_registry(db, [])
    parser = _make_parser()
    args = parser.parse_args([
        "index", "status", "--registry-path", str(db),
    ])
    rc = args._run(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "0" in out


def test_status_shows_layer_breakdown(tmp_path, capsys):
    db = tmp_path / "r.db"
    _seed_registry(db, [
        {"entry_id": "e1", "content": "x", "layer": "episodic",
         "scope": "personal", "tags": []},
        {"entry_id": "e2", "content": "y", "layer": "episodic",
         "scope": "personal", "tags": []},
        {"entry_id": "e3", "content": "z", "layer": "semantic",
         "scope": "personal", "tags": []},
    ])
    parser = _make_parser()
    args = parser.parse_args([
        "index", "status", "--registry-path", str(db),
    ])
    rc = args._run(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "3" in out
    assert "episodic" in out and "2" in out
    assert "semantic" in out and "1" in out


# ---------------------------------------------------------------------------
# rebuild subcommand
# ---------------------------------------------------------------------------


def test_rebuild_refuses_missing_registry(tmp_path):
    parser = _make_parser()
    args = parser.parse_args([
        "index", "rebuild",
        "--registry-path", str(tmp_path / "nope.db"),
    ])
    rc = args._run(args)
    assert rc == 2


def test_rebuild_empty_registry_is_clean_noop(tmp_path, capsys):
    db = tmp_path / "r.db"
    _seed_registry(db, [])
    parser = _make_parser()
    args = parser.parse_args([
        "index", "rebuild", "--registry-path", str(db),
    ])
    rc = args._run(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "nothing to rebuild" in out


def test_dry_run_skips_embedder_load(tmp_path, capsys):
    """--dry-run path must NOT import sentence-transformers
    (PersonalIndex's embedder). Operators use dry-run to size
    the rebuild before paying the cold-load cost."""
    db = tmp_path / "r.db"
    _seed_registry(db, [
        {"entry_id": "e1", "content": "alpha beta", "layer": "episodic",
         "scope": "personal", "tags": []},
    ])
    # If PersonalIndex were instantiated, the no-deps sandbox would
    # raise PersonalIndexError on first add. Dry-run must avoid that.
    parser = _make_parser()
    args = parser.parse_args([
        "index", "rebuild", "--registry-path", str(db), "--dry-run",
    ])
    rc = args._run(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "e1" in out


def test_real_rebuild_populates_index(tmp_path, capsys, monkeypatch):
    """The real rebuild path imports PersonalIndex. We monkeypatch
    its embedder so the test doesn't need sentence-transformers."""
    import hashlib
    from forest_soul_forge.core import personal_index as pi_module

    class _Mock:
        dimensions = 8
        def embed(self, text):
            h = hashlib.sha256(text.encode("utf-8")).digest()
            return [(b - 128) / 128.0 for b in h[:8]]
        def embed_batch(self, texts):
            return [self.embed(t) for t in texts]

    original_init = pi_module.PersonalIndex.__init__
    def _patched_init(self, embedder=None):
        original_init(self, embedder=_Mock())
    monkeypatch.setattr(pi_module.PersonalIndex, "__init__", _patched_init)

    db = tmp_path / "r.db"
    _seed_registry(db, [
        {"entry_id": "e1", "content": "alpha beta gamma", "layer": "episodic",
         "scope": "personal", "tags": ["habit"]},
        {"entry_id": "e2", "content": "delta epsilon", "layer": "semantic",
         "scope": "personal", "tags": []},
    ])
    parser = _make_parser()
    args = parser.parse_args([
        "index", "rebuild", "--registry-path", str(db),
    ])
    rc = args._run(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "indexed=2" in out
    assert "failed=0" in out


def test_real_rebuild_skips_encrypted_rows(tmp_path, capsys, monkeypatch):
    """The encrypted row should not be counted in the indexed
    tally."""
    import hashlib
    from forest_soul_forge.core import personal_index as pi_module

    class _Mock:
        dimensions = 8
        def embed(self, text):
            h = hashlib.sha256(text.encode("utf-8")).digest()
            return [(b - 128) / 128.0 for b in h[:8]]
        def embed_batch(self, texts):
            return [self.embed(t) for t in texts]

    original_init = pi_module.PersonalIndex.__init__
    def _patched_init(self, embedder=None):
        original_init(self, embedder=_Mock())
    monkeypatch.setattr(pi_module.PersonalIndex, "__init__", _patched_init)

    db = tmp_path / "r.db"
    _seed_registry(db, [
        {"entry_id": "plain", "content": "plain text", "layer": "episodic",
         "scope": "personal", "tags": []},
        {"entry_id": "enc", "content": "ciphertext", "layer": "episodic",
         "scope": "personal", "tags": [], "content_encrypted": 1},
    ])
    parser = _make_parser()
    args = parser.parse_args([
        "index", "rebuild", "--registry-path", str(db),
    ])
    rc = args._run(args)
    assert rc == 0
    out = capsys.readouterr().out
    # Only 1 entry should be indexed (the plaintext one).
    assert "indexed=1" in out
