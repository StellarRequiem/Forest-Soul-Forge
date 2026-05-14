"""ADR-0050 T5 (B271) — soul + constitution file encryption tests.

Covers the write/read/rollback/rewrite round-trip for the
``.soul.md.enc`` / ``.constitution.yaml.enc`` artifact pattern. Tests
that the on-disk variant is sticky (plaintext stays plaintext, encrypted
stays encrypted), that mixed deployments work per ADR Decision 6, and
that pre-T5 callers passing ``encryption_config=None`` get bit-identical
plaintext behavior.

What this does NOT cover (queued for B272 / T5b):
  - Dispatcher hot-path constitution reads (the ~10 sites in
    tools/dispatcher.py + tool_dispatch + conversation_helpers that
    still read constitution_path directly).
  - End-to-end /agents/{id}/tools/call against an encrypted agent.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from forest_soul_forge.core.at_rest_encryption import (
    EncryptionConfig,
)
from forest_soul_forge.daemon.routers.birth_pipeline import (
    read_constitution_yaml,
    read_soul_md,
    rollback_artifacts,
    write_artifacts,
    write_soul_md,
)
from forest_soul_forge.soul.voice_renderer import VoiceText, update_soul_voice


@pytest.fixture
def encryption_config() -> EncryptionConfig:
    """A fixed 32-byte key fixture. Same key for both write + read in
    each test — covers the happy path. Wrong-key + tampered cases
    live in test_audit_chain_encryption / test_memory_encryption."""
    return EncryptionConfig(master_key=b"\x01" * 32, kid="test-kid")


def _soul_md_minimal() -> str:
    """Minimal soul.md content with parseable frontmatter."""
    return (
        "---\n"
        "dna: aabbcc\n"
        "agent_name: test\n"
        "role: tester\n"
        "constitution_file: x.constitution.yaml\n"
        "narrative_provider: stub\n"
        "narrative_model: m\n"
        "narrative_generated_at: 2026-05-14T00:00:00Z\n"
        "---\n"
        "\n## Voice\n\nvoice body here\n"
    )


def _const_yaml_minimal() -> str:
    return "agent:\n  name: test\n  genre: research\n"


# ---------------------------------------------------------------------------
# write_artifacts
# ---------------------------------------------------------------------------
def test_write_artifacts_plaintext_default(tmp_path):
    """Without encryption_config: bit-identical pre-T5 behavior."""
    soul = tmp_path / "a.soul.md"
    const = tmp_path / "a.constitution.yaml"
    actual_soul, actual_const = write_artifacts(
        soul, "soul-body", const, "const-body",
    )
    assert actual_soul == soul
    assert actual_const == const
    assert soul.read_text() == "soul-body"
    assert const.read_text() == "const-body"
    # No .enc files were created.
    assert not (tmp_path / "a.soul.md.enc").exists()
    assert not (tmp_path / "a.constitution.yaml.enc").exists()


def test_write_artifacts_encrypted_lands_at_enc_paths(
    tmp_path, encryption_config
):
    """With encryption_config: writes go to .enc variants; the
    returned paths reflect what's actually on disk."""
    soul = tmp_path / "b.soul.md"
    const = tmp_path / "b.constitution.yaml"
    actual_soul, actual_const = write_artifacts(
        soul, "soul-body", const, "const-body",
        encryption_config=encryption_config,
    )
    assert actual_soul == tmp_path / "b.soul.md.enc"
    assert actual_const == tmp_path / "b.constitution.yaml.enc"
    # Plain paths absent; .enc paths exist.
    assert not soul.exists()
    assert not const.exists()
    assert actual_soul.exists()
    assert actual_const.exists()
    # On-disk content is NOT plaintext — should not contain
    # "soul-body" or "const-body" anywhere.
    assert "soul-body" not in actual_soul.read_text()
    assert "const-body" not in actual_const.read_text()


# ---------------------------------------------------------------------------
# read_soul_md / read_constitution_yaml round-trip
# ---------------------------------------------------------------------------
def test_read_soul_md_decrypts_enc_variant(tmp_path, encryption_config):
    """Encrypted write → encrypted-aware read recovers plaintext."""
    soul = tmp_path / "c.soul.md"
    const = tmp_path / "c.constitution.yaml"
    payload = "hello\nsoul\nwith\nnewlines and unicode 🌳"
    write_artifacts(
        soul, payload, const, "const",
        encryption_config=encryption_config,
    )
    recovered = read_soul_md(soul, encryption_config=encryption_config)
    assert recovered == payload


def test_read_constitution_yaml_decrypts_enc_variant(
    tmp_path, encryption_config
):
    soul = tmp_path / "d.soul.md"
    const = tmp_path / "d.constitution.yaml"
    payload = "agent:\n  name: 测试\n  list:\n    - a\n    - b\n"
    write_artifacts(
        soul, "s", const, payload,
        encryption_config=encryption_config,
    )
    recovered = read_constitution_yaml(
        const, encryption_config=encryption_config,
    )
    assert recovered == payload


def test_read_soul_md_plaintext_path_unchanged(tmp_path):
    """Plaintext write → plaintext read; no config required."""
    soul = tmp_path / "e.soul.md"
    soul.write_text("plain text body")
    assert read_soul_md(soul) == "plain text body"


def test_read_soul_md_enc_without_config_raises(
    tmp_path, encryption_config
):
    """Encrypted on disk + None config → explicit RuntimeError
    (not a cryptic base64 / decrypt failure deep in the stack)."""
    soul = tmp_path / "f.soul.md"
    const = tmp_path / "f.constitution.yaml"
    write_artifacts(
        soul, "x", const, "y",
        encryption_config=encryption_config,
    )
    with pytest.raises(RuntimeError, match="encrypted"):
        read_soul_md(soul, encryption_config=None)


# ---------------------------------------------------------------------------
# rollback unlinks both shapes
# ---------------------------------------------------------------------------
def test_rollback_clears_both_plain_and_enc(tmp_path, encryption_config):
    """Best-effort rollback should unlink whichever variant exists."""
    soul = tmp_path / "g.soul.md"
    const = tmp_path / "g.constitution.yaml"
    write_artifacts(
        soul, "x", const, "y",
        encryption_config=encryption_config,
    )
    enc_soul = tmp_path / "g.soul.md.enc"
    enc_const = tmp_path / "g.constitution.yaml.enc"
    assert enc_soul.exists() and enc_const.exists()
    rollback_artifacts(soul, const)
    assert not enc_soul.exists()
    assert not enc_const.exists()


def test_rollback_clears_plaintext_post_t5_still(tmp_path):
    """Plaintext rollback path (pre-T5 behavior) preserved."""
    soul = tmp_path / "h.soul.md"
    const = tmp_path / "h.constitution.yaml"
    soul.write_text("a")
    const.write_text("b")
    rollback_artifacts(soul, const)
    assert not soul.exists()
    assert not const.exists()


# ---------------------------------------------------------------------------
# voice rewrite preserves encryption variant
# ---------------------------------------------------------------------------
def test_voice_rewrite_keeps_encrypted_encrypted(
    tmp_path, encryption_config
):
    """update_soul_voice on an encrypted soul: decrypt → modify → re-encrypt.
    The on-disk variant stays .enc; plaintext shape never appears.
    Operator can't accidentally downgrade confidentiality via a voice rewrite.
    """
    soul = tmp_path / "i.soul.md"
    const = tmp_path / "i.constitution.yaml"
    write_artifacts(
        soul, _soul_md_minimal(), const, _const_yaml_minimal(),
        encryption_config=encryption_config,
    )
    enc_soul = tmp_path / "i.soul.md.enc"
    assert enc_soul.exists()
    assert not soul.exists()

    new_voice = VoiceText(
        provider="new-prov",
        model="new-model",
        generated_at="2026-05-14T01:02:03Z",
        markdown="totally new voice body",
    )
    update_soul_voice(soul, new_voice, encryption_config=encryption_config)

    # Still .enc, never gained a plaintext sibling.
    assert enc_soul.exists()
    assert not soul.exists()
    # Read back via the helper — confirm the rewrite landed correctly.
    rewritten = read_soul_md(soul, encryption_config=encryption_config)
    assert "totally new voice body" in rewritten
    assert "new-prov" in rewritten


def test_voice_rewrite_refuses_to_downgrade(tmp_path, encryption_config):
    """update_soul_voice on encrypted soul without config: raise rather
    than write plaintext over the encrypted file."""
    soul = tmp_path / "j.soul.md"
    const = tmp_path / "j.constitution.yaml"
    write_artifacts(
        soul, _soul_md_minimal(), const, _const_yaml_minimal(),
        encryption_config=encryption_config,
    )
    with pytest.raises(RuntimeError, match="encrypted"):
        update_soul_voice(soul, VoiceText(
            provider="p", model="m",
            generated_at="2026-05-14T00:00:00Z",
            markdown="body",
        ), encryption_config=None)
