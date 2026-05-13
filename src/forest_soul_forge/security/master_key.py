"""At-rest encryption master key — ADR-0050 T1 (B266).

Forest's audit chain, registry SQLite, and memory bodies will be
encrypted at rest under a single 32-byte AES-256-GCM master key.
This module is the substrate that produces (or loads) that key
during daemon startup. T2 (sqlcipher3 PRAGMA key) and T3
(per-event audit chain encryption) consume it; T1 just stands
up the key-management surface.

## Surface

  - :func:`resolve_master_key` — process-cached. On first call,
    fetches the 32-byte key from the configured backend under
    the reserved name :data:`MASTER_KEY_NAME`. If absent (first
    daemon startup ever, fresh data dir), generates a fresh
    cryptographically-random 32-byte key, stores it, and returns
    it. Subsequent calls return the cached value.

  - :data:`MASTER_KEY_NAME` — the reserved SecretStore secret
    name. Multi-key support (rotation: ``...:2026-05``) is
    forward-compatible via the colon-suffix convention but only
    ``default`` is consumed in T1; T8 (``fsf encrypt rotate-key``)
    introduces date-stamped suffixes.

  - :data:`FSF_MASTER_KEY_BACKEND_ENV` — env var name
    (``FSF_MASTER_KEY_BACKEND``) that picks the backend:
    ``keychain``  → macOS Keychain (Secure Enclave-backed where
                    available)
    ``file``      → file-backed SecretStore (cross-platform default
                    on non-darwin; INSECURE compared to keychain)
    ``passphrase`` → operator passphrase + Argon2id KDF — T6
    ``hsm``       → hardware security module — T16

## Why mirror operator_key.py

The operator master keypair (ADR-0061) and the at-rest master key
(this ADR) are different concerns — different cryptographic
algorithms (ed25519 vs. AES-256), different threat models, different
lifecycle requirements — but they have IDENTICAL substrate needs:
"persist a small secret to platform-best storage, fetch it on
demand at daemon startup, cache it for the process lifetime, treat
the on-disk encoding as a backend implementation detail."

ADR-0050 Decision 5 specifies KeyStore reuse for exactly this
reason. The shipped substrate is ``SecretStoreProtocol`` (ADR-0052),
which the operator key wraps; this module wraps it the same way.
Two reserved names, one backend, one key-management surface.

## Why per-process cache

Daemon startup calls this once at lifespan. Tools / dispatchers
that need to encrypt or decrypt request the cached key. Without
caching, every read would round-trip through the OS keychain
(macOS: a small but measurable IPC); with caching, the key lives
in process memory for the daemon's lifetime.

The trusted-host model (ADR-0025) explicitly accepts this:
attackers with debugger access to the daemon process can read
the master key from memory. Anything stronger than that needs
HSM-backed key sealing (T16) or process-isolation hardening
(ptrace_scope=3, hardened-runtime entitlements) — both
platform-level concerns outside ADR-0050's substrate.
"""
from __future__ import annotations

import base64
import os
import secrets
import threading

from forest_soul_forge.security.keys import (
    AgentKeyStore,
    resolve_agent_key_store,
)


# ---- constants ------------------------------------------------------------

# Reserved SecretStore secret name. DO NOT change this string
# without a migration — backends store the key under this exact
# name. Suffix supports key rotation (``...:2026-05``) in the
# future; ``default`` is the only one used today (T8 introduces
# the rotation flow).
#
# Convention: prefix is ``forest_master_key`` (distinct from
# ``forest_agent_key:`` and ``forest_operator_master:``). The three
# namespaces let the operator audit `fsf secret list` / Keychain
# Access without per-name confusion.
_MASTER_KEY_LOCAL_PART = "default"
MASTER_KEY_NAME = f"forest_master_key:{_MASTER_KEY_LOCAL_PART}"

# Length of the master key in bytes. 32 bytes = 256 bits = the
# canonical AES-256 key length used by AES-256-GCM (per-event audit
# chain encryption in T3) and SQLCipher's PRAGMA key (in T2). Do
# NOT shorten without recoordinating both consumers — they assume
# 32-byte input.
MASTER_KEY_LENGTH_BYTES = 32

# Env-var name for backend selection. Documented in the module
# docstring; the actual default-platform logic lives in
# :func:`_select_backend_for_master_key` so the env-var lookup
# stays in one place.
FSF_MASTER_KEY_BACKEND_ENV = "FSF_MASTER_KEY_BACKEND"


# ---- cache ---------------------------------------------------------------

_CACHE: dict[str, bytes] = {}
_CACHE_LOCK = threading.RLock()


# ---- public surface ------------------------------------------------------


def resolve_master_key(
    *,
    key_store: AgentKeyStore | None = None,
) -> bytes:
    """Get (or generate-and-store) the at-rest encryption master key.

    Returns the raw 32-byte master key suitable for direct use as
    an AES-256 key (via cryptography.hazmat.primitives.ciphers.aead.AESGCM)
    or as input to SQLCipher's PRAGMA key.

    On first call after process start, fetches from the configured
    SecretStore backend via :func:`resolve_agent_key_store`. If
    absent (first daemon startup ever, or fresh data dir),
    generates a fresh 32-byte key via :func:`secrets.token_bytes`
    (cryptographically random per CSPRNG) and persists it. Caches
    the result for the process lifetime.

    Pass an explicit ``key_store`` to bypass the cache + the
    default resolver. Used by tests with a tmpdir-backed store.
    """
    if key_store is not None:
        # Explicit-backend path: don't touch the cache.
        return _generate_or_load(key_store)

    with _CACHE_LOCK:
        if MASTER_KEY_NAME in _CACHE:
            return _CACHE[MASTER_KEY_NAME]
        store = resolve_agent_key_store()
        key = _generate_or_load(store)
        _CACHE[MASTER_KEY_NAME] = key
        return key


def reset_cache() -> None:
    """Clear the process-cached master key.

    Tests call this between scenarios that want a fresh
    backend-derived key. Operators should not normally need this —
    the cache is process-bound and a daemon restart effectively
    resets it. T8's rotate-key flow also uses this after writing
    the new key to the backend, so the next ``resolve_master_key``
    re-reads from disk.
    """
    with _CACHE_LOCK:
        _CACHE.clear()


def configured_backend_name() -> str:
    """Report the backend that ``resolve_master_key`` will use.

    Used by startup_diagnostics + the (future) ``fsf encrypt
    status`` CLI subcommand so the operator can verify what's
    actually configured without unlocking the key.

    Resolution priority (highest first):
      1. ``FSF_MASTER_KEY_BACKEND`` env var (when set to a known
         value)
      2. Platform default: ``keychain`` on darwin, ``file``
         elsewhere

    Unknown env values fall back to the platform default so a typo
    doesn't silently route through an unintended backend.
    """
    raw = (os.environ.get(FSF_MASTER_KEY_BACKEND_ENV) or "").strip().lower()
    if raw in _KNOWN_BACKENDS:
        return raw
    return _platform_default_backend()


# ---- internals -----------------------------------------------------------


_KNOWN_BACKENDS: frozenset[str] = frozenset({
    "keychain", "file", "passphrase", "hsm",
})


def _platform_default_backend() -> str:
    """macOS → ``keychain`` (Secure Enclave-backed when available).
    Anything else → ``file`` (the SecretStore file-backed store).

    Note: the file-backed default on non-darwin matches the
    ``resolve_agent_key_store`` resolver's own default, so an
    operator on Linux who hasn't explicitly configured a backend
    gets identical placement for the master key and per-agent
    keys. This is the substrate-reuse property ADR-0050 Decision 5
    relies on.
    """
    import sys
    return "keychain" if sys.platform == "darwin" else "file"


def _generate_or_load(store: AgentKeyStore) -> bytes:
    """Read existing master key, or generate + persist a fresh one.

    The AgentKeyStore wrapper rejects ``instance_id`` with colon
    delimiters in its public ``store()`` / ``fetch()`` API (the
    colon is reserved for its own namespace convention), so we
    go through the underlying SecretStoreProtocol backend directly
    — identical pattern to ``operator_key._generate_or_load``.

    The colon-prefix convention (``forest_master_key:default``)
    keeps the master-key namespace separate from per-agent keys
    (``forest_agent_key:<instance>``) and the operator key
    (``forest_operator_master:default``), so listing operations
    in any backend stay cleanly partitioned by prefix.

    Backend selection (FSF_MASTER_KEY_BACKEND env override) is
    handled at the resolve_master_key level by passing an
    explicit ``key_store`` — backend resolution there walks the
    same routing as resolve_agent_key_store, just with the
    different env var. T1 ships the env var name + the routing
    rule; ``passphrase`` and ``hsm`` backends raise NotImplemented
    until T6 / T16 add them. T1's ``file`` and ``keychain`` paths
    pick the existing SecretStore implementations from
    ``security.secrets`` so no new backend wiring lands here.
    """
    backend = store._backend  # type: ignore[attr-defined]
    encoded = backend.get(MASTER_KEY_NAME)
    if encoded is not None:
        try:
            key_bytes = base64.b64decode(encoded.encode("ascii"), validate=True)
        except Exception as e:
            # Malformed payload in the backend. A corrupted master
            # key is unrecoverable — the data encrypted under it
            # is gone. Surface a clear exception rather than
            # silently generate a fresh key (which would discard
            # everything encrypted under the old one).
            raise RuntimeError(
                f"master key in backend is malformed: {e}. "
                "If this is intentional (lost key), the encrypted "
                "data is unrecoverable. See docs/runbooks/"
                "encryption-at-rest.md key-loss section."
            ) from e
        if len(key_bytes) != MASTER_KEY_LENGTH_BYTES:
            raise RuntimeError(
                f"master key in backend has wrong length: "
                f"got {len(key_bytes)} bytes, expected "
                f"{MASTER_KEY_LENGTH_BYTES}. Backend may be "
                f"corrupted or written by an incompatible Forest "
                f"version."
            )
        return key_bytes

    # First-time generation. 32 bytes from secrets.token_bytes is
    # cryptographically random per the OS CSPRNG (urandom on linux,
    # SecRandomCopyBytes on macOS). No application-level entropy
    # mixing — relying on the OS CSPRNG is the right primitive.
    key_bytes = secrets.token_bytes(MASTER_KEY_LENGTH_BYTES)
    backend.put(
        MASTER_KEY_NAME,
        base64.b64encode(key_bytes).decode("ascii"),
    )
    return key_bytes
