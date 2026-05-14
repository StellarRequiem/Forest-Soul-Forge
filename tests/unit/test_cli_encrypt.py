"""ADR-0050 T8 (B275) — fsf encrypt CLI tests.

Covers the three subcommands:

  - status: surface inventory + safe read of plaintext + encrypted
    audit chain entries, soul dir counts, registry shape detection
  - decrypt-event: round-trip via real EncryptionConfig + decrypt
  - rotate-key: refuse-without-flag safety gate; happy-path
    rotation across audit chain + soul/const files (registry
    rotation requires sqlcipher3 wheel + is exercised via the
    integration phase, not here)

Heavy paths (full SQLCipher rotation, passphrase-backend rotation
refusal) are documented in the runbook and exercised manually —
unit tests cover the orchestration logic, not the SQLCipher
substrate (which has its own tests via test_registry_sqlcipher).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from forest_soul_forge.cli.encrypt_cmd import (
    _rotate_audit_chain,
    _rotate_soul_files,
    _run_decrypt_event,
    _run_rotate_key,
    _run_status,
    _summarize_audit_chain,
    _summarize_registry,
    _summarize_soul_dir,
)
from forest_soul_forge.core.at_rest_encryption import (
    EncryptionConfig,
    decrypt_text,
    encrypt_event_data,
    encrypt_text,
)


def _make_args(**kwargs):
    """Build an argparse.Namespace with the requested kwargs."""
    ns = argparse.Namespace()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


# ---------------------------------------------------------------------------
# Summarizer helpers
# ---------------------------------------------------------------------------


def test_summarize_audit_chain_counts_mixed(tmp_path):
    chain = tmp_path / "audit_chain.jsonl"
    cfg = EncryptionConfig(master_key=b"\xaa" * 32, kid="t")
    entries = [
        {"seq": 1, "event_data": {"hi": 1}, "prev_hash": "GENESIS",
         "entry_hash": "x", "agent_dna": "d", "event_type": "t"},
        {"seq": 2, "event_data": {"hi": 2}, "prev_hash": "x",
         "entry_hash": "y", "agent_dna": "d", "event_type": "t"},
    ]
    # Encrypt the second entry.
    enc_entry = encrypt_event_data(entries[1], cfg)
    lines = [
        json.dumps(entries[0]),
        json.dumps(enc_entry),
        "{not valid json",  # malformed
    ]
    chain.write_text("\n".join(lines) + "\n")

    out = _summarize_audit_chain(chain)
    assert "total lines      = 3" in out
    assert "encrypted        = 1" in out
    assert "plaintext        = 1" in out
    assert "malformed        = 1" in out


def test_summarize_audit_chain_missing_file(tmp_path):
    out = _summarize_audit_chain(tmp_path / "nope.jsonl")
    assert "not found" in out


def test_summarize_soul_dir_counts(tmp_path):
    (tmp_path / "a.soul.md").write_text("plain")
    (tmp_path / "b.soul.md.enc").write_text("enc-body")
    (tmp_path / "a.constitution.yaml").write_text("plain")
    (tmp_path / "b.constitution.yaml.enc").write_text("enc-body")
    out = _summarize_soul_dir(tmp_path)
    assert "souls plaintext  = 1" in out
    assert "souls encrypted  = 1" in out
    assert "const plaintext  = 1" in out
    assert "const encrypted  = 1" in out


def test_summarize_registry_detects_plaintext(tmp_path):
    db = tmp_path / "registry.sqlite"
    db.write_bytes(b"SQLite format 3\x00" + b"\x00" * 16)
    out = _summarize_registry(db)
    assert "plaintext SQLite" in out


def test_summarize_registry_detects_opaque(tmp_path):
    db = tmp_path / "registry.sqlite"
    db.write_bytes(b"\xff" * 32)  # not SQLite magic — likely encrypted
    out = _summarize_registry(db)
    assert "opaque" in out


# ---------------------------------------------------------------------------
# status command — end-to-end (no key required when encryption off)
# ---------------------------------------------------------------------------


def test_run_status_encryption_off(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("FSF_AT_REST_ENCRYPTION", raising=False)
    chain = tmp_path / "audit_chain.jsonl"
    chain.write_text("")
    args = _make_args(
        data_dir=None,
        audit_chain_path=str(chain),
        registry_path=None,
        souls_dir=str(tmp_path / "souls"),  # missing — will report not-found
    )
    rc = _run_status(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "FSF_AT_REST_ENCRYPTION  = (unset)" in out
    assert "key resolution]  skipped" in out


# ---------------------------------------------------------------------------
# decrypt-event command
# ---------------------------------------------------------------------------


def test_decrypt_event_plaintext_entry(tmp_path, capsys, monkeypatch):
    """Plaintext entry → just print the event_data field;
    no master key required."""
    chain = tmp_path / "audit_chain.jsonl"
    chain.write_text(json.dumps({
        "seq": 42, "event_data": {"kind": "plaintext", "value": "ok"},
        "prev_hash": "GENESIS", "entry_hash": "x",
        "agent_dna": "d", "event_type": "e",
    }) + "\n")
    args = _make_args(seq=42, audit_chain_path=str(chain))
    rc = _run_decrypt_event(args)
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed == {"kind": "plaintext", "value": "ok"}


def test_decrypt_event_encrypted_entry(tmp_path, capsys, monkeypatch):
    """Encrypted entry under a known key → decrypts cleanly via
    resolve_master_key. Tests inject the key via the resolver's
    cache so we don't touch a real backend."""
    from forest_soul_forge.security import master_key as mk
    chain = tmp_path / "audit_chain.jsonl"
    cfg = EncryptionConfig(master_key=b"\xbb" * 32, kid="default")
    entry = encrypt_event_data({
        "seq": 7, "event_data": {"secret": "shhh"},
        "prev_hash": "GENESIS", "entry_hash": "x",
        "agent_dna": "d", "event_type": "e",
    }, cfg)
    chain.write_text(json.dumps(entry) + "\n")

    # Seed the resolver's cache so decrypt_event finds the key.
    mk.reset_cache()
    mk._CACHE[mk.MASTER_KEY_NAME] = b"\xbb" * 32  # noqa: SLF001 — test seam

    args = _make_args(seq=7, audit_chain_path=str(chain))
    rc = _run_decrypt_event(args)
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed == {"secret": "shhh"}
    mk.reset_cache()


def test_decrypt_event_seq_not_found(tmp_path, capsys):
    chain = tmp_path / "audit_chain.jsonl"
    chain.write_text(json.dumps({"seq": 1, "event_data": {}}) + "\n")
    args = _make_args(seq=999, audit_chain_path=str(chain))
    rc = _run_decrypt_event(args)
    assert rc == 1
    assert "no audit entry" in capsys.readouterr().err


def test_decrypt_event_missing_chain(tmp_path, capsys):
    args = _make_args(seq=1, audit_chain_path=str(tmp_path / "nope.jsonl"))
    rc = _run_decrypt_event(args)
    assert rc == 2
    assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# rotate-key safety gates
# ---------------------------------------------------------------------------


def test_rotate_key_refuses_without_confirm_flag(capsys):
    args = _make_args(
        confirm_daemon_stopped=False,
        data_dir=None, audit_chain_path=None,
        registry_path=None, souls_dir=None,
        backup_suffix=".pre-rotate",
    )
    rc = _run_rotate_key(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "REFUSED" in err
    assert "confirm-daemon-stopped" in err


# ---------------------------------------------------------------------------
# rotate-key surface helpers
# ---------------------------------------------------------------------------


def test_rotate_audit_chain_reencrypts_under_new_key(tmp_path):
    """Encrypted entries get rewritten under new key; plaintext
    entries stay byte-identical (chain integrity invariant)."""
    chain = tmp_path / "audit_chain.jsonl"
    old_key = b"\x11" * 32
    new_key = b"\x22" * 32
    old_cfg = EncryptionConfig(master_key=old_key)
    new_cfg = EncryptionConfig(master_key=new_key)

    plain_entry = {
        "seq": 1, "event_data": {"plain": True},
        "prev_hash": "GENESIS", "entry_hash": "x",
        "agent_dna": "d", "event_type": "e",
    }
    enc_entry = encrypt_event_data({
        "seq": 2, "event_data": {"secret": "yes"},
        "prev_hash": "x", "entry_hash": "y",
        "agent_dna": "d", "event_type": "e",
    }, old_cfg)
    chain.write_text(
        json.dumps(plain_entry) + "\n" + json.dumps(enc_entry) + "\n"
    )

    _rotate_audit_chain(chain, old_key, new_key, ".pre-rotate")

    # Backup exists and matches the pre-rotation file (modulo line
    # ordering — both copies have the same bytes after rotation
    # because shutil.copy2 happens before mutation).
    assert (tmp_path / "audit_chain.jsonl.pre-rotate").exists()

    # New chain: line 1 still plaintext, line 2 decryptable under new key.
    from forest_soul_forge.core.at_rest_encryption import (
        decrypt_event_data,
    )
    lines = chain.read_text().strip().splitlines()
    assert json.loads(lines[0])["event_data"] == {"plain": True}
    new_enc = json.loads(lines[1])
    assert decrypt_event_data(new_enc, new_cfg) == {"secret": "yes"}


def test_rotate_soul_files_reencrypts_enc_variants(tmp_path):
    """`.enc` files re-encrypt under new key; plaintext .md / .yaml
    files left alone."""
    old_key = b"\x33" * 32
    new_key = b"\x44" * 32
    old_cfg = EncryptionConfig(master_key=old_key)
    new_cfg = EncryptionConfig(master_key=new_key)

    plain = tmp_path / "alice.soul.md"
    plain.write_text("plaintext untouched")
    enc = tmp_path / "bob.soul.md.enc"
    enc.write_text(encrypt_text("bob-secret-body", old_cfg))
    const_enc = tmp_path / "bob.constitution.yaml.enc"
    const_enc.write_text(encrypt_text("agent:\n  name: bob\n", old_cfg))

    _rotate_soul_files(tmp_path, old_key, new_key, ".pre-rotate")

    # Plaintext file untouched.
    assert plain.read_text() == "plaintext untouched"
    # .enc files have backups.
    assert (tmp_path / "bob.soul.md.enc.pre-rotate").exists()
    assert (tmp_path / "bob.constitution.yaml.enc.pre-rotate").exists()
    # New ciphertext decrypts under new key.
    assert decrypt_text(enc.read_text(), new_cfg) == "bob-secret-body"
    assert decrypt_text(const_enc.read_text(), new_cfg) == (
        "agent:\n  name: bob\n"
    )
