"""Unit tests for the per-agent encrypted secrets store.

Covers ADR-003X Phase C1: master key loader, encrypt/decrypt
round-trip, AAD enforcement, registry CRUD methods, and the
"subsystem disabled" path when no master key is configured.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# core.secrets — module-level helpers
# ---------------------------------------------------------------------------
class TestMasterKey:
    def test_load_returns_none_when_env_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FSF_SECRETS_MASTER_KEY", raising=False)
        from forest_soul_forge.core import secrets
        assert secrets.load_master_key() is None

    def test_load_returns_master_key_when_env_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from forest_soul_forge.core import secrets
        b64 = secrets.generate_master_key_b64()
        monkeypatch.setenv("FSF_SECRETS_MASTER_KEY", b64)
        mk = secrets.load_master_key()
        assert mk is not None
        assert len(mk.raw) == 32  # AES-256

    def test_load_rejects_bad_base64(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from forest_soul_forge.core import secrets
        monkeypatch.setenv("FSF_SECRETS_MASTER_KEY", "not_valid_base64!!!")
        with pytest.raises(secrets.SecretsKeyError):
            secrets.load_master_key()

    def test_load_rejects_wrong_length(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from forest_soul_forge.core import secrets
        # 16 bytes — valid base64 but not 32 bytes after decode.
        import base64
        monkeypatch.setenv(
            "FSF_SECRETS_MASTER_KEY",
            base64.urlsafe_b64encode(b"x" * 16).decode("ascii"),
        )
        with pytest.raises(secrets.SecretsKeyError):
            secrets.load_master_key()

    def test_repr_does_not_leak_bytes(self) -> None:
        from forest_soul_forge.core import secrets
        b64 = secrets.generate_master_key_b64()
        os.environ["FSF_SECRETS_MASTER_KEY"] = b64
        try:
            mk = secrets.load_master_key()
            assert "redacted" in repr(mk)
            # The raw bytes themselves should not appear in repr.
            assert mk.raw.hex()[:8] not in repr(mk)
        finally:
            os.environ.pop("FSF_SECRETS_MASTER_KEY", None)


class TestEncryptDecrypt:
    """Crypto round-trip + AAD pinning. The AAD is what makes a stolen
    ciphertext untransferable between (instance_id, name) pairs.
    """

    def _key(self):
        from forest_soul_forge.core import secrets
        return secrets.MasterKey(raw=b"k" * 32)

    def test_round_trip(self) -> None:
        from forest_soul_forge.core import secrets
        mk = self._key()
        aad = secrets.aad_for("inst1", "openai_key")
        ct, nonce = secrets.encrypt(mk, "sk-test-12345", associated=aad)
        assert secrets.decrypt(mk, ct, nonce, associated=aad) == "sk-test-12345"

    def test_aad_mismatch_raises(self) -> None:
        from forest_soul_forge.core import secrets
        mk = self._key()
        ct, nonce = secrets.encrypt(
            mk, "sk-test", associated=secrets.aad_for("inst1", "openai_key"),
        )
        # AAD pinned to a different (instance, name) — decrypt must fail.
        with pytest.raises(Exception):  # InvalidTag from cryptography
            secrets.decrypt(
                mk, ct, nonce, associated=secrets.aad_for("inst2", "openai_key"),
            )

    def test_aad_for_is_deterministic(self) -> None:
        from forest_soul_forge.core import secrets
        assert secrets.aad_for("inst1", "k") == secrets.aad_for("inst1", "k")
        assert secrets.aad_for("inst1", "k") != secrets.aad_for("inst2", "k")

    def test_nonce_is_unique_per_call(self) -> None:
        # Same plaintext + same AAD → different nonces. Otherwise an
        # attacker who sees the same ciphertext repeatedly can infer
        # value reuse.
        from forest_soul_forge.core import secrets
        mk = self._key()
        aad = secrets.aad_for("inst1", "k")
        nonces = set()
        for _ in range(50):
            _ct, nonce = secrets.encrypt(mk, "v", associated=aad)
            nonces.add(nonce)
        assert len(nonces) == 50, "nonces must be unique per encrypt call"


# ---------------------------------------------------------------------------
# Registry CRUD
# ---------------------------------------------------------------------------
class TestRegistrySecrets:
    """The Registry methods do SQL + crypto. Audit emission is the
    caller's responsibility — these tests cover the storage layer only.
    """

    def _registry(self, tmp_path: Path):
        from forest_soul_forge.registry import Registry
        return Registry.bootstrap(db_path=tmp_path / "test.sqlite")

    def _master(self):
        from forest_soul_forge.core import secrets
        return secrets.MasterKey(raw=b"m" * 32)

    def _agent(self, reg, instance_id="inst-test-001"):
        # Insert a minimal agent row so the FK on agent_secrets resolves.
        with reg._conn:  # transaction
            reg._conn.execute(
                """
                INSERT INTO agents (
                    instance_id, dna, dna_full, role, agent_name,
                    soul_path, constitution_path, constitution_hash,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (instance_id, "abc123", "abc123" * 10, "log_analyst",
                 "TestAgent", "soul.md", "const.yaml", "deadbeef" * 8,
                 "2026-04-29T00:00:00Z"),
            )
        return instance_id

    def test_set_and_get_round_trip(self, tmp_path: Path) -> None:
        reg = self._registry(tmp_path)
        inst = self._agent(reg)
        mk = self._master()
        reg.set_secret(inst, "openai_key", "sk-test-12345", master_key=mk)
        assert reg.get_secret(inst, "openai_key", master_key=mk) == "sk-test-12345"

    def test_list_names_only(self, tmp_path: Path) -> None:
        reg = self._registry(tmp_path)
        inst = self._agent(reg)
        mk = self._master()
        reg.set_secret(inst, "openai_key", "sk-A", master_key=mk)
        reg.set_secret(inst, "anthropic_key", "sk-B", master_key=mk)
        names = reg.list_secret_names(inst)
        assert sorted(names) == ["anthropic_key", "openai_key"]

    def test_list_per_agent_isolated(self, tmp_path: Path) -> None:
        # An agent must only see its own secret names — not a sibling's.
        reg = self._registry(tmp_path)
        inst_a = self._agent(reg, "inst-A")
        inst_b = self._agent(reg, "inst-B")
        mk = self._master()
        reg.set_secret(inst_a, "key_A", "value_A", master_key=mk)
        reg.set_secret(inst_b, "key_B", "value_B", master_key=mk)
        assert reg.list_secret_names(inst_a) == ["key_A"]
        assert reg.list_secret_names(inst_b) == ["key_B"]

    def test_get_unknown_raises(self, tmp_path: Path) -> None:
        from forest_soul_forge.core.secrets import UnknownSecretError
        reg = self._registry(tmp_path)
        inst = self._agent(reg)
        mk = self._master()
        with pytest.raises(UnknownSecretError):
            reg.get_secret(inst, "never_set", master_key=mk)

    def test_set_replaces_existing(self, tmp_path: Path) -> None:
        # Re-setting the same name overwrites the value. Useful for
        # rotation; the audit chain will record both set events.
        reg = self._registry(tmp_path)
        inst = self._agent(reg)
        mk = self._master()
        reg.set_secret(inst, "k", "old_value", master_key=mk)
        reg.set_secret(inst, "k", "new_value", master_key=mk)
        assert reg.get_secret(inst, "k", master_key=mk) == "new_value"

    def test_delete_returns_true_when_existed(self, tmp_path: Path) -> None:
        reg = self._registry(tmp_path)
        inst = self._agent(reg)
        mk = self._master()
        reg.set_secret(inst, "k", "v", master_key=mk)
        assert reg.delete_secret(inst, "k") is True
        assert reg.delete_secret(inst, "k") is False  # second time, gone

    def test_get_with_wrong_key_raises(self, tmp_path: Path) -> None:
        # Master key rotation without re-encrypting old rows = decrypt
        # failures. This test pins that behavior so we know we need a
        # rotation-aware tool if/when we rotate.
        reg = self._registry(tmp_path)
        inst = self._agent(reg)
        from forest_soul_forge.core import secrets
        mk1 = secrets.MasterKey(raw=b"1" * 32)
        mk2 = secrets.MasterKey(raw=b"2" * 32)
        reg.set_secret(inst, "k", "v", master_key=mk1)
        with pytest.raises(Exception):  # InvalidTag
            reg.get_secret(inst, "k", master_key=mk2)

    def test_get_bumps_last_revealed_at(self, tmp_path: Path) -> None:
        # Operator-visible "this secret was actually used" signal.
        reg = self._registry(tmp_path)
        inst = self._agent(reg)
        mk = self._master()
        reg.set_secret(inst, "k", "v", master_key=mk)
        before = reg._conn.execute(
            "SELECT last_revealed_at FROM agent_secrets WHERE instance_id=? AND name=?;",
            (inst, "k"),
        ).fetchone()
        assert before["last_revealed_at"] is None
        reg.get_secret(inst, "k", master_key=mk)
        after = reg._conn.execute(
            "SELECT last_revealed_at FROM agent_secrets WHERE instance_id=? AND name=?;",
            (inst, "k"),
        ).fetchone()
        assert after["last_revealed_at"] is not None
