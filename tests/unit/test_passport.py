"""ADR-0061 T2 + T3 (Burst 246) — passport mint + verify tests.

Coverage:
- mint round-trip: minted passport verifies under correct trust
  list + correct hardware fingerprint.
- mint input validation: empty fields, wrong key lengths, key
  mismatch.
- verify shape errors: missing required fields, wrong version,
  malformed signature prefix.
- verify trust failures: untrusted issuer.
- verify cryptographic failures: tampered signature, tampered
  body, wrong issuer pubkey.
- verify expiry: expired passport refused.
- verify hardware: wrong fingerprint refused.
"""
from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from cryptography.hazmat.primitives import serialization

from forest_soul_forge.security.passport import (
    PASSPORT_VERSION_V1,
    PassportFormatError,
    mint_passport,
    verify_passport,
)


# ---- helpers ------------------------------------------------------------


def _keypair_bytes() -> tuple[bytes, bytes, str]:
    """Generate a fresh ed25519 keypair. Returns
    (priv_bytes, pub_bytes, pub_b64)."""
    priv = Ed25519PrivateKey.generate()
    priv_bytes = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_bytes = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    pub_b64 = base64.b64encode(pub_bytes).decode("ascii")
    return priv_bytes, pub_bytes, pub_b64


def _mint_sample(
    *,
    expires_at: str | None = None,
    authorized: list[str] | None = None,
) -> tuple[dict, str, str]:
    """Mint a passport. Returns (passport_dict, issuer_pub_b64,
    fingerprint_used). Convenience for assertion-heavy tests."""
    op_priv, _, op_pub_b64 = _keypair_bytes()
    _, _, agent_pub_b64 = _keypair_bytes()
    fp = "fp_home_machine"
    authorized = authorized or [fp]
    passport = mint_passport(
        agent_dna="abc123",
        instance_id="operator_companion_abc123",
        agent_public_key_b64=agent_pub_b64,
        authorized_fingerprints=authorized,
        operator_private_key=op_priv,
        issuer_public_key_b64=op_pub_b64,
        expires_at=expires_at,
    )
    return passport, op_pub_b64, fp


# ---- mint round-trip ----------------------------------------------------


def test_mint_then_verify_happy_path():
    """A minted passport verifies under the matching trust list
    + matching hardware fingerprint."""
    passport, op_pub_b64, fp = _mint_sample()
    valid, reason = verify_passport(
        passport,
        trusted_issuer_pubkeys_b64=[op_pub_b64],
        current_hardware_fingerprint=fp,
    )
    assert valid, reason


def test_minted_passport_has_expected_fields():
    """Lock the on-disk shape — receivers' parsers depend on
    these exact field names."""
    passport, op_pub_b64, _ = _mint_sample()
    expected_keys = {
        "version", "agent_dna", "instance_id", "agent_public_key",
        "authorized_fingerprints", "issued_at", "expires_at",
        "issuer_public_key", "signature",
    }
    assert set(passport.keys()) == expected_keys
    assert passport["version"] == PASSPORT_VERSION_V1
    assert passport["agent_dna"] == "abc123"
    assert passport["issuer_public_key"] == op_pub_b64
    assert passport["signature"].startswith("ed25519:")


def test_minted_passport_round_trips_through_json():
    """Operators write passports to disk + read them back; the
    JSON round-trip must be lossless for verification to keep
    passing."""
    passport, op_pub_b64, fp = _mint_sample()
    s = json.dumps(passport)
    reparsed = json.loads(s)
    valid, reason = verify_passport(
        reparsed,
        trusted_issuer_pubkeys_b64=[op_pub_b64],
        current_hardware_fingerprint=fp,
    )
    assert valid, reason


# ---- mint input validation ----------------------------------------------


def test_mint_rejects_empty_agent_dna():
    op_priv, _, op_pub_b64 = _keypair_bytes()
    _, _, agent_pub_b64 = _keypair_bytes()
    with pytest.raises(PassportFormatError, match="agent_dna"):
        mint_passport(
            agent_dna="",
            instance_id="x",
            agent_public_key_b64=agent_pub_b64,
            authorized_fingerprints=["fp"],
            operator_private_key=op_priv,
            issuer_public_key_b64=op_pub_b64,
        )


def test_mint_rejects_empty_fingerprint_list():
    op_priv, _, op_pub_b64 = _keypair_bytes()
    _, _, agent_pub_b64 = _keypair_bytes()
    with pytest.raises(PassportFormatError, match="authorized_fingerprints"):
        mint_passport(
            agent_dna="abc",
            instance_id="x",
            agent_public_key_b64=agent_pub_b64,
            authorized_fingerprints=[],
            operator_private_key=op_priv,
            issuer_public_key_b64=op_pub_b64,
        )


def test_mint_rejects_wrong_private_key_length():
    _, _, op_pub_b64 = _keypair_bytes()
    _, _, agent_pub_b64 = _keypair_bytes()
    with pytest.raises(PassportFormatError, match="operator_private_key"):
        mint_passport(
            agent_dna="abc",
            instance_id="x",
            agent_public_key_b64=agent_pub_b64,
            authorized_fingerprints=["fp"],
            operator_private_key=b"only-16-bytes!!!",
            issuer_public_key_b64=op_pub_b64,
        )


def test_mint_rejects_private_pub_mismatch():
    """If the supplied operator_private_key doesn't derive to
    the supplied issuer_public_key_b64, mint refuses — catches
    the obvious 'wrong key threaded' bug before producing an
    un-verifiable passport."""
    op_priv_a, _, _ = _keypair_bytes()
    _, _, op_pub_b_b64 = _keypair_bytes()  # different keypair
    _, _, agent_pub_b64 = _keypair_bytes()
    with pytest.raises(PassportFormatError, match="does not match"):
        mint_passport(
            agent_dna="abc",
            instance_id="x",
            agent_public_key_b64=agent_pub_b64,
            authorized_fingerprints=["fp"],
            operator_private_key=op_priv_a,
            issuer_public_key_b64=op_pub_b_b64,
        )


def test_mint_rejects_non_base64_agent_pubkey():
    op_priv, _, op_pub_b64 = _keypair_bytes()
    with pytest.raises(PassportFormatError, match="agent_public_key"):
        mint_passport(
            agent_dna="abc",
            instance_id="x",
            agent_public_key_b64="not-base64!!!",
            authorized_fingerprints=["fp"],
            operator_private_key=op_priv,
            issuer_public_key_b64=op_pub_b64,
        )


# ---- verify shape errors ------------------------------------------------


def test_verify_refuses_missing_field():
    passport, op_pub_b64, fp = _mint_sample()
    del passport["agent_dna"]
    valid, reason = verify_passport(
        passport,
        trusted_issuer_pubkeys_b64=[op_pub_b64],
        current_hardware_fingerprint=fp,
    )
    assert not valid
    assert "agent_dna" in reason


def test_verify_refuses_unsupported_version():
    passport, op_pub_b64, fp = _mint_sample()
    passport["version"] = 99
    valid, reason = verify_passport(
        passport,
        trusted_issuer_pubkeys_b64=[op_pub_b64],
        current_hardware_fingerprint=fp,
    )
    assert not valid
    assert "version" in reason.lower()


def test_verify_refuses_malformed_signature_prefix():
    passport, op_pub_b64, fp = _mint_sample()
    passport["signature"] = "sphincs+:bogus"
    valid, reason = verify_passport(
        passport,
        trusted_issuer_pubkeys_b64=[op_pub_b64],
        current_hardware_fingerprint=fp,
    )
    assert not valid
    assert "ed25519" in reason


# ---- verify trust failures ----------------------------------------------


def test_verify_refuses_untrusted_issuer():
    """Even with a valid signature, issuer not in trusted list
    means refuse. This is the TLS-root-CA trust model: signature
    correctness is necessary but not sufficient."""
    passport, _, fp = _mint_sample()
    # Trust list is empty.
    valid, reason = verify_passport(
        passport,
        trusted_issuer_pubkeys_b64=[],
        current_hardware_fingerprint=fp,
    )
    assert not valid
    assert "trusted list" in reason


# ---- verify cryptographic failures --------------------------------------


def test_verify_refuses_tampered_signature():
    passport, op_pub_b64, fp = _mint_sample()
    # Swap signature with another keypair's signature over the
    # same body — same byte length, different key, won't verify.
    other_priv = Ed25519PrivateKey.generate()
    canonical = json.dumps(
        {k: v for k, v in passport.items() if k != "signature"},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    bad_sig = other_priv.sign(canonical)
    passport["signature"] = "ed25519:" + base64.b64encode(bad_sig).decode("ascii")
    valid, reason = verify_passport(
        passport,
        trusted_issuer_pubkeys_b64=[op_pub_b64],
        current_hardware_fingerprint=fp,
    )
    assert not valid
    assert "signature" in reason.lower()


def test_verify_refuses_tampered_body():
    """Modifying any signed field invalidates the signature."""
    passport, op_pub_b64, fp = _mint_sample()
    passport["agent_dna"] = "tampered_dna"
    valid, reason = verify_passport(
        passport,
        trusted_issuer_pubkeys_b64=[op_pub_b64],
        current_hardware_fingerprint=fp,
    )
    assert not valid
    assert "signature" in reason.lower()


# ---- verify expiry -------------------------------------------------------


def test_verify_refuses_expired_passport():
    passport, op_pub_b64, fp = _mint_sample(
        expires_at="2026-01-01T00:00:00Z",  # already in the past
    )
    valid, reason = verify_passport(
        passport,
        trusted_issuer_pubkeys_b64=[op_pub_b64],
        current_hardware_fingerprint=fp,
        now_iso="2026-05-12T22:00:00Z",
    )
    assert not valid
    assert "expired" in reason


def test_verify_accepts_unexpired_passport():
    passport, op_pub_b64, fp = _mint_sample(
        expires_at="2030-01-01T00:00:00Z",  # well in the future
    )
    valid, reason = verify_passport(
        passport,
        trusted_issuer_pubkeys_b64=[op_pub_b64],
        current_hardware_fingerprint=fp,
        now_iso="2026-05-12T22:00:00Z",
    )
    assert valid, reason


def test_verify_accepts_passport_without_expires():
    """expires_at None means no expiration; passports without
    expiry verify forever (until issuer revokes via trust-list
    removal)."""
    passport, op_pub_b64, fp = _mint_sample(expires_at=None)
    valid, reason = verify_passport(
        passport,
        trusted_issuer_pubkeys_b64=[op_pub_b64],
        current_hardware_fingerprint=fp,
        now_iso="2030-01-01T00:00:00Z",
    )
    assert valid, reason


# ---- verify hardware -----------------------------------------------------


def test_verify_refuses_wrong_hardware_fingerprint():
    passport, op_pub_b64, _ = _mint_sample()
    valid, reason = verify_passport(
        passport,
        trusted_issuer_pubkeys_b64=[op_pub_b64],
        current_hardware_fingerprint="fp_attacker_machine",
    )
    assert not valid
    assert "hardware fingerprint" in reason


def test_verify_accepts_secondary_authorized_fingerprint():
    """A passport authorized for laptop + desktop validates on
    either machine. Covers the canonical roaming case."""
    passport, op_pub_b64, _ = _mint_sample(
        authorized=["fp_desktop", "fp_laptop"],
    )
    valid_desktop, _ = verify_passport(
        passport,
        trusted_issuer_pubkeys_b64=[op_pub_b64],
        current_hardware_fingerprint="fp_desktop",
    )
    valid_laptop, _ = verify_passport(
        passport,
        trusted_issuer_pubkeys_b64=[op_pub_b64],
        current_hardware_fingerprint="fp_laptop",
    )
    assert valid_desktop
    assert valid_laptop
