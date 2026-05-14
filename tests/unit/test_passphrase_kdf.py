"""ADR-0050 T6 (B273) — passphrase KDF + master-key passphrase-backend
tests.

Covers:
  - Scrypt KDF determinism (same passphrase + salt → same key)
  - Salt persistence (round-trip + tamper detection)
  - Master-key resolver passphrase backend:
      * FSF_MASTER_PASSPHRASE env supply path
      * non-interactive without env → clean refusal
      * cache shared with other backends (single MASTER_KEY_NAME slot)

What this does NOT cover (queued for integration tests):
  - Interactive getpass.getpass() prompt — hard to exercise in
    unit tests without a TTY harness; manual smoke is the path.
  - End-to-end daemon boot under FSF_MASTER_KEY_BACKEND=passphrase
    (covered by the lifespan smoke when T7 ships).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from forest_soul_forge.security.master_key import (
    FSF_MASTER_KEY_BACKEND_ENV,
    FSF_MASTER_PASSPHRASE_ENV,
    MASTER_KEY_LENGTH_BYTES,
    configured_backend_name,
    reset_cache,
    resolve_master_key,
)
from forest_soul_forge.security.passphrase_kdf import (
    PassphraseKDFError,
    default_salt_path,
    derive_key_from_passphrase,
    load_or_create_salt,
)


@pytest.fixture(autouse=True)
def _isolate_cache():
    """Each test starts with a clean master-key cache + env baseline."""
    reset_cache()
    saved_env = {
        k: os.environ.get(k)
        for k in (FSF_MASTER_KEY_BACKEND_ENV, FSF_MASTER_PASSPHRASE_ENV)
    }
    yield
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    reset_cache()


# ---------------------------------------------------------------------------
# KDF surface
# ---------------------------------------------------------------------------
def test_derive_key_is_deterministic():
    salt = b"\x11" * 16
    k1 = derive_key_from_passphrase("correct horse battery staple", salt)
    k2 = derive_key_from_passphrase("correct horse battery staple", salt)
    assert k1 == k2
    assert len(k1) == MASTER_KEY_LENGTH_BYTES


def test_derive_key_changes_with_passphrase():
    salt = b"\x22" * 16
    k1 = derive_key_from_passphrase("alpha", salt)
    k2 = derive_key_from_passphrase("alpha ", salt)  # trailing space
    assert k1 != k2


def test_derive_key_changes_with_salt():
    pw = "swordfish"
    k1 = derive_key_from_passphrase(pw, b"\x33" * 16)
    k2 = derive_key_from_passphrase(pw, b"\x44" * 16)
    assert k1 != k2


def test_derive_key_rejects_empty_passphrase():
    salt = b"\x55" * 16
    with pytest.raises(PassphraseKDFError, match="non-empty"):
        derive_key_from_passphrase("", salt)
    with pytest.raises(PassphraseKDFError, match="non-empty"):
        derive_key_from_passphrase("    ", salt)


def test_derive_key_rejects_wrong_salt_length():
    with pytest.raises(PassphraseKDFError, match="16 bytes"):
        derive_key_from_passphrase("ok", b"\x66" * 8)
    with pytest.raises(PassphraseKDFError, match="16 bytes"):
        derive_key_from_passphrase("ok", b"\x66" * 32)


# ---------------------------------------------------------------------------
# Salt persistence
# ---------------------------------------------------------------------------
def test_load_or_create_salt_generates_on_first_boot(tmp_path):
    p = tmp_path / "salt"
    assert not p.exists()
    salt = load_or_create_salt(p)
    assert p.exists()
    assert len(salt) == 16
    assert p.read_bytes() == salt


def test_load_or_create_salt_round_trips(tmp_path):
    p = tmp_path / "salt"
    s1 = load_or_create_salt(p)
    s2 = load_or_create_salt(p)
    assert s1 == s2


def test_load_or_create_salt_rejects_corrupted(tmp_path):
    """Truncated/tampered salt file → explicit error rather than
    silently regenerating (which would orphan encrypted data)."""
    p = tmp_path / "salt"
    p.write_bytes(b"\x00" * 8)  # wrong length
    with pytest.raises(PassphraseKDFError, match="wrong length"):
        load_or_create_salt(p)


def test_default_salt_path_under_data_dir(tmp_path):
    assert default_salt_path(tmp_path) == tmp_path / "master_salt"


# ---------------------------------------------------------------------------
# Master-key resolver — passphrase backend
# ---------------------------------------------------------------------------
def test_configured_backend_passphrase(monkeypatch):
    monkeypatch.setenv(FSF_MASTER_KEY_BACKEND_ENV, "passphrase")
    assert configured_backend_name() == "passphrase"


def test_resolve_master_key_passphrase_via_env(tmp_path, monkeypatch):
    """Non-interactive supply via FSF_MASTER_PASSPHRASE produces a
    deterministic 32-byte key keyed by the persisted salt."""
    monkeypatch.setenv(FSF_MASTER_KEY_BACKEND_ENV, "passphrase")
    monkeypatch.setenv(FSF_MASTER_PASSPHRASE_ENV, "pw-via-env")
    key1 = resolve_master_key(data_dir=tmp_path)
    assert isinstance(key1, bytes)
    assert len(key1) == MASTER_KEY_LENGTH_BYTES

    # Second call from a cold cache against the SAME tmp_path
    # (same salt on disk) + SAME env passphrase → identical key.
    reset_cache()
    key2 = resolve_master_key(data_dir=tmp_path)
    assert key1 == key2


def test_resolve_master_key_passphrase_changes_with_passphrase(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv(FSF_MASTER_KEY_BACKEND_ENV, "passphrase")
    monkeypatch.setenv(FSF_MASTER_PASSPHRASE_ENV, "first")
    k_first = resolve_master_key(data_dir=tmp_path)

    reset_cache()
    monkeypatch.setenv(FSF_MASTER_PASSPHRASE_ENV, "second")
    k_second = resolve_master_key(data_dir=tmp_path)
    assert k_first != k_second


def test_resolve_master_key_passphrase_non_interactive_refuses(
    tmp_path, monkeypatch,
):
    """No env passphrase + no TTY → clean refusal (rather than hang
    or silently downgrade to a different backend)."""
    monkeypatch.setenv(FSF_MASTER_KEY_BACKEND_ENV, "passphrase")
    monkeypatch.delenv(FSF_MASTER_PASSPHRASE_ENV, raising=False)

    # Force isatty() to False on stdin. pytest typically captures
    # stdin so this is the natural state, but guard explicitly.
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)

    with pytest.raises(RuntimeError, match="not a TTY"):
        resolve_master_key(data_dir=tmp_path)


def test_resolve_master_key_hsm_raises_not_implemented(tmp_path, monkeypatch):
    """HSM backend is reserved for ADR-0050 T16; must raise rather
    than silently route to a different backend."""
    monkeypatch.setenv(FSF_MASTER_KEY_BACKEND_ENV, "hsm")
    with pytest.raises(NotImplementedError, match="T16"):
        resolve_master_key(data_dir=tmp_path)
