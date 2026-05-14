"""ADR-0050 T5b (B272) — dispatcher hot-path constitution reads
encryption-aware.

Verifies that the dispatcher's per-call constitution.yaml reads
decrypt the .enc variant transparently when master_key is wired:

  - _read_constitution_text round-trip on plain + encrypted shapes
  - failure-mode coverage (encrypted with no config → defensive None,
    not a crash)
  - the three module-level loaders (_load_initiative_level,
    _load_constitution_mcp_allowlist, _load_resolved_constraints)
    each work against encrypted constitutions when an encryption_config
    is supplied
  - the three "extended" helpers (_apply_provider_posture_overrides,
    _hardware_quarantine_reason, _reality_anchor_opt_out) likewise
    decrypt the .enc variant

Not in scope:
  - End-to-end /agents/{id}/tools/call against an encrypted agent
    (requires a full TestClient harness + birth flow under encryption;
    queued for the T6 / T7 / T8 integration phase).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from forest_soul_forge.core.at_rest_encryption import (
    EncryptionConfig,
    encrypt_text,
)
from forest_soul_forge.tools.dispatcher import (
    _apply_provider_posture_overrides,
    _hardware_quarantine_reason,
    _load_constitution_mcp_allowlist,
    _load_initiative_level,
    _load_resolved_constraints,
    _read_constitution_text,
    _reality_anchor_opt_out,
    _ResolvedToolConstraints,
)


@pytest.fixture
def encryption_config() -> EncryptionConfig:
    return EncryptionConfig(master_key=b"\x02" * 32, kid="t5b-kid")


def _write_encrypted_constitution(
    path: Path, yaml_text: str, config: EncryptionConfig
) -> Path:
    """Write the AES-256-GCM envelope to ``<path>.enc`` and return it.
    Caller passes the canonical (plaintext-named) path; this lands the
    encrypted bytes at the .enc sibling per ADR-0050 T5 conventions."""
    enc_path = path.with_name(path.name + ".enc")
    enc_path.write_text(encrypt_text(yaml_text, config), encoding="utf-8")
    return enc_path


# ---------------------------------------------------------------------------
# _read_constitution_text — the central helper
# ---------------------------------------------------------------------------
def test_read_constitution_text_plaintext(tmp_path):
    p = tmp_path / "agent.constitution.yaml"
    p.write_text("agent:\n  name: t\n")
    assert "name: t" in (_read_constitution_text(p) or "")


def test_read_constitution_text_encrypted(tmp_path, encryption_config):
    p = tmp_path / "agent.constitution.yaml"
    _write_encrypted_constitution(p, "agent:\n  name: enc\n", encryption_config)
    text = _read_constitution_text(p, encryption_config=encryption_config)
    assert text is not None
    assert "name: enc" in text


def test_read_constitution_text_encrypted_no_config_returns_none(
    tmp_path, encryption_config,
):
    """Encrypted on disk + None config → defensive None (caller
    falls back to its safe default like 'L5' or empty)."""
    p = tmp_path / "agent.constitution.yaml"
    _write_encrypted_constitution(p, "agent:\n  x: 1\n", encryption_config)
    assert _read_constitution_text(p, encryption_config=None) is None


def test_read_constitution_text_missing_both_returns_none(tmp_path):
    p = tmp_path / "agent.constitution.yaml"
    assert _read_constitution_text(p) is None


# ---------------------------------------------------------------------------
# Module-level loader helpers
# ---------------------------------------------------------------------------
def test_load_initiative_level_encrypted(tmp_path, encryption_config):
    p = tmp_path / "agent.constitution.yaml"
    _write_encrypted_constitution(
        p, "agent:\n  initiative_level: L3\n", encryption_config,
    )
    level = _load_initiative_level(p, encryption_config=encryption_config)
    assert level == "L3"


def test_load_initiative_level_encrypted_wrong_config_defaults_L5(
    tmp_path, encryption_config,
):
    """Encrypted on disk + None config → defaults to L5 (no
    initiative ceiling). Defensive — operator misconfig is recoverable
    on retry without leaking the agent's posture."""
    p = tmp_path / "agent.constitution.yaml"
    _write_encrypted_constitution(
        p, "agent:\n  initiative_level: L1\n", encryption_config,
    )
    level = _load_initiative_level(p, encryption_config=None)
    assert level == "L5"


def test_load_constitution_mcp_allowlist_encrypted(
    tmp_path, encryption_config,
):
    p = tmp_path / "agent.constitution.yaml"
    _write_encrypted_constitution(
        p,
        "allowed_mcp_servers:\n  - alpha\n  - beta\n  - gamma\n",
        encryption_config,
    )
    allow = _load_constitution_mcp_allowlist(
        p, encryption_config=encryption_config,
    )
    assert allow == ("alpha", "beta", "gamma")


def test_load_resolved_constraints_encrypted(tmp_path, encryption_config):
    p = tmp_path / "agent.constitution.yaml"
    _write_encrypted_constitution(
        p,
        "tools:\n"
        "  - name: shell_exec\n"
        "    version: v1\n"
        "    side_effects: external\n"
        "    constraints:\n"
        "      max_calls_per_session: 5\n",
        encryption_config,
    )
    resolved = _load_resolved_constraints(
        p, "shell_exec", "v1", encryption_config=encryption_config,
    )
    assert resolved is not None
    assert resolved.name == "shell_exec"
    assert resolved.constraints.get("max_calls_per_session") == 5


# ---------------------------------------------------------------------------
# Extended helpers (posture / quarantine / reality_anchor)
# ---------------------------------------------------------------------------
def test_apply_provider_posture_overrides_encrypted(
    tmp_path, encryption_config,
):
    p = tmp_path / "agent.constitution.yaml"
    _write_encrypted_constitution(
        p,
        "provider_posture_overrides:\n"
        "  qwen3.6:\n"
        "    requires_approval_filesystem: true\n",
        encryption_config,
    )
    base = _ResolvedToolConstraints(
        name="t", version="v1",
        side_effects="filesystem",
        constraints={"requires_human_approval": False},
        applied_rules=(),
    )
    resolved, notes = _apply_provider_posture_overrides(
        base, p, "qwen3.6",
        encryption_config=encryption_config,
    )
    assert any("provider_posture" in r for r in resolved.applied_rules)
    assert notes  # at least one override fired


def test_hardware_quarantine_reason_encrypted_no_binding_returns_none(
    tmp_path, encryption_config,
):
    """Encrypted constitution with no hardware_binding block — quarantine
    helper returns None (no quarantine needed), proving the decrypt
    happened and the YAML parsed cleanly."""
    p = tmp_path / "agent.constitution.yaml"
    _write_encrypted_constitution(
        p, "agent:\n  name: roamer\n", encryption_config,
    )
    result = _hardware_quarantine_reason(
        p, encryption_config=encryption_config,
    )
    assert result is None


def test_reality_anchor_opt_out_encrypted(tmp_path, encryption_config):
    """Encrypted constitution with explicit opt-out → True."""
    p = tmp_path / "agent.constitution.yaml"
    _write_encrypted_constitution(
        p,
        "reality_anchor:\n  enabled: false\n",
        encryption_config,
    )
    assert _reality_anchor_opt_out(
        p, encryption_config=encryption_config,
    ) is True


def test_reality_anchor_opt_out_encrypted_default_in(
    tmp_path, encryption_config,
):
    """Encrypted constitution with no reality_anchor block → False
    (gate opted IN per ADR-0063 D2 default)."""
    p = tmp_path / "agent.constitution.yaml"
    _write_encrypted_constitution(
        p, "agent:\n  name: x\n", encryption_config,
    )
    assert _reality_anchor_opt_out(
        p, encryption_config=encryption_config,
    ) is False


# ---------------------------------------------------------------------------
# Plaintext path bit-identical to pre-T5b
# ---------------------------------------------------------------------------
def test_all_loaders_plaintext_unchanged(tmp_path):
    """Without encryption_config + plaintext constitution: bit-identical
    pre-T5b behavior across all loaders. Operators not opting into
    encryption-at-rest see zero change."""
    p = tmp_path / "agent.constitution.yaml"
    p.write_text(
        "agent:\n  initiative_level: L2\n"
        "allowed_mcp_servers:\n  - srv\n"
        "tools:\n"
        "  - name: web_fetch\n"
        "    version: v1\n"
        "    side_effects: network\n"
        "    constraints: {}\n"
        "reality_anchor:\n  enabled: false\n"
    )
    assert _load_initiative_level(p) == "L2"
    assert _load_constitution_mcp_allowlist(p) == ("srv",)
    resolved = _load_resolved_constraints(p, "web_fetch", "v1")
    assert resolved is not None and resolved.name == "web_fetch"
    assert _reality_anchor_opt_out(p) is True
