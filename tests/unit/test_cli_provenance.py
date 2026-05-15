"""ADR-0072 T2 (B303) — fsf provenance CLI tests.

Exercises the three subcommands:
  - precedence (formatted + JSON output)
  - resolve   (happy path + bad input)
  - list      (formatted + JSON, uses temp YAML fixtures)
"""
from __future__ import annotations

import argparse
import io
import json
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

import pytest
import yaml

from forest_soul_forge.cli.provenance_cmd import add_subparser


@pytest.fixture
def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    add_subparser(sub)
    return p


# ---------------------------------------------------------------------------
# precedence
# ---------------------------------------------------------------------------

def test_precedence_text_output(parser):
    args = parser.parse_args(["provenance", "precedence"])
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = args._run(args)
    assert rc == 0
    out = buf.getvalue()
    assert "hardcoded_handoff" in out
    assert "constitutional" in out
    assert "preference" in out
    assert "learned" in out
    # Descending ordering: hardcoded_handoff appears before learned.
    assert out.index("hardcoded_handoff") < out.index("learned")


def test_precedence_json_output(parser):
    args = parser.parse_args(["provenance", "precedence", "--json"])
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = args._run(args)
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert payload == {
        "hardcoded_handoff": 1000,
        "constitutional": 800,
        "preference": 400,
        "learned": 100,
    }


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------

def test_resolve_preference_beats_learned(parser):
    args = parser.parse_args(
        ["provenance", "resolve", "preference", "learned"],
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = args._run(args)
    assert rc == 0
    assert "winner: preference" in buf.getvalue()


def test_resolve_constitutional_beats_preference(parser):
    args = parser.parse_args(
        ["provenance", "resolve", "constitutional", "preference"],
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = args._run(args)
    assert rc == 0
    assert "winner: constitutional" in buf.getvalue()


def test_resolve_hardcoded_beats_everything(parser):
    for other in ("constitutional", "preference", "learned"):
        args = parser.parse_args(
            ["provenance", "resolve", "hardcoded_handoff", other],
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = args._run(args)
        assert rc == 0
        assert "winner: hardcoded_handoff" in buf.getvalue()


def test_resolve_unknown_layer_returns_2(parser):
    args = parser.parse_args(
        ["provenance", "resolve", "bogus", "learned"],
    )
    err_buf = io.StringIO()
    with redirect_stderr(err_buf):
        rc = args._run(args)
    assert rc == 2
    assert "unknown layer" in err_buf.getvalue()


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def _write_prefs_yaml(path: Path, prefs: list[dict]) -> None:
    path.write_text(
        yaml.safe_dump({
            "schema_version": 1,
            "preferences": prefs,
        }),
        encoding="utf-8",
    )


def _write_rules_yaml(
    path: Path,
    *,
    pending: list[dict] | None = None,
    active: list[dict] | None = None,
) -> None:
    path.write_text(
        yaml.safe_dump({
            "schema_version": 1,
            "pending_activation": pending or [],
            "active": active or [],
        }),
        encoding="utf-8",
    )


def test_list_empty_files(parser, tmp_path):
    pref = tmp_path / "preferences.yaml"
    rules = tmp_path / "learned_rules.yaml"
    _write_prefs_yaml(pref, [])
    _write_rules_yaml(rules)
    args = parser.parse_args([
        "provenance", "list",
        "--preferences-path", str(pref),
        "--learned-rules-path", str(rules),
    ])
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = args._run(args)
    assert rc == 0
    out = buf.getvalue()
    assert "PREFERENCES (0 loaded)" in out
    assert "(none)" in out  # Both empty buckets emit a (none) marker.


def test_list_with_loaded_preferences_text(parser, tmp_path):
    pref = tmp_path / "preferences.yaml"
    rules = tmp_path / "learned_rules.yaml"
    _write_prefs_yaml(pref, [
        {
            "id": "orch.draft_to_d7",
            "statement": "Bias D7 over D10 on draft tasks",
            "weight": 0.5,
            "domain": "orchestrator",
            "created_at": "2026-05-01T00:00:00+00:00",
            "updated_at": "2026-05-01T00:00:00+00:00",
        },
    ])
    _write_rules_yaml(rules)
    args = parser.parse_args([
        "provenance", "list",
        "--preferences-path", str(pref),
        "--learned-rules-path", str(rules),
    ])
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = args._run(args)
    assert rc == 0
    out = buf.getvalue()
    assert "PREFERENCES (1 loaded)" in out
    assert "orch.draft_to_d7" in out
    assert "Bias D7 over D10" in out


def test_list_json_output(parser, tmp_path):
    pref = tmp_path / "preferences.yaml"
    rules = tmp_path / "learned_rules.yaml"
    _write_prefs_yaml(pref, [
        {
            "id": "p1",
            "statement": "p-stmt",
            "weight": 0.3,
            "domain": "orchestrator",
            "created_at": "2026-05-01T00:00:00+00:00",
            "updated_at": "2026-05-01T00:00:00+00:00",
        },
    ])
    _write_rules_yaml(rules, pending=[
        {
            "id": "r_pending",
            "statement": "rule-stmt",
            "weight": 0.4,
            "domain": "memory",
            "proposer_agent_dna": "dna_abc",
            "created_at": "2026-05-02T00:00:00+00:00",
        },
    ])
    args = parser.parse_args([
        "provenance", "list",
        "--preferences-path", str(pref),
        "--learned-rules-path", str(rules),
        "--json",
    ])
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = args._run(args)
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert len(payload["preferences"]) == 1
    assert payload["preferences"][0]["id"] == "p1"
    assert payload["preferences"][0]["weight"] == 0.3
    assert len(payload["learned_rules"]["pending_activation"]) == 1
    assert payload["learned_rules"]["pending_activation"][0]["id"] == "r_pending"
    assert payload["learned_rules"]["active"] == []
