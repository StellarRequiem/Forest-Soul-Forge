"""Unit tests for cli/triune.py and cli/chronicle.py.

Coverage was 0 unit tests at Phase A audit (2026-04-30, findings T-7
and T-8). These are the two CLI subcommands that don't have their own
forge_*/install_* test files.

Strategy:
  - triune: validate the --instances arity check + the URL POST helper's
    error handling. The actual daemon-call happy path is exercised via
    integration; here we cover the input-validation gates that would
    otherwise only fail at runtime.
  - chronicle: cover the chain-path resolver (env var, explicit flag,
    fallback chain), the scope-validation logic, and a round-trip from
    a tmp audit chain through render to written output.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

from forest_soul_forge.cli.chronicle import _resolve_chain_path, run_chronicle
from forest_soul_forge.cli.triune import _post, run_bond


# ===========================================================================
# triune --instances arity validation
# ===========================================================================
class TestTriuneRunBondValidation:
    def _ns(self, **kw) -> argparse.Namespace:
        defaults = dict(
            name="test-bond",
            instances=["i1", "i2", "i3"],
            operator="op",
            no_restrict=False,
        )
        defaults.update(kw)
        return argparse.Namespace(**defaults)

    def test_wrong_instance_count_returns_2(self, capsys):
        rc = run_bond(self._ns(instances=["i1", "i2"]))
        assert rc == 2
        err = capsys.readouterr().err
        assert "exactly 3" in err

    def test_duplicate_instances_returns_2(self, capsys):
        rc = run_bond(self._ns(instances=["i1", "i1", "i2"]))
        assert rc == 2
        err = capsys.readouterr().err
        assert "distinct" in err

    def test_happy_path_calls_post(self, capsys):
        """All 3 instance ids distinct → POST fires. We mock _post to
        avoid hitting a daemon."""
        canned_response = {
            "bond_name": "test-bond",
            "restrict_delegations": True,
            "ceremony_seq": 42,
            "ceremony_timestamp": "2026-04-30T12:00:00Z",
        }
        with mock.patch(
            "forest_soul_forge.cli.triune._post", return_value=canned_response,
        ) as post:
            rc = run_bond(self._ns())
        assert rc == 0
        post.assert_called_once()
        # The body passed to _post must contain restrict_delegations=True
        # because no_restrict=False:
        body = post.call_args[0][1]
        assert body["restrict_delegations"] is True
        assert body["bond_name"] == "test-bond"

    def test_no_restrict_flag_inverts(self):
        canned_response = {
            "bond_name": "x", "restrict_delegations": False,
            "ceremony_seq": 1, "ceremony_timestamp": "t",
        }
        with mock.patch(
            "forest_soul_forge.cli.triune._post", return_value=canned_response,
        ) as post:
            run_bond(self._ns(no_restrict=True))
        body = post.call_args[0][1]
        assert body["restrict_delegations"] is False


# ===========================================================================
# triune _post — error translation
# ===========================================================================
class TestTriunePost:
    def test_http_error_translated_to_systemexit(self):
        from urllib.error import HTTPError
        # Mock urlopen to raise HTTPError. The exception's read()
        # should be called by _post.
        err_body = json.dumps({"detail": "bond_name already exists"}).encode("utf-8")
        err = HTTPError("http://x", 409, "conflict", {}, fp=None)
        err.read = mock.Mock(return_value=err_body)
        with mock.patch(
            "forest_soul_forge.cli.triune.urlopen", side_effect=err,
        ):
            with pytest.raises(SystemExit, match="HTTP 409"):
                _post("http://daemon/triune/bond", {"x": 1})

    def test_url_error_translated_to_systemexit(self):
        from urllib.error import URLError
        with mock.patch(
            "forest_soul_forge.cli.triune.urlopen",
            side_effect=URLError("connection refused"),
        ):
            with pytest.raises(SystemExit, match="could not reach daemon"):
                _post("http://daemon/triune/bond", {"x": 1})

    def test_happy_path_returns_parsed_json(self):
        """Successful POST returns the parsed JSON dict."""
        body = json.dumps({"ok": True, "value": 42}).encode("utf-8")
        resp = mock.MagicMock()
        resp.read.return_value = body
        ctx = mock.MagicMock()
        ctx.__enter__.return_value = resp
        ctx.__exit__.return_value = False
        with mock.patch(
            "forest_soul_forge.cli.triune.urlopen", return_value=ctx,
        ):
            out = _post("http://daemon/x", {"a": 1})
        assert out == {"ok": True, "value": 42}


# ===========================================================================
# chronicle._resolve_chain_path — fallback chain
# ===========================================================================
class TestResolveChainPath:
    def test_explicit_flag_wins(self, tmp_path):
        explicit = tmp_path / "custom.jsonl"
        explicit.write_text("")
        out = _resolve_chain_path(str(explicit))
        assert out == explicit

    def test_env_var_used_when_no_flag(self, tmp_path, monkeypatch):
        env_path = tmp_path / "env.jsonl"
        env_path.write_text("")
        monkeypatch.setenv("FSF_AUDIT_CHAIN_PATH", str(env_path))
        assert _resolve_chain_path(None) == env_path

    def test_falls_through_to_examples_default(self, tmp_path, monkeypatch):
        """No flag, no env → returns examples/audit_chain.jsonl
        candidate. We can't easily prove cwd-relative paths in tests
        without changing cwd, so we just assert the returned name
        matches the expected fallback."""
        monkeypatch.delenv("FSF_AUDIT_CHAIN_PATH", raising=False)
        # When neither candidate exists, the helper returns the FIRST
        # candidate (examples/audit_chain.jsonl) so the caller's "file
        # not found" error mentions a concrete path.
        result = _resolve_chain_path(None)
        # Either a valid existing path OR the first candidate.
        assert result.name == "audit_chain.jsonl"


# ===========================================================================
# chronicle.run_chronicle — scope validation + path-not-found
# ===========================================================================
class TestRunChronicleValidation:
    def _ns(self, **kw) -> argparse.Namespace:
        defaults = dict(
            instance_id=None,
            bond=None,
            full_chain=False,
            md=False,
            include_payload=False,
            reverse=False,
            out=None,
            chain_path=None,
        )
        defaults.update(kw)
        return argparse.Namespace(**defaults)

    def test_no_chain_file_returns_1(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv(
            "FSF_AUDIT_CHAIN_PATH", str(tmp_path / "nonexistent.jsonl"),
        )
        rc = run_chronicle(self._ns(full_chain=True))
        assert rc == 1
        err = capsys.readouterr().err
        assert "audit chain not found" in err

    def test_no_scope_returns_2(self, tmp_path, capsys, monkeypatch):
        # Need a real chain file to get past the existence check.
        chain = tmp_path / "audit.jsonl"
        chain.write_text("")
        monkeypatch.setenv("FSF_AUDIT_CHAIN_PATH", str(chain))
        rc = run_chronicle(self._ns())  # no instance_id, no bond, no full_chain
        assert rc == 2
        err = capsys.readouterr().err
        assert "exactly one" in err

    def test_multiple_scopes_returns_2(self, tmp_path, capsys, monkeypatch):
        chain = tmp_path / "audit.jsonl"
        chain.write_text("")
        monkeypatch.setenv("FSF_AUDIT_CHAIN_PATH", str(chain))
        rc = run_chronicle(self._ns(bond="x", full_chain=True))
        assert rc == 2

    def test_full_chain_writes_html_to_default_path(
        self, tmp_path, monkeypatch, capsys,
    ):
        """End-to-end: real (empty) chain → render_html → written to
        default data/chronicles/<slug>__<date>.html path."""
        # Build a tiny real chain so AuditChain reads it.
        chain = tmp_path / "audit.jsonl"
        chain.write_text("")
        monkeypatch.setenv("FSF_AUDIT_CHAIN_PATH", str(chain))
        # Run from tmp_path so the default output dir lands inside it.
        monkeypatch.chdir(tmp_path)
        rc = run_chronicle(self._ns(full_chain=True))
        assert rc == 0
        # Default path is data/chronicles/full_chain__<date>.html
        chronicles_dir = tmp_path / "data" / "chronicles"
        files = list(chronicles_dir.iterdir())
        assert len(files) == 1
        assert files[0].suffix == ".html"
        assert files[0].read_text(encoding="utf-8").startswith("<!doctype html>")

    def test_md_flag_writes_markdown(
        self, tmp_path, monkeypatch,
    ):
        chain = tmp_path / "audit.jsonl"
        chain.write_text("")
        monkeypatch.setenv("FSF_AUDIT_CHAIN_PATH", str(chain))
        out_path = tmp_path / "chronicle.md"
        rc = run_chronicle(self._ns(full_chain=True, md=True, out=str(out_path)))
        assert rc == 0
        assert out_path.exists()
        content = out_path.read_text(encoding="utf-8")
        # Markdown output starts with the title heading
        assert content.startswith("# Chronicle: full forge")
