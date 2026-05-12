"""ADR-0049 T1 (Burst 242) — AgentKeyStore wrapper tests.

Covers the ADR-0049 KeyStore surface as implemented in
``src/forest_soul_forge/security/keys/agent_key_store.py``.

Strategy: every test wires the wrapper over a FileStore-backed
SecretStore pointed at a tmpdir. That exercises the real
SecretStoreProtocol contract (including chmod-600 + YAML
round-trip) so the wrapper integration is verified end-to-end —
not just unit-faked.
"""
from __future__ import annotations

import secrets
from pathlib import Path

import pytest

from forest_soul_forge.security.keys import (
    AgentKeyNotFoundError,
    AgentKeyStore,
    AgentKeyStoreError,
    resolve_agent_key_store,
)
from forest_soul_forge.security.keys.agent_key_store import SECRET_NAME_PREFIX
from forest_soul_forge.security.secrets import FileStore


# ---- helpers ---------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> AgentKeyStore:
    """Wrapper over a FileStore pointed at a tmpdir — every test
    gets a clean keystore."""
    backend = FileStore(path=tmp_path / "secrets.yaml")
    return AgentKeyStore(secret_store=backend)


def _random_key() -> bytes:
    """Stand-in for an ed25519 private key — 32 random bytes.
    Real ed25519 keys are also 32 bytes; the wrapper doesn't care
    what's in them, so random bytes exercise the same path."""
    return secrets.token_bytes(32)


# ---- store / fetch round-trip ---------------------------------------------


def test_store_then_fetch_returns_exact_bytes(store):
    """The bytes that come out match the bytes that went in —
    base64 encoding is transparent to the caller."""
    key = _random_key()
    store.store("agent_a", key)
    assert store.fetch("agent_a") == key


def test_fetch_missing_returns_none(store):
    """A never-stored agent returns None; no raise."""
    assert store.fetch("agent_never") is None


def test_fetch_strict_missing_raises(store):
    """fetch_strict is the must-have-a-key path — raises
    AgentKeyNotFoundError on absence."""
    with pytest.raises(AgentKeyNotFoundError) as exc_info:
        store.fetch_strict("agent_never")
    assert "agent_never" in str(exc_info.value)


def test_fetch_strict_returns_bytes(store):
    """The happy path on fetch_strict returns the exact bytes
    (same contract as fetch, just non-None enforced)."""
    key = _random_key()
    store.store("agent_a", key)
    assert store.fetch_strict("agent_a") == key


# ---- delete ---------------------------------------------------------------


def test_delete_existing_returns_true(store):
    store.store("agent_a", _random_key())
    assert store.delete("agent_a") is True
    assert store.fetch("agent_a") is None


def test_delete_missing_returns_false(store):
    """Idempotent on absence — no raise, just False."""
    assert store.delete("agent_never") is False


def test_delete_then_re_store_works(store):
    key1, key2 = _random_key(), _random_key()
    store.store("agent_a", key1)
    store.delete("agent_a")
    store.store("agent_a", key2)
    assert store.fetch("agent_a") == key2


# ---- overwrite + idempotency ----------------------------------------------


def test_store_overwrite_yields_new_value(store):
    """Re-storing replaces. The ADR-0049 birth pipeline calls
    store exactly once per agent, but the wrapper's contract is
    that overwrite is allowed (it's the backend's contract;
    enforcing single-write would be a separate policy layer)."""
    key1, key2 = _random_key(), _random_key()
    store.store("agent_a", key1)
    store.store("agent_a", key2)
    assert store.fetch("agent_a") == key2


def test_store_idempotent_with_same_value(store):
    """Storing the same bytes twice doesn't error and doesn't
    change observable state."""
    key = _random_key()
    store.store("agent_a", key)
    store.store("agent_a", key)
    assert store.fetch("agent_a") == key


# ---- multi-agent ---------------------------------------------------------


def test_multiple_agents_isolated(store):
    """Each agent's key is independent — fetching one doesn't
    leak the other's."""
    key_a, key_b = _random_key(), _random_key()
    store.store("agent_a", key_a)
    store.store("agent_b", key_b)
    assert store.fetch("agent_a") == key_a
    assert store.fetch("agent_b") == key_b
    assert key_a != key_b  # sanity


def test_list_agent_ids_returns_stored_agents(store):
    """list_agent_ids enumerates every agent with a stored key,
    sorted for stable display."""
    store.store("agent_b", _random_key())
    store.store("agent_a", _random_key())
    store.store("agent_c", _random_key())
    assert store.list_agent_ids() == ["agent_a", "agent_b", "agent_c"]


def test_list_agent_ids_skips_non_agent_secrets(store, tmp_path: Path):
    """Other secrets in the same backend (plugin secrets, e.g.)
    don't pollute the agent-key list. Tests the namespace prefix
    filter."""
    backend = FileStore(path=tmp_path / "secrets.yaml")
    s = AgentKeyStore(secret_store=backend)
    s.store("agent_a", _random_key())
    # Drop a plugin-style secret directly via the backend.
    backend.put("plugin:github:token", "pretend-this-is-a-token")
    backend.put("plugin:slack:webhook", "https://example.invalid/hook")
    # Only the agent key surfaces.
    assert s.list_agent_ids() == ["agent_a"]


def test_list_agent_ids_empty_when_none_stored(store):
    assert store.list_agent_ids() == []


# ---- error paths -----------------------------------------------------------


def test_store_rejects_non_bytes(store):
    """Passing a string instead of bytes is a programmer error;
    raise rather than silently encode-then-decode to mismatched
    bytes."""
    with pytest.raises(AgentKeyStoreError):
        store.store("agent_a", "not-bytes")  # type: ignore[arg-type]


def test_store_rejects_empty_instance_id(store):
    """Empty/non-str instance_id raises early — would otherwise
    produce a malformed secret name."""
    with pytest.raises(AgentKeyStoreError):
        store.store("", _random_key())


def test_store_rejects_instance_id_with_colon(store):
    """The colon is the namespace delimiter in the secret name;
    refusing it in instance_id keeps the inverse mapping
    unambiguous."""
    with pytest.raises(AgentKeyStoreError):
        store.store("agent:has:colons", _random_key())


def test_fetch_surfaces_base64_corruption_as_error(store, tmp_path: Path):
    """If the backend stored value isn't valid base64 (tampering /
    operator hand-edit), surface as AgentKeyStoreError rather
    than silently returning None."""
    backend = FileStore(path=tmp_path / "secrets.yaml")
    backend.put(SECRET_NAME_PREFIX + "agent_a", "not!base64!!!")
    s = AgentKeyStore(secret_store=backend)
    with pytest.raises(AgentKeyStoreError) as exc_info:
        s.fetch("agent_a")
    assert "valid base64" in str(exc_info.value)


# ---- factory --------------------------------------------------------------


def test_resolve_with_explicit_backend_bypasses_cache(tmp_path: Path):
    """Passing an explicit secret_store to the resolver yields a
    fresh AgentKeyStore — used by tests that want isolation from
    the process-wide default."""
    b1 = FileStore(path=tmp_path / "s1.yaml")
    b2 = FileStore(path=tmp_path / "s2.yaml")
    s1 = resolve_agent_key_store(secret_store=b1)
    s2 = resolve_agent_key_store(secret_store=b2)
    # Stores are different objects + write to different files.
    assert s1 is not s2
    s1.store("agent_a", _random_key())
    # s2 sees nothing — proves isolation.
    assert s2.fetch("agent_a") is None


# ---- secret-name shape ----------------------------------------------------


def test_stored_key_lands_under_expected_prefix(store, tmp_path: Path):
    """Lock the on-disk secret-name format. External tools (the
    `fsf secret list` CLI, macOS Keychain Access) rely on the
    prefix to display the agent-key namespace; changing it is a
    backwards-incompatible migration."""
    backend = FileStore(path=tmp_path / "secrets.yaml")
    s = AgentKeyStore(secret_store=backend)
    s.store("agent_a", _random_key())
    names = backend.list_names()
    assert SECRET_NAME_PREFIX + "agent_a" in names
