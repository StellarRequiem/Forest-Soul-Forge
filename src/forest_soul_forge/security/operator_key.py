"""Operator master keypair — root of the ADR-0061 passport trust
chain.

Every Forge daemon, on first startup, generates an ed25519
keypair bound to the operator's deployment. The private key is
the root of trust for minting agent passports; the public key
is shared (via copy-paste, well-known URL, etc.) with receiving
machines so they can verify passports from this operator's
agents.

## Surface

  - ``resolve_operator_keypair() -> (private_bytes, public_b64)``
    Process-cached. On first call, fetches the keypair from
    the AgentKeyStore under the reserved name
    ``forest_operator_master:default``. If absent, generates
    a fresh keypair, stores the private bytes, and returns
    (priv_bytes, pub_b64). Subsequent calls return the cached
    pair.

  - ``OPERATOR_KEY_NAME = "forest_operator_master:default"``
    The reserved AgentKeyStore secret name. Multi-operator
    support (e.g., ``...:second_op``) is forward-compatible
    via the colon-suffix convention but not exposed today.

## Why a reserved name rather than a parallel store

The AgentKeyStore (ADR-0049 T1) already provides backend-
abstracted byte storage with three backend implementations
(file/keychain/vaultwarden) and the right security posture
per platform. The operator master is just another kind of
keypair — same shape, same threat model, same backend
preferences. Reusing the substrate via a reserved name
keeps Forest's "where do secrets live" surface unified for
the operator.

The name uses the same ``namespace:identifier`` pattern as
agent keys (``forest_agent_key:<instance_id>``). The
``forest_operator_master:`` prefix makes the operator-key
namespace visible in any backend that lists secrets by name
(macOS Keychain Access, ``fsf secret list``, etc.).
"""
from __future__ import annotations

import base64
import threading
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from cryptography.hazmat.primitives import serialization

from forest_soul_forge.security.keys import (
    AgentKeyNotFoundError,
    AgentKeyStore,
    resolve_agent_key_store,
)


# ---- constants ------------------------------------------------------------

# Reserved AgentKeyStore secret name for the operator master.
# DO NOT change this string without a migration — backends store
# the key under this exact name. Suffix supports multi-operator
# in the future (e.g. ``...:secondary``); ``default`` is the
# only one used today.
#
# Convention: prefix is ``forest_operator_master`` (NOT
# ``forest_agent_key`` — distinct namespace from per-agent keys
# so AgentKeyStore.list_agent_ids() doesn't include the operator
# key). The colon delimiter is the same as the agent-key
# namespace's instance_id suffix.
_OPERATOR_KEY_LOCAL_PART = "default"
OPERATOR_KEY_NAME = f"forest_operator_master:{_OPERATOR_KEY_LOCAL_PART}"


# ---- cache ---------------------------------------------------------------

_CACHE: dict[str, tuple[bytes, str]] = {}
_CACHE_LOCK = threading.RLock()


# ---- public surface ------------------------------------------------------


def resolve_operator_keypair(
    *,
    key_store: AgentKeyStore | None = None,
) -> tuple[bytes, str]:
    """Get (or generate-and-store) the operator master keypair.

    Returns ``(private_key_bytes, public_key_b64)``:

    - ``private_key_bytes`` is the raw 32-byte ed25519 private
      seed. Callers use it via
      ``Ed25519PrivateKey.from_private_bytes(priv_bytes)``.
    - ``public_key_b64`` is the base64-encoded raw 32-byte
      ed25519 public key, identical to the shape stored in
      ``agents.public_key`` for per-agent keypairs. Operators
      paste this string into receiving machines' trust lists.

    On first call after process start, fetches from the
    AgentKeyStore via :func:`resolve_agent_key_store`. If
    absent (first daemon startup ever, or fresh data dir),
    generates a new keypair and stores the private bytes. The
    public key is recomputed from the private bytes — never
    stored separately.

    Pass an explicit ``key_store`` to bypass the cache + the
    default resolver. Used by tests with a tmpdir-backed store.
    """
    if key_store is not None:
        # Explicit-backend path: don't touch the cache.
        return _generate_or_load(key_store)

    with _CACHE_LOCK:
        if OPERATOR_KEY_NAME in _CACHE:
            return _CACHE[OPERATOR_KEY_NAME]
        store = resolve_agent_key_store()
        priv_bytes, pub_b64 = _generate_or_load(store)
        _CACHE[OPERATOR_KEY_NAME] = (priv_bytes, pub_b64)
        return priv_bytes, pub_b64


def reset_cache() -> None:
    """Clear the process-cached operator keypair.

    Tests call this between scenarios that want a fresh
    keystore-derived keypair. Operators should not normally
    need this — the cache is process-bound and a daemon
    restart effectively resets it.
    """
    with _CACHE_LOCK:
        _CACHE.clear()


# ---- internals -----------------------------------------------------------


def _generate_or_load(store: AgentKeyStore) -> tuple[bytes, str]:
    """Read existing keypair, or generate + persist a fresh one.

    AgentKeyStore.store stores via the reserved name. The
    instance_id-style argument is just a string identifier from
    the store's perspective; the colon-prefix convention keeps
    the operator-key namespace distinct from agent keys.

    Note: the AgentKeyStore guard rejects ``instance_id`` with
    a colon (its own delimiter convention). We bypass this by
    going directly through the underlying SecretStoreProtocol
    backend with the reserved-name string.
    """
    # Look up via the raw backend so the namespace-prefix
    # validation in AgentKeyStore doesn't trip on the colon
    # in our reserved name.
    backend = store._backend  # type: ignore[attr-defined]
    encoded = backend.get(OPERATOR_KEY_NAME)
    if encoded is not None:
        priv_bytes = base64.b64decode(encoded.encode("ascii"), validate=True)
    else:
        priv_obj = Ed25519PrivateKey.generate()
        priv_bytes = priv_obj.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        backend.put(
            OPERATOR_KEY_NAME,
            base64.b64encode(priv_bytes).decode("ascii"),
        )

    # Derive public from private; the public is never stored
    # separately — recomputed on demand keeps the keystore
    # single-source-of-truth.
    priv_obj = Ed25519PrivateKey.from_private_bytes(priv_bytes)
    pub_bytes = priv_obj.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    pub_b64 = base64.b64encode(pub_bytes).decode("ascii")
    return priv_bytes, pub_b64
