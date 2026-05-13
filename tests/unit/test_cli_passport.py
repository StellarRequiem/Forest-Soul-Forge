"""Tests for ``fsf passport ...`` subcommand wiring (ADR-0061 T7).

The mint path needs a daemon to hit, so it's covered end-to-end
by test_daemon_passport.py. This file pins:

- Argparse wires `fsf passport` into the main parser without error.
- `fsf passport show <id>` reads passport.json off disk + pretty-
  prints it (no HTTP).
- `fsf passport show` returns a useful error code when the file is
  absent.
- `fsf passport fingerprint` returns 0 + the computed fingerprint
  on stdout.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from forest_soul_forge.cli.main import _build_parser, main


def test_parser_registers_passport_subcommand():
    parser = _build_parser()
    # The parser doesn't expose a public subcommand list, but
    # parse_args on a known passport invocation should succeed
    # (the show subcommand requires an instance_id only).
    args = parser.parse_args([
        "passport", "show", "anything", "--souls-dir", "/does/not/exist",
    ])
    assert args.cmd == "passport"
    assert args.passport_cmd == "show"
    assert args.instance_id == "anything"
    assert args.souls_dir == "/does/not/exist"


def test_show_missing_passport_returns_4(tmp_path: Path, capsys):
    rc = main([
        "passport", "show", "ghost_agent",
        "--souls-dir", str(tmp_path / "nope"),
    ])
    assert rc == 4
    err = capsys.readouterr().err
    assert "no passport.json" in err


def test_show_existing_passport_prints_pretty_json(tmp_path: Path, capsys):
    instance_id = "alpha_abc123abc123"
    agent_dir = tmp_path / "souls" / instance_id
    agent_dir.mkdir(parents=True)
    passport = {
        "version":                 1,
        "agent_dna":               "abc123",
        "instance_id":             instance_id,
        "authorized_fingerprints": ["aaaa1111bbbb2222"],
        "signature":               "ed25519:Zm9vYmFy",
    }
    (agent_dir / "passport.json").write_text(
        json.dumps(passport), encoding="utf-8",
    )
    rc = main([
        "passport", "show", instance_id,
        "--souls-dir", str(tmp_path / "souls"),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    # Pretty-printed (indent=2) so we get newlines between keys.
    assert "\n" in out
    parsed = json.loads(out)
    assert parsed["instance_id"] == instance_id
    assert parsed["authorized_fingerprints"] == ["aaaa1111bbbb2222"]


def test_show_malformed_passport_returns_7(tmp_path: Path, capsys):
    instance_id = "broken_agent"
    agent_dir = tmp_path / "souls" / instance_id
    agent_dir.mkdir(parents=True)
    (agent_dir / "passport.json").write_text(
        "{not valid json", encoding="utf-8",
    )
    rc = main([
        "passport", "show", instance_id,
        "--souls-dir", str(tmp_path / "souls"),
    ])
    assert rc == 7
    assert "not valid JSON" in capsys.readouterr().err


def test_fingerprint_subcommand_prints_fp_on_stdout(capsys):
    # Patch the substrate so we don't rely on host platform behavior.
    fake_fp = type("FP", (), {
        "fingerprint": "abc123def456ffff",
        "source": "test_fixture",
    })()
    with patch(
        "forest_soul_forge.core.hardware.compute_hardware_fingerprint",
        return_value=fake_fp,
    ):
        rc = main(["passport", "fingerprint"])
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "abc123def456ffff"
    # Source goes to stderr so pipes stay clean.
    assert "test_fixture" in captured.err


def test_mint_requires_at_least_one_fingerprint():
    """argparse should refuse a mint call with no -f flags."""
    parser = _build_parser()
    with pytest.raises(SystemExit):
        # Suppress argparse's stderr noise during the test.
        with patch("sys.stderr", io.StringIO()):
            parser.parse_args(["passport", "mint", "some_agent"])
