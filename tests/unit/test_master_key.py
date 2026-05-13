"""ADR-0050 T1 (Burst 266) — at-rest encryption master key tests.

The master key is the root of the at-rest encryption substrate
(T2 SQLCipher, T3 audit chain per-event encryption, T4 memory body
encryption all consume it). Tests verify: first-call generates +
stores; subsequent calls return the same key; explicit-store path
bypasses the cache; the reserved namespace doesn't collide with
agent keys or the operator key; persistence across simulated daemon
restarts; backend selection via FSF_MASTER_KEY_BACKEND env var.
"""
from __future__ import annotations

import base64
from pathlib import Path

import pytest

from forest_soul_forge.security.keys import AgentKeyStore
from forest_soul_forge.security.master_key import (
    FSF_MASTER_KEY_BACKEND_ENV,
    MASTER_KEY_LENGTH_BYTES,
    MASTER_KEY_NAME,
    configured_backend_name,
    reset_cache,
    resolve_master_key,
)
from forest_soul_forge.security.secrets import FileStore


@pytest.fixture
def fresh_store(tmp_path: Path) -> AgentKeyStore:
    """Tmpdir-backed AgentKeyStore — every test gets a clean slate.
    Also resets the process-wide cache so the explicit-store
    paths don't accidentally see a previous test's key."""
    reset_cache()
    backend = FileStore(path=tmp_path / "secrets.yaml")
    return AgentKeyStore(secret_store=backend)


# ---- first-call generation ----------------------------------------------


def test_first_call_generates_32_byte_key(fresh_store):
    """No key stored yet → resolve_master_key generates one,
    persists it, and returns 32 random bytes."""
    key = resolve_master_key(key_store=fresh_store)
    assert isinstance(key, bytes)
    assert len(key) == 32 == MASTER_KEY_LENGTH_BYTES


def test_generated_key_is_random(fresh_store, tmp_path):
    """Two separate stores (different tmpdir paths) produce
    cryptographically random + distinct keys. If they happen to
    match, our CSPRNG is broken."""
    backend_a = FileStore(path=tmp_path / "a.yaml")
    backend_b = FileStore(path=tmp_path / "b.yaml")
    key_a = resolve_master_key(key_store=AgentKeyStore(secret_store=backend_a))
    key_b = resolve_master_key(key_store=AgentKeyStore(secret_store=backend_b))
    assert key_a != key_b


# ---- second-call idempotency --------------------------------------------


def test_second_call_returns_same_key(fresh_store):
    """Once generated, the key is sticky — subsequent calls fetch
    from the store (or cache) and return the same bytes."""
    key1 = resolve_master_key(key_store=fresh_store)
    key2 = resolve_master_key(key_store=fresh_store)
    assert key1 == key2


# ---- namespace isolation ------------------------------------------------


def test_master_key_doesnt_pollute_agent_list(fresh_store):
    """The reserved master-key name uses a different prefix than
    agent keys. AgentKeyStore.list_agent_ids must not surface the
    master key — that would confuse the signing/verify wiring
    which assumes list_agent_ids enumerates agents only."""
    fresh_store.store("agent_a", b"a" * 32)
    resolve_master_key(key_store=fresh_store)
    agent_ids = fresh_store.list_agent_ids()
    assert agent_ids == ["agent_a"]


def test_reserved_name_format():
    """Lock the reserved name shape — backends store under this
    exact string. Changing it is a backwards-incompatible
    migration that orphans every encrypted-data deployment."""
    assert MASTER_KEY_NAME == "forest_master_key:default"


def test_master_key_namespace_distinct_from_operator(fresh_store):
    """The master key and the operator key share the AgentKeyStore
    backend but use different reserved prefixes
    (forest_master_key: vs forest_operator_master:). Verify both
    can coexist without collision."""
    from forest_soul_forge.security.operator_key import (
        OPERATOR_KEY_NAME,
        resolve_operator_keypair,
        reset_cache as reset_operator_cache,
    )

    reset_operator_cache()
    master_key = resolve_master_key(key_store=fresh_store)
    op_priv, _ = resolve_operator_keypair(key_store=fresh_store)
    # Two distinct secrets in the same backend, distinct lookup keys.
    assert master_key != op_priv
    backend = fresh_store._backend  # type: ignore[attr-defined]
    assert backend.get(MASTER_KEY_NAME) is not None
    assert backend.get(OPERATOR_KEY_NAME) is not None


# ---- explicit-store bypasses cache --------------------------------------


def test_explicit_store_isolates_from_default_cache(tmp_path: Path):
    """Passing an explicit key_store bypasses the process cache —
    used by tests and by the lifespan's startup path. Two stores
    produce two distinct master keys."""
    reset_cache()
    backend_a = FileStore(path=tmp_path / "a.yaml")
    backend_b = FileStore(path=tmp_path / "b.yaml")
    store_a = AgentKeyStore(secret_store=backend_a)
    store_b = AgentKeyStore(secret_store=backend_b)
    key_a = resolve_master_key(key_store=store_a)
    key_b = resolve_master_key(key_store=store_b)
    assert key_a != key_b


# ---- persistence across "process restarts" ------------------------------


def test_key_persists_across_fresh_resolves(tmp_path: Path):
    """Simulates a daemon restart: first resolve generates +
    stores; second resolve (after cache reset) reads from the
    persisted store and returns the same bytes. The encrypted
    data only survives if the key survives — this is the core
    operational guarantee."""
    backend = FileStore(path=tmp_path / "secrets.yaml")
    store1 = AgentKeyStore(secret_store=backend)
    key1 = resolve_master_key(key_store=store1)

    reset_cache()
    store2 = AgentKeyStore(secret_store=backend)
    key2 = resolve_master_key(key_store=store2)
    assert key1 == key2


# ---- corruption / bad backend payload handling --------------------------


def test_malformed_backend_payload_raises(tmp_path: Path):
    """A corrupted backend payload (non-base64 garbage) must raise
    a clear RuntimeError, NOT silently regenerate a fresh key
    (which would lose all data encrypted under the old one)."""
    reset_cache()
    backend = FileStore(path=tmp_path / "secrets.yaml")
    # Inject malformed data directly into the backend, bypassing
    # the master-key writer's base64 envelope.
    backend.put(MASTER_KEY_NAME, "!!!not-base64!!!")

    store = AgentKeyStore(secret_store=backend)
    with pytest.raises(RuntimeError) as ei:
        resolve_master_key(key_store=store)
    assert "malformed" in str(ei.value).lower()


def test_wrong_length_backend_payload_raises(tmp_path: Path):
    """If a previous Forest version (or future incompatible one)
    wrote a key with a different length, refuse rather than
    truncate or pad. Loud failure is better than silent
    misuse."""
    reset_cache()
    backend = FileStore(path=tmp_path / "secrets.yaml")
    # Valid base64, but only 16 bytes (AES-128 length) instead of 32.
    short_payload = base64.b64encode(b"x" * 16).decode("ascii")
    backend.put(MASTER_KEY_NAME, short_payload)

    store = AgentKeyStore(secret_store=backend)
    with pytest.raises(RuntimeError) as ei:
        resolve_master_key(key_store=store)
    assert "length" in str(ei.value).lower()


# ---- backend-selection env-var routing ---------------------------------


class TestConfiguredBackendName:
    """FSF_MASTER_KEY_BACKEND env-var routing. T1 implements the
    lookup + platform default; T6 (passphrase) and T16 (HSM) wire
    those backends. T1's tests verify the env-var contract and
    platform default — the actual backend wiring is exercised
    indirectly via the fresh_store fixture (which uses FileStore)."""

    def test_explicit_keychain_via_env(self, monkeypatch):
        monkeypatch.setenv(FSF_MASTER_KEY_BACKEND_ENV, "keychain")
        assert configured_backend_name() == "keychain"

    def test_explicit_file_via_env(self, monkeypatch):
        monkeypatch.setenv(FSF_MASTER_KEY_BACKEND_ENV, "file")
        assert configured_backend_name() == "file"

    def test_explicit_passphrase_via_env(self, monkeypatch):
        """T1 reports the configured backend but does NOT wire
        passphrase. configured_backend_name returning 'passphrase'
        is the operator-visible signal that they've requested an
        unimplemented backend; T6 actually plumbs it."""
        monkeypatch.setenv(FSF_MASTER_KEY_BACKEND_ENV, "passphrase")
        assert configured_backend_name() == "passphrase"

    def test_unknown_value_falls_back_to_platform_default(self, monkeypatch):
        """A typo or unrecognized value must NOT silently route
        through 'file' on darwin (which would create a less-secure
        key location than the operator intended). Fall back to the
        platform default."""
        import sys
        monkeypatch.setenv(FSF_MASTER_KEY_BACKEND_ENV, "ULTRA_VAULT")
        expected = "keychain" if sys.platform == "darwin" else "file"
        assert configured_backend_name() == expected

    def test_unset_returns_platform_default(self, monkeypatch):
        import sys
        monkeypatch.delenv(FSF_MASTER_KEY_BACKEND_ENV, raising=False)
        expected = "keychain" if sys.platform == "darwin" else "file"
        assert configured_backend_name() == expected
