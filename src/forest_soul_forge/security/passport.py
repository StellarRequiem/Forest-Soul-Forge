"""ADR-0061 Agent Passport — mint + verify primitives.

A passport is a JSON document that authorizes a specific agent
to run on a specific set of hardware fingerprints. The operator's
Forge daemon signs the passport with the operator master
keypair (ADR-0061 D1, see ``operator_key.py``); the receiving
machine's daemon verifies the signature + checks the trust list
+ checks the current host fingerprint + checks expiry.

This module is the **cryptographic primitive layer** — pure
functions over passport dicts + raw key bytes. It does NOT
consult the registry, the AgentKeyStore, or the trusted-issuers
list. Callers (daemon endpoints, CLI subcommands, the K6
quarantine integration) thread those resolutions in.

## Shape

Passport JSON (per ADR-0061 D2):

```json
{
  "version": 1,
  "agent_dna": "abc123",
  "instance_id": "operator_companion_abc123abc123",
  "agent_public_key": "base64 raw 32 bytes",
  "authorized_fingerprints": ["fp_birth", "fp_laptop"],
  "issued_at": "2026-05-12T22:00:00Z",
  "expires_at": "2026-08-12T22:00:00Z",
  "issuer_public_key": "base64 raw 32 bytes",
  "signature": "ed25519:base64 64 bytes"
}
```

## Canonical form

Signature is computed over the canonical JSON of all fields
EXCEPT ``signature``: sort keys, no whitespace, UTF-8.
sha256-then-sign would be valid; ed25519 lets us skip the
intermediate hash and sign the bytes directly. We sign the
canonical-form bytes directly — same pattern as JWS-compact +
matches ADR-0049's audit-event signing (which signs entry_hash
bytes directly).
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)
from cryptography.exceptions import InvalidSignature


# ---- constants ------------------------------------------------------------

PASSPORT_VERSION_V1 = 1

#: Algorithm prefix in the ``signature`` field. Locked to ed25519
#: in v1; future ADRs adding post-quantum signatures bump the
#: version and add a new prefix (``sphincs+:`` etc.) so the
#: verifier can branch on algorithm.
SIGNATURE_ALG_PREFIX = "ed25519:"


# ---- errors --------------------------------------------------------------


class PassportError(Exception):
    """Base class for passport-layer failures."""


class PassportFormatError(PassportError):
    """The passport dict is missing required fields or has the
    wrong shape. Distinguishable from PassportInvalidError so
    callers can surface "malformed input" separately from
    "signature didn't verify"."""


class PassportInvalidError(PassportError):
    """Passport's signature failed verification, OR the issuer
    isn't trusted, OR the current host isn't in the authorized
    fingerprints, OR the passport is expired. Caller can read
    ``args[0]`` for the human-readable reason."""


# ---- canonical-form serialization ----------------------------------------


def _canonical_form(passport_without_signature: dict[str, Any]) -> bytes:
    """Canonical JSON of the passport-minus-signature.

    sort_keys + no whitespace separators + UTF-8. Same convention
    as the audit chain's _canonical_hash_input (ADR-0005 B134).
    """
    return json.dumps(
        passport_without_signature,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


# ---- mint ---------------------------------------------------------------


def mint_passport(
    *,
    agent_dna: str,
    instance_id: str,
    agent_public_key_b64: str,
    authorized_fingerprints: list[str],
    operator_private_key: bytes,
    issuer_public_key_b64: str,
    issued_at: str | None = None,
    expires_at: str | None = None,
    version: int = PASSPORT_VERSION_V1,
) -> dict[str, Any]:
    """Build + sign a passport. Returns the passport dict ready
    for json.dump or to_string.

    The mint function does NOT consult any external state —
    callers resolve the operator master keypair + the agent's
    public key + the desired authorized fingerprints + expiry
    BEFORE calling.

    Validation:

    - ``agent_dna`` + ``instance_id`` must be non-empty strings.
    - ``agent_public_key_b64`` + ``issuer_public_key_b64`` must
      decode to 32-byte ed25519 public-key blobs.
    - ``authorized_fingerprints`` must be a non-empty list of
      strings (at least the birth fingerprint).
    - ``operator_private_key`` must be 32 bytes (raw ed25519
      private).
    - ``issued_at`` / ``expires_at`` are RFC 3339 strings or None.

    Raises ``PassportFormatError`` on validation failure.
    """
    if not isinstance(agent_dna, str) or not agent_dna:
        raise PassportFormatError("agent_dna must be a non-empty string")
    if not isinstance(instance_id, str) or not instance_id:
        raise PassportFormatError("instance_id must be a non-empty string")
    if not isinstance(authorized_fingerprints, list) or not authorized_fingerprints:
        raise PassportFormatError(
            "authorized_fingerprints must be a non-empty list"
        )
    if not all(isinstance(f, str) and f for f in authorized_fingerprints):
        raise PassportFormatError(
            "authorized_fingerprints entries must be non-empty strings"
        )
    if not isinstance(operator_private_key, (bytes, bytearray)) or \
            len(operator_private_key) != 32:
        raise PassportFormatError(
            "operator_private_key must be 32 raw ed25519 bytes"
        )

    # Sanity-check the public-key blobs decode + are 32 bytes.
    try:
        agent_pub_bytes = base64.b64decode(
            agent_public_key_b64.encode("ascii"), validate=True,
        )
        if len(agent_pub_bytes) != 32:
            raise PassportFormatError(
                f"agent_public_key_b64 decodes to {len(agent_pub_bytes)} bytes; expected 32"
            )
    except (ValueError, TypeError) as e:
        raise PassportFormatError(
            f"agent_public_key_b64 is not valid base64: {e}"
        ) from e
    try:
        issuer_pub_bytes = base64.b64decode(
            issuer_public_key_b64.encode("ascii"), validate=True,
        )
        if len(issuer_pub_bytes) != 32:
            raise PassportFormatError(
                f"issuer_public_key_b64 decodes to {len(issuer_pub_bytes)} bytes; expected 32"
            )
    except (ValueError, TypeError) as e:
        raise PassportFormatError(
            f"issuer_public_key_b64 is not valid base64: {e}"
        ) from e

    # Verify issuer_public_key_b64 corresponds to operator_private_key
    # — catches the obvious "wrong key thread to wrong field" bug
    # before producing a passport that won't verify against the
    # advertised issuer.
    try:
        derived_pub = Ed25519PrivateKey.from_private_bytes(
            bytes(operator_private_key),
        ).public_key().public_bytes_raw()
    except Exception as e:
        raise PassportFormatError(
            f"operator_private_key is not a valid ed25519 private key: {e}"
        ) from e
    if derived_pub != issuer_pub_bytes:
        raise PassportFormatError(
            "operator_private_key does not match issuer_public_key_b64 — "
            "the advertised issuer wouldn't be able to verify the signature"
        )

    if issued_at is None:
        issued_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    body = {
        "version":                 version,
        "agent_dna":               agent_dna,
        "instance_id":             instance_id,
        "agent_public_key":        agent_public_key_b64,
        "authorized_fingerprints": list(authorized_fingerprints),
        "issued_at":               issued_at,
        "expires_at":              expires_at,
        "issuer_public_key":       issuer_public_key_b64,
    }

    canonical = _canonical_form(body)
    priv_obj = Ed25519PrivateKey.from_private_bytes(bytes(operator_private_key))
    sig_bytes = priv_obj.sign(canonical)

    body["signature"] = SIGNATURE_ALG_PREFIX + base64.b64encode(sig_bytes).decode("ascii")
    return body


# ---- verify ------------------------------------------------------------


def verify_passport(
    passport: dict[str, Any],
    *,
    trusted_issuer_pubkeys_b64: list[str],
    current_hardware_fingerprint: str,
    now_iso: str | None = None,
) -> tuple[bool, str]:
    """Validate a passport against a trust list + the current
    runtime context. Returns ``(valid, reason)``.

    The reason string is human-readable; a calling daemon can
    surface it to the operator as a quarantine diagnostic.

    Validation order:

    1. Shape — required fields present, signature parseable.
    2. Issuer trust — issuer_public_key must be in the trusted list.
    3. Cryptographic — ed25519 signature verifies against
       issuer_public_key over the canonical-form body.
    4. Expiry — expires_at must be in the future (when set).
    5. Hardware — current_hardware_fingerprint must be in
       authorized_fingerprints.

    Any failure short-circuits with ``(False, "<reason>")``. All
    checks passing yields ``(True, "")``.
    """
    # Shape check.
    required = (
        "version", "agent_dna", "instance_id", "agent_public_key",
        "authorized_fingerprints", "issued_at",
        "issuer_public_key", "signature",
    )
    for k in required:
        if k not in passport:
            return False, f"passport missing required field {k!r}"
    if passport["version"] != PASSPORT_VERSION_V1:
        return False, (
            f"unsupported passport version {passport['version']}; "
            f"this verifier supports v{PASSPORT_VERSION_V1} only"
        )

    signature_field = passport["signature"]
    if not isinstance(signature_field, str) or \
            not signature_field.startswith(SIGNATURE_ALG_PREFIX):
        return False, (
            f"signature must start with {SIGNATURE_ALG_PREFIX!r}; "
            f"got {signature_field[:24]!r}"
        )

    issuer_pub_b64 = passport["issuer_public_key"]

    # Trust check.
    if issuer_pub_b64 not in trusted_issuer_pubkeys_b64:
        return False, (
            f"issuer public key not in trusted list "
            f"({len(trusted_issuer_pubkeys_b64)} trusted keys configured)"
        )

    # Cryptographic check.
    try:
        sig_b64 = signature_field[len(SIGNATURE_ALG_PREFIX):]
        sig_bytes = base64.b64decode(sig_b64.encode("ascii"), validate=True)
    except (ValueError, TypeError) as e:
        return False, f"signature is not valid base64: {e}"
    try:
        issuer_pub_bytes = base64.b64decode(
            issuer_pub_b64.encode("ascii"), validate=True,
        )
        if len(issuer_pub_bytes) != 32:
            return False, (
                f"issuer_public_key decodes to {len(issuer_pub_bytes)} bytes; "
                f"expected 32"
            )
    except (ValueError, TypeError) as e:
        return False, f"issuer_public_key is not valid base64: {e}"

    body_without_sig = {k: v for k, v in passport.items() if k != "signature"}
    canonical = _canonical_form(body_without_sig)

    try:
        pub_obj = Ed25519PublicKey.from_public_bytes(issuer_pub_bytes)
        pub_obj.verify(sig_bytes, canonical)
    except InvalidSignature:
        return False, "ed25519 signature verification failed"
    except Exception as e:
        return False, f"signature verification raised: {e!r}"

    # Expiry check.
    expires_at = passport.get("expires_at")
    if expires_at is not None:
        now = now_iso or datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ",
        )
        # Lexical compare works because RFC 3339 / our ISO-Z
        # format is ordering-preserving for UTC strings.
        if now > expires_at:
            return False, f"passport expired at {expires_at} (now {now})"

    # Hardware-fingerprint check.
    authorized = passport["authorized_fingerprints"]
    if not isinstance(authorized, list):
        return False, "authorized_fingerprints must be a list"
    if current_hardware_fingerprint not in authorized:
        return False, (
            f"current hardware fingerprint {current_hardware_fingerprint!r} "
            f"not in passport's authorized list "
            f"({len(authorized)} fingerprints authorized)"
        )

    return True, ""
