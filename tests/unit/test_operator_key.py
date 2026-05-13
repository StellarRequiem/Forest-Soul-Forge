"""ADR-0061 T1 (Burst 246) — operator master keypair tests.

The operator master is the root of the passport trust chain.
Tests verify: first-call generates + stores; subsequent calls
return the same keypair; explicit-store path bypasses the cache;
the reserved namespace doesn't collide with per-agent keys.
"""
from __future__ import annotations

import base64
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

from forest_soul_forge.security.keys import AgentKeyStore
from forest_soul_forge.security.operator_key import (
    OPERATOR_KEY_NAME,
    resolve_operator_keypair,
    reset_cache,
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


def test_first_call_generates_keypair(fresh_store):
    """No keypair stored yet → resolve_operator_keypair generates
    one + persists it + returns (priv, pub_b64)."""
    priv, pub_b64 = resolve_operator_keypair(key_store=fresh_store)
    assert isinstance(priv, bytes) and len(priv) == 32
    assert isinstance(pub_b64, str)
    pub_bytes = base64.b64decode(pub_b64.encode("ascii"), validate=True)
    assert len(pub_bytes) == 32


def test_generated_pub_matches_priv(fresh_store):
    """The returned pub_b64 must be the actual public key
    derived from the returned priv bytes. If they drift, every
    downstream passport mint produces unverifiable passports."""
    priv, pub_b64 = resolve_operator_keypair(key_store=fresh_store)
    priv_obj = Ed25519PrivateKey.from_private_bytes(priv)
    derived = priv_obj.public_key().public_bytes_raw()
    assert base64.b64decode(pub_b64.encode("ascii")) == derived


# ---- second-call idempotency --------------------------------------------


def test_second_call_returns_same_keypair(fresh_store):
    """Once generated, the keypair is sticky — subsequent calls
    fetch from the store (or cache) and return the same bytes."""
    priv1, pub_b64_1 = resolve_operator_keypair(key_store=fresh_store)
    priv2, pub_b64_2 = resolve_operator_keypair(key_store=fresh_store)
    assert priv1 == priv2
    assert pub_b64_1 == pub_b64_2


# ---- namespace isolation ------------------------------------------------


def test_operator_key_doesnt_pollute_agent_list(fresh_store):
    """The reserved operator-key name uses a different prefix
    than agent keys. AgentKeyStore.list_agent_ids must not
    surface the operator key — that would confuse the verifier
    which assumes list_agent_ids enumerates agents only."""
    # Store an actual agent key so the list isn't empty.
    fresh_store.store("agent_a", b"a" * 32)
    # Generate the operator master.
    resolve_operator_keypair(key_store=fresh_store)
    # Agent list shouldn't include the operator name.
    agent_ids = fresh_store.list_agent_ids()
    assert agent_ids == ["agent_a"]


def test_reserved_name_format():
    """Lock the reserved name shape — backends store under this
    exact string. Changing it is a backwards-incompatible
    migration."""
    assert OPERATOR_KEY_NAME == "forest_operator_master:default"


# ---- explicit-store bypasses cache --------------------------------------


def test_explicit_store_isolates_from_default_cache(tmp_path: Path):
    """Passing an explicit key_store argument bypasses the
    process cache. Two separate stores produce two distinct
    operator keypairs."""
    reset_cache()
    backend_a = FileStore(path=tmp_path / "a.yaml")
    backend_b = FileStore(path=tmp_path / "b.yaml")
    store_a = AgentKeyStore(secret_store=backend_a)
    store_b = AgentKeyStore(secret_store=backend_b)
    priv_a, pub_a_b64 = resolve_operator_keypair(key_store=store_a)
    priv_b, pub_b_b64 = resolve_operator_keypair(key_store=store_b)
    # Distinct deployments → distinct keypairs.
    assert priv_a != priv_b
    assert pub_a_b64 != pub_b_b64


# ---- persistence across "process restarts" ------------------------------


def test_keypair_persists_across_fresh_resolves(tmp_path: Path):
    """Simulates a daemon restart: first resolve generates +
    stores; second resolve (after cache reset) reads from the
    persisted store and returns the same bytes."""
    backend = FileStore(path=tmp_path / "secrets.yaml")
    store1 = AgentKeyStore(secret_store=backend)
    priv1, pub_b64_1 = resolve_operator_keypair(key_store=store1)
    # Simulate restart: new AgentKeyStore over the same backend
    # file, fresh cache.
    reset_cache()
    store2 = AgentKeyStore(secret_store=backend)
    priv2, pub_b64_2 = resolve_operator_keypair(key_store=store2)
    assert priv1 == priv2
    assert pub_b64_1 == pub_b64_2
