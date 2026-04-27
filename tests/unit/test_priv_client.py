"""Unit tests for ADR-0033 A6 — PrivClient (daemon-side helper wrapper).

Coverage:
- TestValidation       — client refuses obviously bad input BEFORE shell-out
- TestHelperMissing    — assert_available raises when the helper isn't there
- TestMockHelperOutcomes — stub the subprocess.run call and verify ok/refuse
                            outcomes flow through PrivResult correctly
- TestParseReadProtected — output format helper

The actual helper script (``scripts/fsf-priv``) requires root + a
real pf installation to exercise; that surface is covered by the
operator running the install runbook + invoking it manually. These
tests verify the client's contract independent of the helper.
"""
from __future__ import annotations

import subprocess
from unittest import mock

import pytest

from forest_soul_forge.security.priv_client import (
    HelperMissing,
    PrivClient,
    PrivClientError,
    PrivResult,
)


# ===========================================================================
# Validation refusals (fire BEFORE subprocess)
# ===========================================================================
class TestValidation:
    def test_kill_pid_rejects_zero_and_one(self):
        client = PrivClient(helper_path="/dev/null", sudo_path="/dev/null")
        for pid in (0, 1, -1, -100):
            with pytest.raises(PrivClientError, match="positive integer"):
                client.kill_pid(pid)

    def test_kill_pid_rejects_non_int(self):
        client = PrivClient(helper_path="/dev/null", sudo_path="/dev/null")
        for bad in ("123", 1.5, None, True):
            with pytest.raises(PrivClientError, match="positive integer"):
                client.kill_pid(bad)  # type: ignore[arg-type]

    def test_pf_add_rejects_bad_anchor(self):
        client = PrivClient(helper_path="/dev/null", sudo_path="/dev/null")
        for bad_anchor in ("", "with space", "x" * 100, "with;semi", "with$dollar"):
            with pytest.raises(PrivClientError, match="anchor"):
                client.pf_add(bad_anchor, "block in all")

    def test_pf_add_rejects_oversized_rule(self):
        client = PrivClient(helper_path="/dev/null", sudo_path="/dev/null")
        with pytest.raises(PrivClientError, match="rule"):
            client.pf_add("good", "x" * 1000)

    def test_pf_add_rejects_empty_rule(self):
        client = PrivClient(helper_path="/dev/null", sudo_path="/dev/null")
        with pytest.raises(PrivClientError, match="non-empty"):
            client.pf_add("good", "")

    def test_pf_drop_rejects_bad_anchor(self):
        client = PrivClient(helper_path="/dev/null", sudo_path="/dev/null")
        with pytest.raises(PrivClientError, match="anchor"):
            client.pf_drop("with space")

    def test_read_protected_requires_absolute_path(self):
        client = PrivClient(helper_path="/dev/null", sudo_path="/dev/null")
        for bad in ("relative/path", "", None, 123):
            with pytest.raises(PrivClientError, match="absolute"):
                client.read_protected(bad)  # type: ignore[arg-type]


# ===========================================================================
# Helper-not-installed
# ===========================================================================
class TestHelperMissing:
    def test_assert_available_raises_for_missing_helper(self, tmp_path):
        client = PrivClient(
            helper_path=str(tmp_path / "nonexistent"),
            sudo_path=str(tmp_path / "also-nonexistent"),
        )
        with pytest.raises(HelperMissing, match="helper not found"):
            client.assert_available()

    def test_assert_available_passes_when_both_present(self, tmp_path):
        # Both paths just need to exist; we don't actually run them.
        helper = tmp_path / "fsf-priv"
        helper.write_text("#!/bin/sh\nexit 0\n")
        helper.chmod(0o755)
        sudo = tmp_path / "sudo"
        sudo.write_text("#!/bin/sh\nexit 0\n")
        sudo.chmod(0o755)
        client = PrivClient(helper_path=str(helper), sudo_path=str(sudo))
        client.assert_available()  # must not raise

    def test_run_raises_helper_missing_when_helper_absent(self, tmp_path):
        client = PrivClient(
            helper_path=str(tmp_path / "nope"),
            sudo_path=str(tmp_path / "sudo"),
        )
        with pytest.raises(HelperMissing):
            client._run("kill-pid", "1234")  # noqa: SLF001


# ===========================================================================
# Outcome translation via mocked subprocess
# ===========================================================================
class TestMockHelperOutcomes:
    @pytest.fixture
    def client(self, tmp_path):
        # Helper + sudo just need to exist on disk; we mock the
        # subprocess.run that would invoke them.
        helper = tmp_path / "fsf-priv"
        helper.write_text("# stub")
        helper.chmod(0o755)
        sudo = tmp_path / "sudo"
        sudo.write_text("# stub")
        sudo.chmod(0o755)
        return PrivClient(helper_path=str(helper), sudo_path=str(sudo))

    def test_success_path(self, client):
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(
                returncode=0, stdout=b"exited via SIGTERM\n", stderr=b"",
            )
            result = client.kill_pid(12345)
        assert isinstance(result, PrivResult)
        assert result.ok is True
        assert result.exit_code == 0
        assert result.op == "kill-pid"
        assert result.args == ("12345",)
        assert "exited" in result.stdout

    def test_helper_refusal_surfaces_as_not_ok(self, client):
        # Helper-side refusal: exit 2, stderr has the diagnostic.
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(
                returncode=2, stdout=b"",
                stderr=b"fsf-priv: refused: no process with PID 99999\n",
            )
            result = client.kill_pid(99999)
        assert result.ok is False
        assert result.exit_code == 2
        assert "no process" in result.stderr

    def test_timeout_returns_124(self, client):
        with mock.patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["x"], timeout=15.0),
        ):
            result = client.pf_drop("test_anchor")
        assert result.ok is False
        assert result.exit_code == 124
        assert "timed out" in result.stderr.lower()

    def test_pf_add_command_shape(self, client):
        # Pin the argv shape the client builds so a refactor that
        # drops -n (no-prompt) or reorders args surfaces.
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout=b"", stderr=b"")
            client.pf_add("isolate-12345", "block in all")
        args, kwargs = run.call_args
        cmd = args[0]
        assert cmd[0] == client.sudo_path
        assert cmd[1] == "-n"  # NOPASSWD; refuse to prompt
        assert cmd[2] == client.helper_path
        assert cmd[3] == "pf-add"
        assert cmd[4] == "isolate-12345"
        assert cmd[5] == "block in all"
        # subprocess.run is called with check=False so we can capture
        # non-zero exits and translate them to PrivResult.
        assert kwargs.get("check") is False


# ===========================================================================
# parse_read_protected_output
# ===========================================================================
class TestParseReadProtected:
    def test_well_formed_output(self):
        digest, size, path = PrivClient.parse_read_protected_output(
            "sha256:abc123  1024  /System/Library/CoreServices/file\n"
        )
        # parse_read_protected splits on a single space — the helper
        # uses single spaces, the test uses double to verify the
        # split tolerates whitespace stripping correctly.
        # Actually the helper emits single spaces; the doubles here
        # would break the split. Use single spaces.
        digest, size, path = PrivClient.parse_read_protected_output(
            "sha256:abc123 1024 /System/Library/CoreServices/file\n"
        )
        assert digest == "sha256:abc123"
        assert size == 1024
        assert path == "/System/Library/CoreServices/file"

    def test_path_with_spaces_preserved(self):
        # split(" ", 2) keeps the rest of the line as the path so a
        # path containing spaces is preserved end-to-end.
        digest, size, path = PrivClient.parse_read_protected_output(
            "sha256:def 42 /Library/Apple/some path/bin"
        )
        assert path == "/Library/Apple/some path/bin"

    def test_malformed_output_raises(self):
        with pytest.raises(PrivClientError, match="malformed"):
            PrivClient.parse_read_protected_output("just one field")

    def test_bad_digest_format_raises(self):
        with pytest.raises(PrivClientError, match="digest"):
            PrivClient.parse_read_protected_output("md5:xyz 100 /System/x")

    def test_bad_size_raises(self):
        with pytest.raises(PrivClientError, match="size"):
            PrivClient.parse_read_protected_output("sha256:abc not_a_size /System/x")
