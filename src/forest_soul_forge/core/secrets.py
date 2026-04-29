"""Per-agent encrypted secrets store — AES-256-GCM.

ADR-003X Phase C1. The foundation the open-web tool family depends on.

Threat model:
    The operator trusts the local machine. The secrets store protects
    against:
        - casual disk-image extraction (encrypted at rest)
        - one-agent-reading-another-agent's-secrets (per-instance_id
          rows + per-agent allowlist enforced by the runtime)
        - accidental log exposure (the API never returns secret values
          via reads except when explicitly fetched by an authorized
          tool, and the audit chain records `secret_revealed` events
          with the name only, never the value)
    It does NOT protect against:
        - root on the local machine
        - a memory dump while the daemon is running (master key is
          held in process memory after lifespan load)
        - the operator pinning the wrong allowlist on an agent

Master key sources, in priority order:
    1. ``FSF_SECRETS_MASTER_KEY`` env var (32-byte base64-encoded).
    2. macOS Keychain via ``security find-generic-password -s
       forest-soul-forge -a secrets-master`` (deferred to a sub-task;
       env var is the v1 path).
    3. None — the secrets subsystem is **disabled**. Tools that need
       a secret refuse cleanly with ``SecretsUnavailableError``; the
       daemon stays up, the defensive plane keeps working.
"""
from __future__ import annotations

import base64
import os
import secrets as _stdlib_secrets
from dataclasses import dataclass
from typing import Optional

# Lazy import — `cryptography` is in the daemon optional-deps, but a
# fresh checkout that's only running tests shouldn't crash on import.
_AESGCM = None


def _aesgcm():
    global _AESGCM
    if _AESGCM is None:
        # Import here so the `cryptography` package is only required
        # when the secrets subsystem is actually used.
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        _AESGCM = AESGCM
    return _AESGCM


# ---------------------------------------------------------------------------
# Errors — distinct types so the dispatcher / tools can react differently.
# ---------------------------------------------------------------------------
class SecretsError(Exception):
    """Base class for secrets subsystem failures."""


class SecretsUnavailableError(SecretsError):
    """No master key configured — secrets subsystem is disabled."""


class SecretsKeyError(SecretsError):
    """Master key was present but malformed (bad base64, wrong length)."""


class SecretsAccessDeniedError(SecretsError):
    """Agent tried to read a secret name not on its allowlist."""


class UnknownSecretError(SecretsError):
    """Secret name doesn't exist for the given (instance_id, name)."""


# ---------------------------------------------------------------------------
# Master key loader
# ---------------------------------------------------------------------------
ENV_VAR = "FSF_SECRETS_MASTER_KEY"
KEY_LEN_BYTES = 32  # AES-256


@dataclass(frozen=True)
class MasterKey:
    """A loaded master key. Holds the raw 32 bytes; never logged.

    The dataclass is frozen + the `__repr__` is overridden so
    accidentally printing a MasterKey in an exception traceback or
    logger doesn't leak the bytes.
    """

    raw: bytes

    def __post_init__(self) -> None:
        if len(self.raw) != KEY_LEN_BYTES:
            raise SecretsKeyError(
                f"master key must be {KEY_LEN_BYTES} bytes (AES-256); got {len(self.raw)}"
            )

    def __repr__(self) -> str:  # pragma: no cover — anti-leak guard
        return "MasterKey(<redacted>)"


def load_master_key() -> Optional[MasterKey]:
    """Return the configured master key, or None if not configured.

    Sources in priority order: ``FSF_SECRETS_MASTER_KEY`` env var (v1).
    Future: macOS Keychain. When None is returned the secrets subsystem
    is disabled — callers (the dispatcher) should propagate that to
    tools as ``SecretsUnavailableError`` on first read.
    """
    raw_b64 = os.environ.get(ENV_VAR)
    if not raw_b64:
        return None
    try:
        # urlsafe_b64decode tolerates both standard and URL-safe alphabets
        # and accepts padding-optional input — friendlier for hand-typed
        # keys in .env files. Length check happens in MasterKey.__post_init__.
        raw = base64.urlsafe_b64decode(raw_b64 + "==")  # tolerate missing padding
    except Exception as e:
        raise SecretsKeyError(f"{ENV_VAR} is not valid base64: {e}") from e
    return MasterKey(raw=raw)


def generate_master_key_b64() -> str:
    """Return a fresh 32-byte master key, base64-encoded.

    Helper for the operator: ``python -c "from forest_soul_forge.core.secrets
    import generate_master_key_b64; print(generate_master_key_b64())"``
    Then export it as ``FSF_SECRETS_MASTER_KEY`` in their .env.
    """
    return base64.urlsafe_b64encode(_stdlib_secrets.token_bytes(KEY_LEN_BYTES)).decode("ascii")


# ---------------------------------------------------------------------------
# Encrypt / decrypt
# ---------------------------------------------------------------------------
NONCE_LEN_BYTES = 12  # AES-GCM standard


def encrypt(master: MasterKey, plaintext: str, *, associated: bytes = b"") -> tuple[bytes, bytes]:
    """Return ``(ciphertext, nonce)`` for the given plaintext.

    ``associated`` is per-call AAD — the registry passes the agent's
    instance_id + secret name so that a stolen ciphertext cannot be
    re-attached to a different (agent, name) pair without detection.
    """
    aesgcm = _aesgcm()(master.raw)
    nonce = _stdlib_secrets.token_bytes(NONCE_LEN_BYTES)
    ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), associated)
    return ct, nonce


def decrypt(master: MasterKey, ciphertext: bytes, nonce: bytes, *, associated: bytes = b"") -> str:
    """Inverse of :func:`encrypt`. Raises if AAD or tag don't match."""
    aesgcm = _aesgcm()(master.raw)
    pt = aesgcm.decrypt(nonce, ciphertext, associated)
    return pt.decode("utf-8")


def aad_for(instance_id: str, name: str) -> bytes:
    """Canonical AAD for a (instance_id, name) pair.

    Pinning the AAD prevents an attacker who copies a ciphertext from
    one agent's row to another's from being able to decrypt it — the
    AEAD tag check fails because the AAD doesn't match.
    """
    return f"fsf:secret:{instance_id}:{name}".encode("utf-8")
