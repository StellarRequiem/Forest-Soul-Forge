"""AES-256-GCM at-rest encryption substrate — ADR-0050 T3 (B268).

This module is the third-layer consumer of T1's master-key
substrate (B266). T2 (B267) wires the master key into SQLCipher
for the registry SQLite file; T3 wires it into the audit chain's
JSONL entries on a per-event basis. T4 will reuse the same
helpers for application-layer memory body encryption.

## Cipher choice — AES-256-GCM

AES-256-GCM is the canonical authenticated-encryption choice for
new applications in 2026:

- **Authenticated** — the GCM tag detects ciphertext tampering;
  decrypting tampered data raises ``InvalidTag`` rather than
  returning garbage. This is critical for the audit chain — a
  forged ``ct`` field that decrypted to garbage event_data would
  silently flow through downstream consumers.
- **Wide library support** — the ``cryptography.hazmat.primitives.ciphers.aead.AESGCM``
  primitive ships with the project's existing ``cryptography``
  dependency (already pinned via the [daemon] extras for ADR-0049
  signatures).
- **96-bit nonce** — Section 8.2 of NIST SP 800-38D specifies
  random 96-bit (12-byte) nonces are safe up to 2^32 messages
  under the same key. Forest's per-emit nonce generation produces
  a fresh nonce per event; even at 10K events/sec we don't
  approach the bound for ~13 years per key. Key rotation (T8)
  resets the counter.

## On-disk envelope

Per ADR-0050 Decision 3, encrypted audit-chain entries replace
the ``event_data`` field with an ``encryption`` envelope:

```
{
  "seq": 1234,
  ...
  "encryption": {
    "alg": "AES-256-GCM",
    "kid": "master:default",
    "nonce": "<base64-12-bytes>",
    "ct":    "<base64-ciphertext>"
  },
  "signature": "ed25519:..."
}
```

The envelope is plaintext-readable so the chain verifier (and
external integrators per ADR-0044) can walk the chain structure
without unlocking the master key. The ciphertext + tag is the
canonical-JSON-encoded ``event_data`` AES-encrypted under the
master key with the random nonce.

The ``kid`` (key id) supports future key rotation per ADR-0050
Decision 6 — old entries decrypt with the kid they were encrypted
under, new entries use the current kid. T8 ships the rotation
flow; T3 only emits ``master:default``.

## Hash-chain integrity

CRITICAL: ``entry_hash`` is computed over the **plaintext**
event_data, not the ciphertext. This is the invariant that lets
hash-chain verify continue to work without re-encryption when
keys rotate, and lets the chain be moved between key generations
without invalidating its integrity.

Concretely:
  1. Caller calls ``audit.append(event_type, event_data, ...)``
  2. Chain computes ``entry_hash = sha256(canonical(plaintext))``
  3. Chain encrypts ``event_data`` → ``ciphertext`` (this module)
  4. Chain writes the on-disk form with ``encryption`` envelope
  5. Verifier: reads on-disk form, decrypts → plaintext event_data,
     recomputes ``sha256(canonical(plaintext))``, compares to the
     stored ``entry_hash``.

So a kid rotation re-encrypts old entries under a new kid without
touching ``entry_hash``. The chain stays verifiable end-to-end.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Any


# Algorithm identifier used in the on-disk envelope. Locked to
# AES-256-GCM for v1; future ADRs may introduce a different alg.
# The ``alg`` field's presence in every encrypted entry lets a
# future migration distinguish multi-alg chains without
# guessing.
ALG_AES_256_GCM = "AES-256-GCM"

# Default key id for the v1 single-key deployment. T8's
# rotate-key flow introduces date-stamped suffixes (e.g.
# "master:2026-05"); v1 stays on the static "master:default" so
# the migration shape is forward-compatible.
DEFAULT_KID = "master:default"

# AES-GCM nonce length. NIST SP 800-38D Section 8.2 recommends
# 96 bits (12 bytes) for random nonces. cryptography's AESGCM
# enforces 12 bytes by default.
NONCE_LENGTH_BYTES = 12


class EncryptionError(Exception):
    """Raised on at-rest encryption setup or decrypt failure.

    Subclasses help the daemon distinguish startup-time misconfig
    (missing cryptography library, malformed master key) from
    chain-integrity problems (decrypt fails because the ciphertext
    was tampered or the kid maps to a key that doesn't decrypt
    this entry).
    """


class CipherUnavailableError(EncryptionError):
    """Raised when the ``cryptography`` library isn't importable.

    ``cryptography`` ships with the [daemon] extras (already
    required by ADR-0049 per-event signatures); operators running
    the daemon should always have it. This is the safety net for
    test envs that import core/at_rest_encryption.py without the
    daemon dependency tree.
    """


class DecryptError(EncryptionError):
    """Raised when ciphertext can't be decrypted — bad key, wrong
    kid, or tampered ciphertext (the GCM tag mismatches).

    Auditors who hit this for a single entry should suspect
    tampering at the file level; the per-event signature (ADR-0049)
    provides the orthogonal forgery detection.
    """


@dataclass(frozen=True)
class EncryptionConfig:
    """Active-key bundle for at-rest encryption.

    Daemon lifespan constructs one of these when
    ``FSF_AT_REST_ENCRYPTION=true`` and stores it on
    ``app.state.encryption_config``. AuditChain (and T4's memory
    encryption) consume it.

    ``kid`` defaults to ``master:default`` for v1; T8's rotation
    introduces date-stamped kids and a ``previous_keys: dict[str,
    bytes]`` field so decrypt can find the right key for legacy
    entries. Adding that field is forward-compatible (additional
    field, no breaking change).
    """

    master_key: bytes
    kid: str = DEFAULT_KID

    def __post_init__(self) -> None:
        if not isinstance(self.master_key, bytes):
            raise EncryptionError(
                f"master_key must be bytes; got {type(self.master_key).__name__}"
            )
        if len(self.master_key) != 32:
            raise EncryptionError(
                f"master_key must be 32 bytes (AES-256); got {len(self.master_key)}"
            )
        if not isinstance(self.kid, str) or not self.kid:
            raise EncryptionError("kid must be a non-empty string")


def _aesgcm(key: bytes):
    """Construct the AESGCM primitive, raising
    CipherUnavailableError if ``cryptography`` is missing."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as e:  # pragma: no cover — packaging gate
        raise CipherUnavailableError(
            "cryptography library not installed; install via "
            "pip install -e '.[daemon]'"
        ) from e
    return AESGCM(key)


def encrypt_event_data(
    event_data: dict[str, Any],
    config: EncryptionConfig,
) -> dict[str, str]:
    """Encrypt ``event_data`` and return the ADR-0050 envelope.

    Output shape matches the ADR Decision 3 spec:

        {
          "alg":   "AES-256-GCM",
          "kid":   "master:default",
          "nonce": "<base64-12-bytes>",
          "ct":    "<base64-ciphertext-and-tag>"
        }

    Plaintext is canonical-JSON of ``event_data`` (sort_keys,
    compact separators) — the same canonical form used by the
    hash-chain. This guarantees that re-encrypting plaintext
    recovered from a different deployment produces a different
    ciphertext (because of the fresh nonce) but the same
    canonical plaintext, so hash-chain verify is stable.

    Caller is responsible for storing the envelope in the on-disk
    entry's ``encryption`` field and omitting the plaintext
    ``event_data`` field.
    """
    plaintext = json.dumps(
        event_data, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    aesgcm = _aesgcm(config.master_key)
    nonce = os.urandom(NONCE_LENGTH_BYTES)
    # AESGCM.encrypt appends the auth tag to the ciphertext; both
    # are recovered together in decrypt. No associated_data — the
    # envelope's other fields (kid, alg) are plaintext-readable
    # and not part of the AEAD-authenticated input. If a future
    # ADR wants kid binding, that's an additive change to the
    # associated_data parameter.
    ct = aesgcm.encrypt(nonce, plaintext, None)
    return {
        "alg":   ALG_AES_256_GCM,
        "kid":   config.kid,
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ct":    base64.b64encode(ct).decode("ascii"),
    }


def decrypt_event_data(
    envelope: dict[str, Any],
    config: EncryptionConfig,
) -> dict[str, Any]:
    """Recover plaintext ``event_data`` from an ADR-0050 envelope.

    Raises :class:`DecryptError` if:
      - the envelope is missing required fields
      - ``alg`` is unknown (forward-compat with future cipher
        rotations)
      - the kid doesn't match the configured key (T8 will look
        up previous keys; T3 only knows the current one)
      - the GCM tag fails — tampered ciphertext, wrong key, or
        wrong nonce
    """
    if not isinstance(envelope, dict):
        raise DecryptError(
            f"encryption envelope must be dict; got {type(envelope).__name__}"
        )
    alg = envelope.get("alg")
    if alg != ALG_AES_256_GCM:
        raise DecryptError(
            f"unsupported encryption alg {alg!r}; this build only "
            f"decrypts {ALG_AES_256_GCM}"
        )
    kid = envelope.get("kid")
    if kid != config.kid:
        # T8 introduces previous-key lookup. For T3, a kid mismatch
        # is a hard error — the operator likely rotated keys
        # without running the rotation flow.
        raise DecryptError(
            f"entry encrypted under kid={kid!r}, but only "
            f"kid={config.kid!r} is loaded. Key rotation handling "
            "lands in T8."
        )
    nonce_b64 = envelope.get("nonce")
    ct_b64 = envelope.get("ct")
    if not isinstance(nonce_b64, str) or not isinstance(ct_b64, str):
        raise DecryptError(
            "encryption envelope missing nonce or ct string fields"
        )
    try:
        nonce = base64.b64decode(nonce_b64.encode("ascii"), validate=True)
        ct = base64.b64decode(ct_b64.encode("ascii"), validate=True)
    except Exception as e:
        raise DecryptError(f"envelope nonce/ct not valid base64: {e}") from e
    if len(nonce) != NONCE_LENGTH_BYTES:
        raise DecryptError(
            f"nonce must be {NONCE_LENGTH_BYTES} bytes; got {len(nonce)}"
        )

    aesgcm = _aesgcm(config.master_key)
    try:
        plaintext = aesgcm.decrypt(nonce, ct, None)
    except Exception as e:
        # cryptography raises InvalidTag specifically; other
        # exceptions could be malformed input. Both surface as
        # DecryptError so the caller doesn't need to import
        # cryptography to handle them.
        raise DecryptError(
            f"AES-GCM decrypt failed (tampering or wrong key): {e}"
        ) from e

    try:
        data = json.loads(plaintext.decode("utf-8"))
    except Exception as e:
        raise DecryptError(
            f"decrypted bytes are not valid JSON: {e}"
        ) from e

    if not isinstance(data, dict):
        raise DecryptError(
            f"decrypted plaintext must be a JSON object; "
            f"got {type(data).__name__}"
        )
    return data


def encrypt_text(plaintext: str, config: EncryptionConfig) -> str:
    """Encrypt a single string under the master key.

    ADR-0050 T4 (B269): used for the application-layer memory body
    encryption that lives BENEATH the SQLCipher layer (T2). The
    defense-in-depth posture: a hypothetical SQLCipher break still
    leaves the operator's memory bodies sealed by this layer, and
    vice versa.

    Output is a single base64 string carrying the canonical-JSON
    of an envelope dict (alg/kid/nonce/ct). One string round-trips
    cleanly through the SQLite TEXT column without escaping
    surprises — caller stores this exact string in the ``content``
    column and sets ``content_encrypted=1``.

    Plaintext can be any unicode string; encoded as UTF-8 before
    encrypting.
    """
    if not isinstance(plaintext, str):
        raise EncryptionError(
            f"encrypt_text requires str; got {type(plaintext).__name__}"
        )
    aesgcm = _aesgcm(config.master_key)
    nonce = os.urandom(NONCE_LENGTH_BYTES)
    ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    envelope = {
        "alg":   ALG_AES_256_GCM,
        "kid":   config.kid,
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ct":    base64.b64encode(ct).decode("ascii"),
    }
    return base64.b64encode(
        json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    ).decode("ascii")


def decrypt_text(ciphertext_b64: str, config: EncryptionConfig) -> str:
    """Recover plaintext string from an ``encrypt_text`` output.

    Same failure taxonomy as :func:`decrypt_event_data` —
    :class:`DecryptError` on tampered ciphertext, wrong key,
    unknown kid, or malformed envelope.
    """
    if not isinstance(ciphertext_b64, str):
        raise DecryptError(
            f"decrypt_text requires str input; got {type(ciphertext_b64).__name__}"
        )
    try:
        envelope_json = base64.b64decode(
            ciphertext_b64.encode("ascii"), validate=True,
        )
        envelope = json.loads(envelope_json.decode("utf-8"))
    except Exception as e:
        raise DecryptError(
            f"encrypt_text output is malformed: {e}"
        ) from e
    if not isinstance(envelope, dict):
        raise DecryptError(
            f"decrypted envelope must be a JSON object; got {type(envelope).__name__}"
        )
    if envelope.get("alg") != ALG_AES_256_GCM:
        raise DecryptError(
            f"unsupported encryption alg {envelope.get('alg')!r}"
        )
    if envelope.get("kid") != config.kid:
        raise DecryptError(
            f"entry encrypted under kid={envelope.get('kid')!r}, "
            f"but only kid={config.kid!r} is loaded"
        )
    try:
        nonce = base64.b64decode(envelope["nonce"].encode("ascii"), validate=True)
        ct = base64.b64decode(envelope["ct"].encode("ascii"), validate=True)
    except Exception as e:
        raise DecryptError(f"envelope nonce/ct not valid base64: {e}") from e
    if len(nonce) != NONCE_LENGTH_BYTES:
        raise DecryptError(
            f"nonce must be {NONCE_LENGTH_BYTES} bytes; got {len(nonce)}"
        )
    aesgcm = _aesgcm(config.master_key)
    try:
        plaintext = aesgcm.decrypt(nonce, ct, None)
    except Exception as e:
        raise DecryptError(
            f"AES-GCM decrypt failed (tampering or wrong key): {e}"
        ) from e
    return plaintext.decode("utf-8")


def is_encrypted_entry(obj: dict[str, Any]) -> bool:
    """True if the on-disk audit-chain object has the encryption
    envelope.

    Used by ``_entry_from_dict`` to decide whether to decrypt or
    pass through. ADR-0050 Decision 6: mixed legacy/encrypted
    chains coexist. Pre-ADR entries have ``event_data`` and no
    ``encryption`` field; post-ADR entries (when encryption is
    on) have ``encryption`` and no ``event_data``.
    """
    enc = obj.get("encryption")
    return isinstance(enc, dict) and enc.get("alg") is not None
