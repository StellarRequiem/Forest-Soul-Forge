"""ADR-0050 T2 (B267) — SQLCipher registry encryption tests.

Gated with ``pytest.importorskip('sqlcipher3')`` — the test runs only
when the binding is installed. Sandbox-only test environments
(daemon extras not installed) skip cleanly.

Coverage:
  - Bootstrap with a master_key produces an encrypted DB file
    (raw sqlite3.connect() can't read its schema).
  - Bootstrap without master_key produces the legacy plaintext
    DB (bit-identical pre-T2 behavior).
  - Bootstrap with the WRONG master_key on an existing encrypted
    DB raises RegistryEncryptionError.
  - Bootstrap with master_key on an existing PLAINTEXT DB raises
    RegistryEncryptionError (operator turned encryption on without
    running the T8 migration).
  - sqlcipher3 missing from the import path raises a clear
    RegistryEncryptionError (lazy-import error message points at
    the install command).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

# Hard gate — every test in this file requires sqlcipher3. Without
# the binding installed, all tests in the file skip cleanly.
sqlcipher3 = pytest.importorskip(
    "sqlcipher3",
    reason="sqlcipher3 binding not installed (pip install -e '.[daemon]')",
)

from forest_soul_forge.registry.registry import (
    Registry,
    RegistryEncryptionError,
)


# ---- helpers ----


def _make_master_key(byte: int = 0xAB) -> bytes:
    """Deterministic 32-byte key. NOT cryptographically random —
    tests don't need real entropy, just two distinct values."""
    return bytes([byte]) * 32


# ---- encrypted-mode bootstrap ----


def test_bootstrap_with_key_creates_encrypted_db(tmp_path: Path):
    """When master_key is set, the DB file is encrypted such that
    raw stdlib sqlite3 cannot read its schema. This is the core
    guarantee — file-level disclosure is gated on the key."""
    db = tmp_path / "encrypted.sqlite"
    key = _make_master_key(0xAB)

    with Registry.bootstrap(db, master_key=key) as r:
        # Schema setup ran through SQLCipher path — registry is
        # usable.
        assert r.list_agents() == []

    # Raw stdlib sqlite3 cannot read the file's tables (the
    # sqlite_master magic bytes are encrypted). Either the open
    # fails or the first read fails — both are valid SQLCipher
    # behaviors. Either way, the data isn't recoverable without
    # the key.
    raw = sqlite3.connect(str(db))
    with pytest.raises(sqlite3.DatabaseError):
        raw.execute("SELECT name FROM sqlite_master;").fetchall()
    raw.close()


def test_bootstrap_with_same_key_reopens_db(tmp_path: Path):
    """A daemon restart with the same key must reopen the same DB
    cleanly. This is the operational guarantee — operators don't
    lose their data on restart."""
    db = tmp_path / "encrypted.sqlite"
    key = _make_master_key(0xCD)

    with Registry.bootstrap(db, master_key=key) as r:
        # Write something so we can detect cross-restart visibility.
        # We use registry_meta because it's already populated by
        # bootstrap; just confirm we can read.
        rows = r._conn.execute(
            "SELECT key FROM registry_meta;"
        ).fetchall()
        assert len(rows) > 0

    # Second bootstrap with the same key.
    with Registry.bootstrap(db, master_key=key) as r2:
        rows2 = r2._conn.execute(
            "SELECT key FROM registry_meta;"
        ).fetchall()
        assert len(rows2) == len(rows)


def test_bootstrap_with_wrong_key_raises(tmp_path: Path):
    """Operator rotates the master key (or it gets corrupted)
    without re-keying the DB — the encrypted file can't be
    decrypted with the new key. Refuse with a clean error;
    silent failure here would mean a daemon that boots but
    can't read its own state."""
    db = tmp_path / "encrypted.sqlite"
    correct_key = _make_master_key(0xAB)
    wrong_key = _make_master_key(0xCD)

    with Registry.bootstrap(db, master_key=correct_key) as r:
        # Ensure the file is properly initialized + closed before
        # we try to reopen.
        assert r.list_agents() == []

    with pytest.raises(RegistryEncryptionError):
        with Registry.bootstrap(db, master_key=wrong_key) as r:
            r.list_agents()


def test_bootstrap_with_key_on_plaintext_db_raises(tmp_path: Path):
    """Operator turns on FSF_AT_REST_ENCRYPTION without running the
    T8 migration — the existing plaintext DB cannot be opened
    with a key. RegistryEncryptionError gives the operator a
    clear signal that they need the migration tool, rather than
    silently re-creating the DB or corrupting it."""
    db = tmp_path / "plaintext.sqlite"
    # Bootstrap WITHOUT a key first (pre-T2 behavior).
    with Registry.bootstrap(db) as r:
        assert r.list_agents() == []

    # Try to reopen WITH a key — must refuse.
    key = _make_master_key(0xAB)
    with pytest.raises(RegistryEncryptionError):
        with Registry.bootstrap(db, master_key=key) as r:
            r.list_agents()


# ---- plaintext-mode bootstrap (no regression check) ----


def test_bootstrap_without_key_is_bit_identical_pre_T2(tmp_path: Path):
    """Bootstrap with master_key=None must produce a plaintext DB
    that stdlib sqlite3 can read directly — the pre-T2 path is
    completely untouched."""
    db = tmp_path / "plaintext.sqlite"
    with Registry.bootstrap(db) as r:
        assert r.list_agents() == []

    # Raw sqlite3 reads its schema fine because the file's plain.
    raw = sqlite3.connect(str(db))
    rows = raw.execute(
        "SELECT name FROM sqlite_master WHERE type='table';"
    ).fetchall()
    raw.close()
    # Schema must contain at least the registry_meta table.
    names = {r[0] for r in rows}
    assert "registry_meta" in names


# ---- import-time error path ----


class TestSqlcipherImportError:
    """RegistryEncryptionError is raised with a clear install hint
    when sqlcipher3 is missing from the path AT CONNECT TIME (lazy
    import inside _get). We simulate by monkey-patching the
    import."""

    def test_missing_sqlcipher_raises_with_install_hint(
        self, tmp_path: Path, monkeypatch,
    ):
        # Hide sqlcipher3 from the import system. The lazy import
        # inside _ThreadLocalConn._get re-imports it on first
        # connection; this patch makes that import fail.
        import sys
        # Save real module so we can restore for other tests.
        original = sys.modules.pop("sqlcipher3", None)
        original_dbapi = sys.modules.pop("sqlcipher3.dbapi2", None)
        # Block re-import by inserting an entry that raises.
        import importlib

        def _broken_import(name, *args, **kwargs):
            if name == "sqlcipher3.dbapi2" or name.startswith("sqlcipher3"):
                raise ImportError(
                    "simulated: sqlcipher3 not installed",
                )
            return importlib.__import__(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", _broken_import)

        db = tmp_path / "encrypted.sqlite"
        key = _make_master_key(0xAB)
        try:
            with pytest.raises(RegistryEncryptionError) as ei:
                # Bootstrap triggers a connection on schema install,
                # which is where the lazy import fires.
                Registry.bootstrap(db, master_key=key)
            assert "sqlcipher3" in str(ei.value).lower()
            assert "install" in str(ei.value).lower()
        finally:
            # Restore for other tests.
            if original is not None:
                sys.modules["sqlcipher3"] = original
            if original_dbapi is not None:
                sys.modules["sqlcipher3.dbapi2"] = original_dbapi
