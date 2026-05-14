"""Passphrase → master key KDF — ADR-0050 T6 (B273).

Forest's at-rest encryption (ADR-0050) needs a 32-byte master key.
T1 (B266) shipped the substrate that fetches it from a SecretStore
backend (keychain on macOS, file elsewhere). T6 adds a third option:
**derive the key from an operator passphrase** via a memory-hard KDF.

## Why passphrase backend

Operators on machines where the keychain isn't available (CI/headless
boxes, Linux without secret-service, custom hardened deployments) need
a way to enable encryption-at-rest without storing the raw key on
disk. A passphrase the operator memorizes (or feeds via env at
non-interactive startup) closes that gap.

Trade-off accepted: the passphrase is in operator memory or in an env
var the operator manages. Compromise of the env's value → compromise
of the key, same as Keychain compromise → compromise. The threat-model
boundary is identical at the substrate level; the difference is who
owns the unlock material (the operator vs. the OS keychain).

## KDF choice — Scrypt (not Argon2id)

ADR-0050 Decision 5 named Argon2id. Forest's existing `cryptography`
dep already ships ``cryptography.hazmat.primitives.kdf.scrypt.Scrypt``;
adding `argon2-cffi` would mean a new transitive dep + native build
tooling on every install. Scrypt is memory-hard (resistant to GPU
brute-force), well-vetted (used by Litecoin, Tarsnap), and the only
KDF in the existing dep set with the required properties.

The decision to swap Argon2id → Scrypt is contained to T6 substrate;
ADR-0050 will be amended at the T8 close-out (the rotation tool
needs both surfaces stable before the ADR text catches up). If a
future operator on regulated infra needs Argon2id specifically,
they can swap by editing this module — the KDF surface is
intentionally small.

## Parameters

Scrypt parameters chosen for ~250ms-1s on modern desktop hardware
(M-series Mac mini, mid-range x86):

  - N (cost factor)          = 2**16 = 65536
  - r (block size)           = 8     (32-byte blocks)
  - p (parallelism factor)   = 1
  - dklen (derived key len)  = 32    (AES-256 key length)

Memory cost ≈ 128 * N * r = 64 MiB. Sufficient for a single login
boot; not a hot-path operation.

## Salt

Persisted to disk because the SAME passphrase + DIFFERENT salt
produces a DIFFERENT key, which would make existing encrypted
data unrecoverable. The salt itself is not secret — it's an
anti-precomputation primitive (rainbow-table resistance). 16
random bytes generated on first boot, written to
``<data_dir>/master_salt``, never rotated unless the operator
deliberately wipes the encrypted data.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


# Scrypt parameters. See the module docstring for the rationale.
# Changing any of these without a migration plan invalidates every
# encrypted artifact stored under the previous parameters.
_SCRYPT_N = 2 ** 16
_SCRYPT_R = 8
_SCRYPT_P = 1
_DERIVED_KEY_LENGTH_BYTES = 32

# Random-salt length. 16 bytes (128 bits) is the standard collision-
# resistant size; doubling to 32 buys nothing meaningful against a
# passphrase-strength adversary and bloats the on-disk salt file.
_SALT_LENGTH_BYTES = 16


class PassphraseKDFError(RuntimeError):
    """Raised when the passphrase-derive flow can't complete.

    Surfaces include: malformed salt on disk (corrupted file),
    passphrase is empty/whitespace, KDF library missing the
    requested params (shouldn't happen with stock cryptography
    builds but checked defensively).
    """


def derive_key_from_passphrase(passphrase: str, salt: bytes) -> bytes:
    """Run Scrypt over ``passphrase`` + ``salt`` → 32-byte master key.

    Same inputs always produce the same output bytes (deterministic
    KDF). Caller is responsible for persisting the salt — a fresh
    salt on the second boot generates a different key, which would
    render previously-encrypted data unrecoverable.

    Raises :class:`PassphraseKDFError` on empty / whitespace-only
    passphrase. Wrong-length salt raises via the underlying Scrypt
    constructor.
    """
    if not isinstance(passphrase, str) or not passphrase.strip():
        raise PassphraseKDFError(
            "passphrase must be a non-empty string"
        )
    if not isinstance(salt, (bytes, bytearray)) or len(salt) != _SALT_LENGTH_BYTES:
        raise PassphraseKDFError(
            f"salt must be exactly {_SALT_LENGTH_BYTES} bytes; "
            f"got {len(salt) if isinstance(salt, (bytes, bytearray)) else type(salt).__name__}"
        )
    kdf = Scrypt(
        salt=bytes(salt),
        length=_DERIVED_KEY_LENGTH_BYTES,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def load_or_create_salt(salt_path: Path) -> bytes:
    """Read the 16-byte salt at ``salt_path`` or generate + persist it.

    First-boot behavior: if the file doesn't exist, generate a fresh
    salt via ``secrets.token_bytes`` (CSPRNG), write it with mode
    0600, and return it. Subsequent boots read the existing salt.

    Wrong-length file (truncated, tampered, or written by an
    incompatible Forest version) raises :class:`PassphraseKDFError`
    — silently regenerating would orphan all previously-encrypted
    data. The operator decides whether to wipe + restart from
    scratch or restore a backup.

    The parent directory is created if missing — matches the
    secrets file-store convention.
    """
    if salt_path.exists():
        try:
            salt = salt_path.read_bytes()
        except OSError as e:
            raise PassphraseKDFError(
                f"could not read salt at {salt_path}: {e}"
            ) from e
        if len(salt) != _SALT_LENGTH_BYTES:
            raise PassphraseKDFError(
                f"salt at {salt_path} has wrong length: "
                f"got {len(salt)} bytes, expected {_SALT_LENGTH_BYTES}. "
                "If this is intentional, the operator must wipe the "
                "encrypted data — the same passphrase under a new salt "
                "produces a different key, and everything encrypted "
                "under the old key is unrecoverable."
            )
        return salt

    # First-boot generation.
    salt_path.parent.mkdir(parents=True, exist_ok=True)
    salt = secrets.token_bytes(_SALT_LENGTH_BYTES)
    # Write with restrictive permissions. The salt itself isn't
    # secret (it's an anti-precomputation primitive) but operators
    # expect everything under ~/.forest/ to be 0600-tight.
    salt_path.write_bytes(salt)
    try:
        os.chmod(salt_path, 0o600)
    except OSError:
        # Best-effort — on Windows or unusual filesystems the
        # chmod may no-op. The salt isn't load-bearing for
        # confidentiality so the failure is non-fatal.
        pass
    return salt


def default_salt_path(data_dir: Path) -> Path:
    """Canonical location for the passphrase salt.

    Lives under the daemon's data directory alongside other
    persistent operator state (the registry, the audit chain,
    soul artifacts). Naming convention mirrors the SecretStore
    file-backed store at the same root.
    """
    return data_dir / "master_salt"
