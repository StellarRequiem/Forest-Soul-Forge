"""ADR-0061 T4 (Burst 247) — passport bypassing K6 quarantine.

Integration tests for `_hardware_quarantine_reason` extended to
consult `passport.json` on hardware-binding mismatch.

Strategy: build a fake constitution.yaml with a `hardware_binding`
that mismatches the current machine's fingerprint, then drop a
passport.json next to it. Vary the passport content (valid /
expired / wrong-fp / tampered / untrusted-issuer) and assert the
quarantine outcome.

Trust list is supplied via FSF_TRUSTED_OPERATOR_KEYS env var
pointing at a tmpdir file.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from cryptography.hazmat.primitives import serialization

from forest_soul_forge.security.passport import mint_passport
from forest_soul_forge.security.trust_list import (
    ENV_VAR,
    load_trusted_operator_pubkeys,
    reset_cache as reset_trust_cache,
)
from forest_soul_forge.security.operator_key import (
    reset_cache as reset_op_cache,
)


# ---- helpers ------------------------------------------------------------


def _keypair_bytes() -> tuple[bytes, str]:
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
    return priv_bytes, pub_b64


def _write_constitution(
    dir_: Path,
    *,
    binding_fp: str,
) -> Path:
    """Write a minimal constitution.yaml with the named hardware
    binding. Returns the path."""
    path = dir_ / "constitution.yaml"
    path.write_text(
        f"# minimal test constitution\n"
        f"hardware_binding:\n"
        f"  fingerprint: {binding_fp}\n"
        f"  source: test\n"
        f"  bound_at: 2026-05-12T00:00:00Z\n",
        encoding="utf-8",
    )
    return path


def _write_trust_file(dir_: Path, pubkeys_b64: list[str]) -> Path:
    """Write a trust-list file with one pubkey per line + a
    test comment. Returns the path."""
    path = dir_ / "trusted_operators.txt"
    body = "# test trust list\n" + "\n".join(pubkeys_b64) + "\n"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture
def fake_hw():
    """Patch compute_hardware_fingerprint to return a stable test
    value. Otherwise we'd be at the mercy of whatever machine the
    suite runs on."""
    from forest_soul_forge.core import hardware
    here = "fp_THIS_MACHINE"
    with patch.object(
        hardware, "compute_hardware_fingerprint",
        return_value=hardware.HardwareFingerprint(here, "test"),
    ):
        yield here


@pytest.fixture(autouse=True)
def _reset_caches():
    """Each test starts with a fresh trust + operator cache so
    env-var changes take effect."""
    reset_trust_cache()
    reset_op_cache()
    yield
    reset_trust_cache()
    reset_op_cache()


# ---- happy path: valid passport bypasses K6 -----------------------------


def test_valid_passport_bypasses_quarantine(tmp_path: Path, fake_hw, monkeypatch):
    from forest_soul_forge.tools.dispatcher import _hardware_quarantine_reason

    op_priv, op_pub = _keypair_bytes()
    _, agent_pub = _keypair_bytes()

    binding_fp = "fp_HOME_MACHINE"   # birth machine
    here = fake_hw                    # current machine, different

    const_path = _write_constitution(tmp_path, binding_fp=binding_fp)

    # Mint passport authorizing the current host.
    passport = mint_passport(
        agent_dna="abc",
        instance_id="agent_x",
        agent_public_key_b64=agent_pub,
        authorized_fingerprints=[binding_fp, here],
        operator_private_key=op_priv,
        issuer_public_key_b64=op_pub,
    )
    (tmp_path / "passport.json").write_text(json.dumps(passport))

    # Trust the operator.
    trust_path = _write_trust_file(tmp_path, [op_pub])
    monkeypatch.setenv(ENV_VAR, str(trust_path))
    reset_trust_cache()

    # Mock resolve_operator_keypair so the trust list doesn't try
    # to spin up a real keystore for the "local operator master".
    from forest_soul_forge.security import trust_list as _tl
    with patch.object(_tl, "resolve_operator_keypair",
                       side_effect=Exception("no local master in tests")):
        result = _hardware_quarantine_reason(const_path)
    assert result is None, f"expected no quarantine, got {result}"


# ---- failure: no passport, mismatch refused -----------------------------


def test_no_passport_keeps_quarantine(tmp_path: Path, fake_hw):
    from forest_soul_forge.tools.dispatcher import _hardware_quarantine_reason

    const_path = _write_constitution(tmp_path, binding_fp="fp_HOME_MACHINE")
    # No passport.json written.
    result = _hardware_quarantine_reason(const_path)
    assert result is not None
    assert result["binding"] == "fp_HOME_MACHINE"
    assert result["expected"] == fake_hw
    assert "passport_path" not in result


# ---- failure: tampered passport refused + reason surfaced ---------------


def test_tampered_passport_refused_with_reason(tmp_path: Path, fake_hw, monkeypatch):
    from forest_soul_forge.tools.dispatcher import _hardware_quarantine_reason

    op_priv, op_pub = _keypair_bytes()
    _, agent_pub = _keypair_bytes()
    binding_fp = "fp_HOME_MACHINE"

    const_path = _write_constitution(tmp_path, binding_fp=binding_fp)

    passport = mint_passport(
        agent_dna="abc",
        instance_id="agent_x",
        agent_public_key_b64=agent_pub,
        authorized_fingerprints=[binding_fp, fake_hw],
        operator_private_key=op_priv,
        issuer_public_key_b64=op_pub,
    )
    # Tamper: change agent_dna AFTER signing.
    passport["agent_dna"] = "tampered"
    (tmp_path / "passport.json").write_text(json.dumps(passport))

    trust_path = _write_trust_file(tmp_path, [op_pub])
    monkeypatch.setenv(ENV_VAR, str(trust_path))
    reset_trust_cache()

    from forest_soul_forge.security import trust_list as _tl
    with patch.object(_tl, "resolve_operator_keypair",
                       side_effect=Exception("no local in tests")):
        result = _hardware_quarantine_reason(const_path)

    assert result is not None
    assert "passport_path" in result
    assert "passport_reason" in result
    assert "signature" in result["passport_reason"].lower()


# ---- failure: passport for different machine refused -------------------


def test_passport_for_other_machine_refused(tmp_path: Path, fake_hw, monkeypatch):
    """The passport authorizes a DIFFERENT machine, not this one.
    Quarantine stays in effect."""
    from forest_soul_forge.tools.dispatcher import _hardware_quarantine_reason

    op_priv, op_pub = _keypair_bytes()
    _, agent_pub = _keypair_bytes()
    binding_fp = "fp_HOME_MACHINE"

    const_path = _write_constitution(tmp_path, binding_fp=binding_fp)

    passport = mint_passport(
        agent_dna="abc",
        instance_id="agent_x",
        agent_public_key_b64=agent_pub,
        # Only authorize home + a third machine; NOT fake_hw.
        authorized_fingerprints=[binding_fp, "fp_third_machine"],
        operator_private_key=op_priv,
        issuer_public_key_b64=op_pub,
    )
    (tmp_path / "passport.json").write_text(json.dumps(passport))

    trust_path = _write_trust_file(tmp_path, [op_pub])
    monkeypatch.setenv(ENV_VAR, str(trust_path))
    reset_trust_cache()

    from forest_soul_forge.security import trust_list as _tl
    with patch.object(_tl, "resolve_operator_keypair",
                       side_effect=Exception("no local in tests")):
        result = _hardware_quarantine_reason(const_path)

    assert result is not None
    assert "hardware fingerprint" in result.get("passport_reason", "")


# ---- failure: passport from untrusted issuer refused -------------------


def test_passport_from_untrusted_issuer_refused(tmp_path: Path, fake_hw, monkeypatch):
    from forest_soul_forge.tools.dispatcher import _hardware_quarantine_reason

    op_priv, op_pub = _keypair_bytes()
    _, agent_pub = _keypair_bytes()
    binding_fp = "fp_HOME_MACHINE"

    const_path = _write_constitution(tmp_path, binding_fp=binding_fp)

    passport = mint_passport(
        agent_dna="abc",
        instance_id="agent_x",
        agent_public_key_b64=agent_pub,
        authorized_fingerprints=[binding_fp, fake_hw],
        operator_private_key=op_priv,
        issuer_public_key_b64=op_pub,
    )
    (tmp_path / "passport.json").write_text(json.dumps(passport))

    # Trust list is EMPTY — operator never authorized this issuer.
    trust_path = _write_trust_file(tmp_path, [])
    monkeypatch.setenv(ENV_VAR, str(trust_path))
    reset_trust_cache()

    from forest_soul_forge.security import trust_list as _tl
    with patch.object(_tl, "resolve_operator_keypair",
                       side_effect=Exception("no local in tests")):
        result = _hardware_quarantine_reason(const_path)

    assert result is not None
    assert "trusted list" in result.get("passport_reason", "").lower()


# ---- failure: expired passport refused ---------------------------------


def test_expired_passport_refused(tmp_path: Path, fake_hw, monkeypatch):
    from forest_soul_forge.tools.dispatcher import _hardware_quarantine_reason

    op_priv, op_pub = _keypair_bytes()
    _, agent_pub = _keypair_bytes()
    binding_fp = "fp_HOME_MACHINE"

    const_path = _write_constitution(tmp_path, binding_fp=binding_fp)

    passport = mint_passport(
        agent_dna="abc",
        instance_id="agent_x",
        agent_public_key_b64=agent_pub,
        authorized_fingerprints=[binding_fp, fake_hw],
        operator_private_key=op_priv,
        issuer_public_key_b64=op_pub,
        expires_at="2020-01-01T00:00:00Z",  # well in the past
    )
    (tmp_path / "passport.json").write_text(json.dumps(passport))

    trust_path = _write_trust_file(tmp_path, [op_pub])
    monkeypatch.setenv(ENV_VAR, str(trust_path))
    reset_trust_cache()

    from forest_soul_forge.security import trust_list as _tl
    with patch.object(_tl, "resolve_operator_keypair",
                       side_effect=Exception("no local in tests")):
        result = _hardware_quarantine_reason(const_path)

    assert result is not None
    assert "expired" in result.get("passport_reason", "")


# ---- trust list loader basics -------------------------------------------


def test_trust_list_loads_file_entries(tmp_path: Path, monkeypatch):
    """File entries + comments work. Local pubkey appended when
    requested (mocked here to keep the test isolated from a
    real keystore)."""
    pk_a = base64.b64encode(b"a" * 32).decode("ascii")
    pk_b = base64.b64encode(b"b" * 32).decode("ascii")
    path = tmp_path / "trust.txt"
    path.write_text(f"# my friend operator A\n{pk_a}\n\n# operator B\n{pk_b}\n")

    monkeypatch.setenv(ENV_VAR, str(path))
    reset_trust_cache()

    from forest_soul_forge.security import trust_list as _tl
    with patch.object(_tl, "resolve_operator_keypair",
                       side_effect=Exception("no local in tests")):
        keys = load_trusted_operator_pubkeys()

    assert keys == [pk_a, pk_b]


def test_trust_list_dedupes(tmp_path: Path, monkeypatch):
    pk = base64.b64encode(b"x" * 32).decode("ascii")
    path = tmp_path / "trust.txt"
    path.write_text(f"{pk}\n{pk}\n{pk}\n")

    monkeypatch.setenv(ENV_VAR, str(path))
    reset_trust_cache()

    from forest_soul_forge.security import trust_list as _tl
    with patch.object(_tl, "resolve_operator_keypair",
                       side_effect=Exception("no local in tests")):
        keys = load_trusted_operator_pubkeys()
    assert keys == [pk]


def test_trust_list_missing_file_returns_just_local(tmp_path: Path, monkeypatch):
    """No trust file + mocked local key → just the local key."""
    pk_local = base64.b64encode(b"l" * 32).decode("ascii")
    nonexistent = tmp_path / "nope.txt"
    monkeypatch.setenv(ENV_VAR, str(nonexistent))
    reset_trust_cache()

    from forest_soul_forge.security import trust_list as _tl
    with patch.object(_tl, "resolve_operator_keypair",
                       return_value=(b"\x00" * 32, pk_local)):
        keys = load_trusted_operator_pubkeys()
    assert keys == [pk_local]


def test_trust_list_skips_local_when_disabled(tmp_path: Path, monkeypatch):
    """include_local=False omits the local operator's pubkey
    — used by test scaffolding where there's no real keystore."""
    pk_a = base64.b64encode(b"a" * 32).decode("ascii")
    path = tmp_path / "trust.txt"
    path.write_text(f"{pk_a}\n")
    monkeypatch.setenv(ENV_VAR, str(path))
    reset_trust_cache()

    keys = load_trusted_operator_pubkeys(include_local=False)
    assert keys == [pk_a]
