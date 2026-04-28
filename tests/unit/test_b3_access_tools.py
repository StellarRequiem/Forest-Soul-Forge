"""Unit tests for ADR-0033 Phase B3 — jit_access + key_inventory.

Covers:
- jit_access.v1     (time-bounded grant + audit metadata)
- key_inventory.v1  (key material enumeration without leaking bytes)
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from unittest import mock

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin import JitAccessTool, KeyInventoryTool
from forest_soul_forge.tools.builtin.jit_access import is_grant_valid


def _run(coro):
    return asyncio.run(coro)


def _ctx(**kw):
    base = dict(
        instance_id="inst-issuer", agent_dna="x" * 12,
        role="guardian", genre="security_high", session_id="s",
    )
    base.update(kw)
    return ToolContext(**base)


# ============================================================================
# jit_access.v1
# ============================================================================
class TestJitAccessValidation:
    def test_refuses_missing_principal(self):
        with pytest.raises(ToolValidationError, match="principal"):
            JitAccessTool().validate({
                "scope": "s", "ttl_seconds": 60, "reason": "r",
            })

    def test_refuses_empty_principal(self):
        with pytest.raises(ToolValidationError, match="principal"):
            JitAccessTool().validate({
                "principal": "", "scope": "s", "ttl_seconds": 60, "reason": "r",
            })

    def test_refuses_oversize_principal(self):
        with pytest.raises(ToolValidationError, match="principal"):
            JitAccessTool().validate({
                "principal": "x" * 100, "scope": "s",
                "ttl_seconds": 60, "reason": "r",
            })

    def test_refuses_empty_scope(self):
        with pytest.raises(ToolValidationError, match="scope"):
            JitAccessTool().validate({
                "principal": "p", "scope": "", "ttl_seconds": 60, "reason": "r",
            })

    def test_refuses_scope_with_spaces(self):
        with pytest.raises(ToolValidationError, match="scope"):
            JitAccessTool().validate({
                "principal": "p", "scope": "with spaces",
                "ttl_seconds": 60, "reason": "r",
            })

    def test_refuses_zero_ttl(self):
        with pytest.raises(ToolValidationError, match="ttl_seconds"):
            JitAccessTool().validate({
                "principal": "p", "scope": "s", "ttl_seconds": 0, "reason": "r",
            })

    def test_refuses_too_long_ttl(self):
        with pytest.raises(ToolValidationError, match="ttl_seconds"):
            JitAccessTool().validate({
                "principal": "p", "scope": "s",
                "ttl_seconds": 86401, "reason": "r",
            })

    def test_refuses_bool_ttl(self):
        with pytest.raises(ToolValidationError, match="ttl_seconds"):
            JitAccessTool().validate({
                "principal": "p", "scope": "s",
                "ttl_seconds": True, "reason": "r",
            })

    def test_refuses_empty_reason(self):
        with pytest.raises(ToolValidationError, match="reason"):
            JitAccessTool().validate({
                "principal": "p", "scope": "s",
                "ttl_seconds": 60, "reason": "",
            })

    def test_refuses_oversize_reason(self):
        with pytest.raises(ToolValidationError, match="reason"):
            JitAccessTool().validate({
                "principal": "p", "scope": "s",
                "ttl_seconds": 60, "reason": "x" * 300,
            })


class TestJitAccessExecution:
    def test_grant_shape(self):
        result = _run(JitAccessTool().execute({
            "principal":   "inst-target",
            "scope":       "fsf-priv:kill-pid",
            "ttl_seconds": 300,
            "reason":      "isolate suspicious shell",
        }, _ctx()))
        out = result.output
        # UUID4 → 36 chars with hyphens.
        assert len(out["grant_id"]) == 36
        assert out["principal"] == "inst-target"
        assert out["scope"] == "fsf-priv:kill-pid"
        assert out["ttl_seconds"] == 300
        assert out["reason"] == "isolate suspicious shell"
        # Fingerprint is 16-char hex (sha256 truncated).
        assert len(out["fingerprint"]) == 16
        assert all(c in "0123456789abcdef" for c in out["fingerprint"])
        # ISO-8601 strings parseable.
        from datetime import datetime
        granted = datetime.fromisoformat(out["granted_at"])
        expires = datetime.fromisoformat(out["expires_at"])
        assert (expires - granted).total_seconds() == 300

    def test_audit_metadata_carries_jit_flag(self):
        result = _run(JitAccessTool().execute({
            "principal": "p", "scope": "x:y",
            "ttl_seconds": 60, "reason": "r",
        }, _ctx(instance_id="my-instance")))
        assert result.metadata["jit_grant"] is True
        assert result.metadata["principal"] == "p"
        assert result.metadata["scope"] == "x:y"
        assert result.metadata["issuer_instance_id"] == "my-instance"
        assert isinstance(result.metadata["expires_at_unix"], int)
        assert result.metadata["fingerprint"] == result.output["fingerprint"]

    def test_fingerprint_changes_per_call(self):
        # Two grants with identical args should still have distinct
        # fingerprints because granted_at differs (UUID4 grant_ids
        # too — those are random).
        a = _run(JitAccessTool().execute({
            "principal": "p", "scope": "s",
            "ttl_seconds": 60, "reason": "r",
        }, _ctx()))
        # Force the timestamps to be different by sleeping a tick.
        time.sleep(0.001)
        b = _run(JitAccessTool().execute({
            "principal": "p", "scope": "s",
            "ttl_seconds": 60, "reason": "r",
        }, _ctx()))
        assert a.output["grant_id"] != b.output["grant_id"]
        assert a.output["fingerprint"] != b.output["fingerprint"]

    def test_side_effect_summary_includes_fingerprint(self):
        result = _run(JitAccessTool().execute({
            "principal": "downstream-agent",
            "scope": "memory.lineage:read",
            "ttl_seconds": 600,
            "reason": "investigate prior incident",
        }, _ctx()))
        assert result.output["fingerprint"] in result.side_effect_summary
        assert "downstream-agent" in result.side_effect_summary
        assert "memory.lineage:read" in result.side_effect_summary


class TestIsGrantValidHelper:
    def test_active_grant_is_valid(self):
        result = _run(JitAccessTool().execute({
            "principal": "p", "scope": "s",
            "ttl_seconds": 60, "reason": "r",
        }, _ctx()))
        assert is_grant_valid(result.output) is True

    def test_expired_grant_is_invalid(self):
        result = _run(JitAccessTool().execute({
            "principal": "p", "scope": "s",
            "ttl_seconds": 60, "reason": "r",
        }, _ctx()))
        # Pretend now is 1000s past granted_at — past 60s expiry.
        future = time.time() + 1000
        assert is_grant_valid(result.output, now_unix=future) is False

    def test_malformed_grant_is_invalid(self):
        assert is_grant_valid({}) is False
        assert is_grant_valid({"expires_at": "not iso"}) is False
        assert is_grant_valid({"expires_at": 12345}) is False


# ============================================================================
# key_inventory.v1
# ============================================================================
class TestKeyInventoryValidation:
    def test_refuses_non_list_categories(self):
        with pytest.raises(ToolValidationError, match="categories"):
            KeyInventoryTool().validate({"categories": "bad"})

    def test_refuses_unknown_category(self):
        with pytest.raises(ToolValidationError, match="category"):
            KeyInventoryTool().validate({"categories": ["weird"]})

    def test_refuses_empty_home_dir(self):
        with pytest.raises(ToolValidationError, match="home_dir"):
            KeyInventoryTool().validate({"home_dir": ""})

    def test_refuses_relative_home_dir(self):
        with pytest.raises(ToolValidationError, match="home_dir"):
            KeyInventoryTool().validate({"home_dir": "relative/path"})


class TestKeyInventorySsh:
    def test_enumerates_private_and_public_keys(self, tmp_path):
        ssh = tmp_path / ".ssh"
        ssh.mkdir(mode=0o700)
        (ssh / "id_rsa").write_text("FAKE-RSA")
        os.chmod(ssh / "id_rsa", 0o600)
        (ssh / "id_rsa.pub").write_text("ssh-rsa AAAA fake")
        (ssh / "id_ed25519").write_text("FAKE-ED")
        os.chmod(ssh / "id_ed25519", 0o600)

        r = _run(KeyInventoryTool().execute({
            "home_dir": str(tmp_path), "categories": ["ssh"],
        }, _ctx()))
        ssh_out = r.output["categories"]["ssh"]
        priv_names = {k["name"] for k in ssh_out["private_keys"]}
        pub_names  = {k["name"] for k in ssh_out["public_keys"]}
        assert priv_names == {"id_rsa", "id_ed25519"}
        assert pub_names == {"id_rsa.pub"}

    def test_warns_on_wrong_private_key_perms(self, tmp_path):
        ssh = tmp_path / ".ssh"
        ssh.mkdir(mode=0o700)
        (ssh / "id_rsa").write_text("FAKE")
        os.chmod(ssh / "id_rsa", 0o644)  # wrong

        r = _run(KeyInventoryTool().execute({
            "home_dir": str(tmp_path), "categories": ["ssh"],
        }, _ctx()))
        warnings = r.output["warnings"]
        assert any("id_rsa" in w and "0o600" in w for w in warnings)

    def test_warns_on_wrong_ssh_dir_perms(self, tmp_path):
        ssh = tmp_path / ".ssh"
        ssh.mkdir(mode=0o755)  # wrong; should be 0o700

        r = _run(KeyInventoryTool().execute({
            "home_dir": str(tmp_path), "categories": ["ssh"],
        }, _ctx()))
        warnings = r.output["warnings"]
        assert any(".ssh" in w and "0o700" in w for w in warnings)

    def test_counts_lines_not_content(self, tmp_path):
        ssh = tmp_path / ".ssh"
        ssh.mkdir(mode=0o700)
        (ssh / "authorized_keys").write_text(
            "ssh-rsa AAA1\nssh-rsa AAA2\n\nssh-ed25519 BBB\n"
        )
        (ssh / "known_hosts").write_text("host1 ssh-rsa A\nhost2 ssh-ed25519 B\n")

        r = _run(KeyInventoryTool().execute({
            "home_dir": str(tmp_path), "categories": ["ssh"],
        }, _ctx()))
        ssh_out = r.output["categories"]["ssh"]
        # 3 non-empty lines → 3
        assert ssh_out["authorized_keys_lines"] == 3
        assert ssh_out["known_hosts_lines"] == 2
        # Verify content not leaked anywhere.
        ser = repr(r.output)
        assert "AAA1" not in ser
        assert "ssh-rsa A\n" not in ser

    def test_missing_ssh_dir_is_skipped(self, tmp_path):
        # No .ssh dir at all.
        r = _run(KeyInventoryTool().execute({
            "home_dir": str(tmp_path), "categories": ["ssh"],
        }, _ctx()))
        assert any(s["name"] == "ssh" for s in r.output["categories_skipped"])
        ssh_out = r.output["categories"]["ssh"]
        assert ssh_out["private_keys"] == []
        assert ssh_out["authorized_keys_lines"] is None


class TestKeyInventoryKeychainGated:
    def test_keychain_skipped_on_linux(self, tmp_path):
        with mock.patch(
            "forest_soul_forge.tools.builtin.key_inventory.platform.system",
            return_value="Linux",
        ):
            r = _run(KeyInventoryTool().execute({
                "home_dir": str(tmp_path), "categories": ["keychain"],
            }, _ctx()))
        assert r.output["categories"]["keychain"] is None
        skipped = [s for s in r.output["categories_skipped"] if s["name"] == "keychain"]
        assert skipped
        assert "macOS-only" in skipped[0]["reason"]

    def test_keychain_returns_paths_only_when_security_present(self, tmp_path):
        # Pretend we're on darwin and security returns two paths.
        fake_paths = (
            "    \"/Users/test/Library/Keychains/login.keychain-db\"\n"
            "    \"/Library/Keychains/System.keychain\"\n"
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.key_inventory.platform.system",
            return_value="Darwin",
        ), mock.patch(
            "shutil.which",
            side_effect=lambda b: "/fake/security" if b == "security" else None,
        ), mock.patch(
            "subprocess.run",
            return_value=mock.Mock(
                stdout=fake_paths.encode(), stderr=b"", returncode=0,
            ),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.key_inventory._safe_stat",
            return_value={"size": 4096, "mtime_unix": 0, "perms": "0o600"},
        ):
            r = _run(KeyInventoryTool().execute({
                "home_dir": str(tmp_path), "categories": ["keychain"],
            }, _ctx()))
        kc = r.output["categories"]["keychain"]
        assert kc["count"] == 2
        # Each entry has path + size + mtime, never item names.
        for f in kc["files"]:
            assert "path" in f
            assert "size" in f
            assert "mtime_unix" in f
            assert "items" not in f
            assert "secret" not in str(f).lower()


class TestKeyInventorySigning:
    def test_enumerates_gnupg_files(self, tmp_path):
        gpg = tmp_path / ".gnupg"
        gpg.mkdir()
        (gpg / "pubring.kbx").write_bytes(b"FAKE-PUBRING")
        (gpg / "trustdb.gpg").write_bytes(b"FAKE-TRUSTDB")
        pkdir = gpg / "private-keys-v1.d"
        pkdir.mkdir()
        (pkdir / "AABBCC.key").write_bytes(b"FAKE")
        (pkdir / "DDEEFF.key").write_bytes(b"FAKE")

        r = _run(KeyInventoryTool().execute({
            "home_dir": str(tmp_path), "categories": ["signing"],
        }, _ctx()))
        sig = r.output["categories"]["signing"]
        names = {f["name"] for f in sig["files"]}
        assert names == {"pubring.kbx", "trustdb.gpg"}
        # Private blob count surfaced; names NOT.
        assert sig["private_blob_count"] == 2
        ser = repr(r.output)
        assert "AABBCC" not in ser
        assert "DDEEFF" not in ser

    def test_missing_gnupg_dir_skipped(self, tmp_path):
        r = _run(KeyInventoryTool().execute({
            "home_dir": str(tmp_path), "categories": ["signing"],
        }, _ctx()))
        assert any(s["name"] == "signing" for s in r.output["categories_skipped"])


# ============================================================================
# Registration sanity
# ============================================================================
class TestRegistration:
    def test_both_tools_register(self):
        from forest_soul_forge.tools import ToolRegistry
        from forest_soul_forge.tools.builtin import register_builtins
        reg = ToolRegistry()
        register_builtins(reg)
        assert reg.has("jit_access", "1")
        assert reg.has("key_inventory", "1")
