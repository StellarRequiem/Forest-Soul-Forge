"""ADR-0050 T4 (B269) — memory body application-layer encryption.

Verifies that when ``Memory`` is constructed with an
``encryption_config``:

  - content is encrypted before INSERT
  - content_encrypted flag is set to 1
  - the on-disk content column is NOT plaintext (raw row read)
  - reads through Memory.recall / get decrypt transparently
  - the content_digest is stable across encrypted/plaintext shapes
    (computed over plaintext, both modes)

Mixed plaintext+encrypted entries on the same table coexist (the
operator opt-in mid-lifecycle scenario per ADR Decision 6).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from forest_soul_forge.core.at_rest_encryption import (
    DecryptError,
    EncryptionConfig,
    decrypt_text,
    encrypt_text,
)
from forest_soul_forge.core.memory import Memory
from forest_soul_forge.registry.registry import Registry


def _key(byte: int = 0xAB) -> bytes:
    return bytes([byte]) * 32


def _make_memory(tmp_path: Path, *, encrypted: bool = True) -> Memory:
    """Tmpdir-backed Memory with the schema fully migrated.

    Uses Registry.bootstrap so the v21 migration runs and the
    content_encrypted column exists. Encryption is opt-in via the
    ``encrypted`` flag; when False, the Memory instance runs in
    legacy plaintext mode and writes content_encrypted=0.
    """
    db = tmp_path / "test.sqlite"
    # Seed the registry, plaintext only. Tests for the encrypted
    # storage path use the resulting Memory wrapper; registry stays
    # plain SQLite so the test doesn't require sqlcipher3 installed.
    reg = Registry.bootstrap(db)
    cfg = EncryptionConfig(master_key=_key(0xAB)) if encrypted else None
    # Required: seed an agent row to satisfy the FK.
    reg.agents.register_birth(
        instance_id="ag1",
        dna="aaaaaaaaaaaa",
        role="experimenter",
        constitution_hash="c" * 64,
        soul_md="seed",
        created_at="2026-05-13T00:00:00Z",
        approved_by="test",
        constitution_yaml="agent: {role: experimenter}\n",
    )
    return Memory(conn=reg._conn, encryption_config=cfg)


# ---- encrypted-write round trip ----


class TestEncryptedWriteRoundTrip:
    def test_append_encrypts_content_on_disk(self, tmp_path: Path):
        mem = _make_memory(tmp_path, encrypted=True)
        entry = mem.append(
            instance_id="ag1", agent_dna="aaaaaaaaaaaa",
            content="secret memory body",
            layer="episodic",
        )
        # Raw row read — content is NOT the plaintext.
        row = mem.conn.execute(
            "SELECT content, content_encrypted FROM memory_entries WHERE entry_id=?;",
            (entry.entry_id,),
        ).fetchone()
        assert row["content"] != "secret memory body"
        assert int(row["content_encrypted"]) == 1

    def test_recall_decrypts_transparently(self, tmp_path: Path):
        mem = _make_memory(tmp_path, encrypted=True)
        mem.append(
            instance_id="ag1", agent_dna="aaaaaaaaaaaa",
            content="recovered through decrypt",
            layer="episodic",
        )
        entries = mem.recall(instance_id="ag1")
        assert len(entries) == 1
        assert entries[0].content == "recovered through decrypt"

    def test_get_decrypts_transparently(self, tmp_path: Path):
        mem = _make_memory(tmp_path, encrypted=True)
        e = mem.append(
            instance_id="ag1", agent_dna="aaaaaaaaaaaa",
            content="single-entry recall",
            layer="semantic",
        )
        got = mem.get(e.entry_id)
        assert got is not None
        assert got.content == "single-entry recall"

    def test_digest_computed_over_plaintext(self, tmp_path: Path):
        """ADR-0050 invariant: content_digest is over plaintext.
        Two Memory instances writing the same plaintext (one
        plaintext, one encrypted) produce the same digest — that's
        the stable identity property across encryption modes."""
        mem_clear = _make_memory(tmp_path / "clear", encrypted=False)
        mem_enc = _make_memory(tmp_path / "enc", encrypted=True)
        plain = "same content both modes"
        e_clear = mem_clear.append(
            instance_id="ag1", agent_dna="aaaaaaaaaaaa",
            content=plain, layer="episodic",
        )
        e_enc = mem_enc.append(
            instance_id="ag1", agent_dna="aaaaaaaaaaaa",
            content=plain, layer="episodic",
        )
        assert e_clear.content_digest == e_enc.content_digest


# ---- mixed plaintext + encrypted on same table ----


class TestMixedTable:
    """Operator opt-in mid-lifecycle: existing entries stay
    plaintext (content_encrypted=0), new entries land encrypted
    (content_encrypted=1). Both are reachable from the same
    Memory instance configured with the encryption key."""

    def test_legacy_row_reads_without_decrypt(self, tmp_path: Path):
        # Stage 1: plaintext writes.
        mem_plain = _make_memory(tmp_path, encrypted=False)
        mem_plain.append(
            instance_id="ag1", agent_dna="aaaaaaaaaaaa",
            content="legacy plaintext",
            layer="episodic",
        )

        # Stage 2: rebind same conn with encryption config.
        mem_enc = Memory(
            conn=mem_plain.conn,
            encryption_config=EncryptionConfig(master_key=_key(0xAB)),
        )
        mem_enc.append(
            instance_id="ag1", agent_dna="aaaaaaaaaaaa",
            content="new encrypted entry",
            layer="episodic",
        )

        # Both rows readable, in newest-first order.
        entries = mem_enc.recall(instance_id="ag1")
        contents = [e.content for e in entries]
        assert "legacy plaintext" in contents
        assert "new encrypted entry" in contents


# ---- failure-shape sanity ----


class TestEncryptedReadWithoutKey:
    """A Memory instance without encryption_config refusing to read
    an encrypted row — surfaces as RuntimeError, NOT silent
    base64 garbage."""

    def test_encrypted_row_read_without_config_raises(self, tmp_path: Path):
        mem_enc = _make_memory(tmp_path, encrypted=True)
        e = mem_enc.append(
            instance_id="ag1", agent_dna="aaaaaaaaaaaa",
            content="encrypted only",
            layer="episodic",
        )
        # Re-bind the same conn WITHOUT encryption_config — reader
        # should refuse loudly when it hits a flag=1 row.
        mem_noconfig = Memory(conn=mem_enc.conn, encryption_config=None)
        with pytest.raises(RuntimeError) as ei:
            mem_noconfig.get(e.entry_id)
        assert "encrypted" in str(ei.value).lower()

    def test_wrong_key_on_recall_raises_decrypt_error(self, tmp_path: Path):
        mem_enc = _make_memory(tmp_path, encrypted=True)
        mem_enc.append(
            instance_id="ag1", agent_dna="aaaaaaaaaaaa",
            content="under correct key",
            layer="episodic",
        )
        # Rebind with WRONG key — recall should raise DecryptError
        # (the row's flag=1 triggers decrypt_text which fails).
        mem_wrong = Memory(
            conn=mem_enc.conn,
            encryption_config=EncryptionConfig(master_key=_key(0xCD)),
        )
        with pytest.raises(DecryptError):
            mem_wrong.recall(instance_id="ag1")


# ---- string-level primitives substrate ----


class TestEncryptTextHelpers:
    def test_round_trip(self):
        cfg = EncryptionConfig(master_key=_key(0xAB))
        for original in ["short", "", "with unicode 日本語",
                         "x" * 5000, "newlines\nin\nbody"]:
            ct = encrypt_text(original, cfg)
            assert ct != original  # ciphertext is base64'd JSON
            assert decrypt_text(ct, cfg) == original

    def test_wrong_key_raises(self):
        cfg_a = EncryptionConfig(master_key=_key(0xAB))
        cfg_b = EncryptionConfig(master_key=_key(0xCD))
        ct = encrypt_text("payload", cfg_a)
        with pytest.raises(DecryptError):
            decrypt_text(ct, cfg_b)
