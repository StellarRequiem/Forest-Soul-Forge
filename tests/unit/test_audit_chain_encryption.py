"""ADR-0050 T3 (B268) — audit-chain per-event encryption tests.

The encryption substrate lives in core/at_rest_encryption.py; this
file exercises the AuditChain integration:

  - Encrypted entries round-trip (encrypt-on-write,
    decrypt-on-read) producing the same plaintext event_data.
  - Hash chain integrity holds: entry_hash is computed over
    plaintext, decrypting reproduces the plaintext exactly, so
    verify() still passes.
  - Mixed legacy+encrypted chains: pre-T3 plaintext entries
    coexist with T3 encrypted entries on the same chain file.
  - Per-entry envelope shape on disk matches ADR Decision 3
    (alg/kid/nonce/ct, no event_data field).
  - Tampered ciphertext raises a clean AuditChainError on read.
  - Wrong key raises a clean AuditChainError on read.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from forest_soul_forge.core.audit_chain import (
    AuditChain,
    AuditChainError,
)
from forest_soul_forge.core.at_rest_encryption import (
    DEFAULT_KID,
    EncryptionConfig,
    decrypt_event_data,
    encrypt_event_data,
    is_encrypted_entry,
)


def _make_key(byte: int = 0xAB) -> bytes:
    return bytes([byte]) * 32


# ---- crypto helpers (round-trip + integrity) ----


class TestEncryptDecryptRoundTrip:
    """The encrypt_event_data / decrypt_event_data primitives must
    round-trip arbitrary JSON-shaped event_data losslessly. These
    tests don't touch the AuditChain — they're the substrate."""

    def test_simple_round_trip(self):
        cfg = EncryptionConfig(master_key=_make_key(0xAB))
        original = {"tool_key": "shell_exec.v1", "args_digest": "f00"}
        envelope = encrypt_event_data(original, cfg)
        assert envelope["alg"] == "AES-256-GCM"
        assert envelope["kid"] == "master:default"
        # Nonce + ct are base64-encoded.
        assert base64.b64decode(envelope["nonce"].encode("ascii"))
        assert base64.b64decode(envelope["ct"].encode("ascii"))
        decrypted = decrypt_event_data(envelope, cfg)
        assert decrypted == original

    def test_round_trip_with_nested_structures(self):
        cfg = EncryptionConfig(master_key=_make_key(0xCD))
        original = {
            "tool_key": "memory_recall.v1",
            "result_digest": "abc123",
            "metadata": {
                "matches": [{"id": 1, "score": 0.9}, {"id": 2, "score": 0.8}],
                "tokens_used": 42,
            },
        }
        envelope = encrypt_event_data(original, cfg)
        assert decrypt_event_data(envelope, cfg) == original

    def test_unique_nonces_per_call(self):
        """Two encrypts of the same plaintext under the same key must
        produce different ciphertexts (random nonce). This is the
        core safety property of AES-GCM — reusing a nonce under the
        same key is catastrophic."""
        cfg = EncryptionConfig(master_key=_make_key(0xEF))
        data = {"x": 1}
        env_a = encrypt_event_data(data, cfg)
        env_b = encrypt_event_data(data, cfg)
        assert env_a["nonce"] != env_b["nonce"]
        assert env_a["ct"] != env_b["ct"]


class TestDecryptFailureShapes:
    """Tampered ciphertext, wrong key, wrong kid — each surfaces a
    distinct DecryptError so an operator hunting an integrity issue
    can tell what went wrong."""

    def test_tampered_ciphertext_raises(self):
        cfg = EncryptionConfig(master_key=_make_key(0xAB))
        envelope = encrypt_event_data({"x": 1}, cfg)
        # Flip a byte in the ciphertext.
        raw_ct = bytearray(base64.b64decode(envelope["ct"].encode("ascii")))
        raw_ct[0] ^= 0x01
        envelope["ct"] = base64.b64encode(bytes(raw_ct)).decode("ascii")
        from forest_soul_forge.core.at_rest_encryption import DecryptError
        with pytest.raises(DecryptError):
            decrypt_event_data(envelope, cfg)

    def test_wrong_key_raises(self):
        cfg_a = EncryptionConfig(master_key=_make_key(0xAB))
        cfg_b = EncryptionConfig(master_key=_make_key(0xCD))
        envelope = encrypt_event_data({"x": 1}, cfg_a)
        from forest_soul_forge.core.at_rest_encryption import DecryptError
        with pytest.raises(DecryptError):
            decrypt_event_data(envelope, cfg_b)

    def test_unknown_kid_raises(self):
        cfg = EncryptionConfig(master_key=_make_key(0xAB))
        envelope = encrypt_event_data({"x": 1}, cfg)
        envelope["kid"] = "master:rotated-2026-06"
        from forest_soul_forge.core.at_rest_encryption import DecryptError
        with pytest.raises(DecryptError) as ei:
            decrypt_event_data(envelope, cfg)
        assert "rotation" in str(ei.value).lower() or "kid" in str(ei.value).lower()

    def test_unsupported_alg_raises(self):
        """A future ADR may switch to ChaCha20-Poly1305. T3 readers
        refuse cleanly rather than silently corrupting."""
        cfg = EncryptionConfig(master_key=_make_key(0xAB))
        envelope = encrypt_event_data({"x": 1}, cfg)
        envelope["alg"] = "ChaCha20-Poly1305"
        from forest_soul_forge.core.at_rest_encryption import DecryptError
        with pytest.raises(DecryptError):
            decrypt_event_data(envelope, cfg)


# ---- AuditChain integration ----


class TestAuditChainEncryption:
    """The end-to-end story: AuditChain configured with encryption,
    appends, reads back the SAME plaintext event_data."""

    def test_append_writes_envelope_not_plaintext_on_disk(self, tmp_path: Path):
        chain = AuditChain(tmp_path / "chain.jsonl")
        chain.set_encryption(EncryptionConfig(master_key=_make_key(0xAB)))
        chain.append("tool_call_dispatched", {"tool_key": "shell_exec.v1"})

        # Read raw lines — the genesis stays plaintext (written before
        # set_encryption), the appended entry has the envelope.
        raw_lines = (tmp_path / "chain.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(raw_lines) == 2
        genesis = json.loads(raw_lines[0])
        assert "event_data" in genesis  # plaintext
        assert "encryption" not in genesis
        appended = json.loads(raw_lines[1])
        assert is_encrypted_entry(appended)
        assert "event_data" not in appended
        assert appended["encryption"]["alg"] == "AES-256-GCM"
        assert appended["encryption"]["kid"] == DEFAULT_KID

    def test_round_trip_plaintext_recovered_on_read(self, tmp_path: Path):
        chain = AuditChain(tmp_path / "chain.jsonl")
        chain.set_encryption(EncryptionConfig(master_key=_make_key(0xAB)))
        chain.append("tool_call_dispatched", {"tool_key": "x.v1", "n": 42})
        chain.append("tool_call_succeeded", {"tool_key": "x.v1", "ok": True})

        # read_all goes through _entry_from_dict which decrypts.
        all_entries = chain.read_all()
        assert all_entries[-2].event_type == "tool_call_dispatched"
        assert all_entries[-2].event_data == {"tool_key": "x.v1", "n": 42}
        assert all_entries[-1].event_type == "tool_call_succeeded"
        assert all_entries[-1].event_data == {"tool_key": "x.v1", "ok": True}

    def test_hash_chain_verify_passes_under_encryption(self, tmp_path: Path):
        """ADR-0050 invariant: entry_hash is computed over plaintext.
        Encryption preserves hash-chain verify because decrypt reproduces
        the same plaintext that was hashed."""
        chain = AuditChain(tmp_path / "chain.jsonl")
        chain.set_encryption(EncryptionConfig(master_key=_make_key(0xAB)))
        for i in range(3):
            chain.append("tool_call_dispatched", {"i": i})

        # Fresh chain instance over the same file — encryption-aware
        # read path.
        chain2 = AuditChain(tmp_path / "chain.jsonl")
        chain2.set_encryption(EncryptionConfig(master_key=_make_key(0xAB)))
        result = chain2.verify()
        assert result.ok is True

    def test_mixed_legacy_and_encrypted_chain_reads(self, tmp_path: Path):
        """Pre-ADR-0050 entries (plaintext event_data) coexist with
        post-T3 encrypted entries. Reader handles both. This is the
        operator path: existing daemons turn on FSF_AT_REST_ENCRYPTION,
        new entries land encrypted, old entries stay plaintext, the
        whole chain stays readable + verifiable."""
        # Stage 1: chain runs WITHOUT encryption, accumulates entries.
        path = tmp_path / "chain.jsonl"
        chain1 = AuditChain(path)
        chain1.append("agent_created", {"role": "x"})
        chain1.append("tool_call_dispatched", {"tool_key": "y.v1"})

        # Stage 2: same chain re-opened WITH encryption, new appends
        # land encrypted on top of the plaintext history.
        chain2 = AuditChain(path)
        chain2.set_encryption(EncryptionConfig(master_key=_make_key(0xCD)))
        chain2.append("tool_call_succeeded", {"tool_key": "y.v1"})

        # Reader sees all four entries (genesis + 2 plaintext + 1 encrypted).
        all_entries = chain2.read_all()
        assert len(all_entries) == 4
        # Plaintext entries are unaffected.
        assert all_entries[1].event_type == "agent_created"
        assert all_entries[1].event_data == {"role": "x"}
        # Encrypted entry decrypts cleanly.
        assert all_entries[3].event_type == "tool_call_succeeded"
        assert all_entries[3].event_data == {"tool_key": "y.v1"}

    def test_encrypted_chain_read_without_config_raises(self, tmp_path: Path):
        """An AuditChain whose on-disk file contains encrypted entries
        MUST have set_encryption() called before reads. A missing
        config raises a clean AuditChainError rather than silently
        skipping the encrypted lines."""
        path = tmp_path / "chain.jsonl"
        chain1 = AuditChain(path)
        chain1.set_encryption(EncryptionConfig(master_key=_make_key(0xAB)))
        chain1.append("tool_call_dispatched", {"tool_key": "x.v1"})

        # Open a fresh instance WITHOUT calling set_encryption.
        chain2 = AuditChain(path)
        with pytest.raises(AuditChainError) as ei:
            chain2.read_all()
        assert "encrypted" in str(ei.value).lower()

    def test_wrong_key_on_read_surfaces_clean_error(self, tmp_path: Path):
        path = tmp_path / "chain.jsonl"
        chain1 = AuditChain(path)
        chain1.set_encryption(EncryptionConfig(master_key=_make_key(0xAB)))
        chain1.append("tool_call_dispatched", {"tool_key": "x.v1"})

        # Re-open with the wrong key — read must refuse, not silently
        # corrupt downstream consumers.
        chain2 = AuditChain(path)
        chain2.set_encryption(EncryptionConfig(master_key=_make_key(0xCD)))
        with pytest.raises(AuditChainError) as ei:
            chain2.read_all()
        assert "decrypt" in str(ei.value).lower()
