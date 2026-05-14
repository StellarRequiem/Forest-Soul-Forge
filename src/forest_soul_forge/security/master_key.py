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
from pathlib import Path

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

# Env-var name for the passphrase-mode non-interactive supply.
# ADR-0050 T6 (B273): when FSF_MASTER_KEY_BACKEND=passphrase and
# stdin is not a TTY, the daemon looks here for the passphrase
# rather than hanging on getpass(). Use for launchd / systemd /
# CI-managed boots. Whitespace-only counts as unset.
FSF_MASTER_PASSPHRASE_ENV = "FSF_MASTER_PASSPHRASE"


# ---- cache ---------------------------------------------------------------

_CACHE: dict[str, bytes] = {}
_CACHE_LOCK = threading.RLock()


# ---- public surface ------------------------------------------------------


def resolve_master_key(
    *,
    key_store: AgentKeyStore | None = None,
    data_dir: "Path | None" = None,
) -> bytes:
    """Get (or generate-and-store) the at-rest encryption master key.

    Returns the raw 32-byte master key suitable for direct use as
    an AES-256 key (via cryptography.hazmat.primitives.ciphers.aead.AESGCM)
    or as input to SQLCipher's PRAGMA key.

    Backend selection:

      - ``passphrase`` (FSF_MASTER_KEY_BACKEND=passphrase, ADR-0050 T6):
        derive the key via Scrypt from operator passphrase + a
        persisted random salt. Passphrase source:

          1. ``FSF_MASTER_PASSPHRASE`` env (non-interactive — CI,
             launchd, systemd-managed daemons)
          2. interactive ``getpass.getpass()`` when stdin is a TTY
          3. otherwise raise — non-interactive without an env-supplied
             passphrase means the operator forgot to set one, and
             silently downgrading to a different backend would split
             the encrypted data store

      - ``keychain`` / ``file`` (default platform-routed):
        fetch from the configured SecretStore backend. First call
        ever (no persisted key) generates a fresh 32-byte key
        and stores it. Subsequent calls return the cached value.

    Pass an explicit ``key_store`` to bypass the cache + the
    default resolver. Used by tests with a tmpdir-backed store.

    Pass ``data_dir`` to override the passphrase salt location;
    defaults to ``~/.forest``. Only consulted when the passphrase
    backend is active.
    """
    backend = configured_backend_name()

    if backend == "passphrase":
        # T6 (B273) — passphrase-derived master key. Bypasses the
        # SecretStore entirely: the key never touches disk, only
        # the salt does. Cache is keyed by MASTER_KEY_NAME so the
        # passphrase-derived key shares the same cache slot.
        with _CACHE_LOCK:
            if MASTER_KEY_NAME in _CACHE:
                return _CACHE[MASTER_KEY_NAME]
            key = _resolve_via_passphrase(data_dir=data_dir)
            _CACHE[MASTER_KEY_NAME] = key
            return key

    if backend == "hsm":
        raise NotImplementedError(
            "FSF_MASTER_KEY_BACKEND=hsm is reserved for ADR-0050 T16; "
            "use keychain (macOS), file (Linux/CI), or passphrase (T6) for now."
        )

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


def _resolve_via_passphrase(*, data_dir: "Path | None" = None) -> bytes:
    """T6 (B273) — derive the master key from an operator passphrase.

    Resolution order:
      1. ``FSF_MASTER_PASSPHRASE`` env (whitespace-stripped; empty
         after strip counts as absent)
      2. interactive prompt via ``getpass.getpass`` when stdin is
         a TTY
      3. raise RuntimeError — non-interactive + no env means we
         can't proceed without silently downgrading the operator's
         posture, which would split the encrypted store

    Salt is persisted via :func:`load_or_create_salt` so the same
    passphrase derives the same key across restarts. The default
    salt location is ``<data_dir or ~/.forest>/master_salt``.
    """
    from pathlib import Path as _Path
    from forest_soul_forge.security.passphrase_kdf import (
        default_salt_path,
        derive_key_from_passphrase,
        load_or_create_salt,
    )

    env_pass = (os.environ.get(FSF_MASTER_PASSPHRASE_ENV) or "").strip()
    if env_pass:
        passphrase = env_pass
    else:
        # Interactive prompt only when stdin is a real TTY. Avoids
        # hanging non-interactive daemons (launchd / systemd / CI
        # runners) waiting on a prompt nobody will answer.
        import sys
        if sys.stdin is None or not sys.stdin.isatty():
            raise RuntimeError(
                "FSF_MASTER_KEY_BACKEND=passphrase but stdin is not a TTY "
                f"and {FSF_MASTER_PASSPHRASE_ENV} is not set. Either run "
                "the daemon interactively to enter the passphrase, or "
                "supply it via env for non-interactive boots."
            )
        import getpass
        try:
            passphrase = getpass.getpass(
                "Forest at-rest encryption passphrase: "
            )
        except (EOFError, KeyboardInterrupt) as e:
            raise RuntimeError(
                "passphrase prompt cancelled — daemon refused to boot "
                "rather than silently fall back to plaintext"
            ) from e
        if not passphrase.strip():
            raise RuntimeError(
                "empty passphrase supplied; refusing to derive a master "
                "key from an empty string"
            )

    # Salt path. Default to ~/.forest/master_salt, matching the
    # SecretStore file-store convention. Operators who put the
    # daemon's data dir elsewhere pass data_dir explicitly.
    if data_dir is None:
        data_dir = _Path.home() / ".forest"
    salt = load_or_create_salt(default_salt_path(data_dir))
    return derive_key_from_passphrase(passphrase, salt)


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
