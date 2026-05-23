"""Tests for ADR-0087 Phase D — decision_journal_compile.v1 builtin tool."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.decision_journal_compile import (
    DecisionJournalCompileTool,
)


def _ctx():
    return ToolContext(
        instance_id="reflector_test",
        agent_dna="a" * 12,
        role="reflector",
        genre="researcher",
        session_id=None,
    )


def _run(args):
    return asyncio.run(DecisionJournalCompileTool().execute(args, _ctx()))


def _write_chain(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _mk_entry(
    *,
    ts: float,
    tags: list[str] | None = None,
    event_type: str | None = None,
    content: str = "",
    entry_id: str = "",
) -> dict:
    payload = {"tags": tags or [], "content": content}
    if entry_id:
        payload["entry_id"] = entry_id
    out = {"ts": ts, "tags": tags or [], "content": content,
           "payload": payload}
    if event_type:
        out["event_type"] = event_type
    return out


class TestValidation:
    def test_window_must_be_positive(self):
        with pytest.raises(ToolValidationError, match="window_hours"):
            DecisionJournalCompileTool().validate({"window_hours": 0})

    def test_window_capped(self):
        with pytest.raises(ToolValidationError, match="720"):
            DecisionJournalCompileTool().validate({"window_hours": 721})

    def test_window_must_be_int(self):
        with pytest.raises(ToolValidationError, match="window_hours"):
            DecisionJournalCompileTool().validate({"window_hours": "many"})

    def test_audit_chain_path_must_be_string(self):
        with pytest.raises(ToolValidationError, match="audit_chain_path"):
            DecisionJournalCompileTool().validate(
                {"audit_chain_path": 42}
            )

    def test_no_args_ok(self):
        DecisionJournalCompileTool().validate({})

    def test_valid_args_ok(self):
        DecisionJournalCompileTool().validate(
            {"window_hours": 168, "audit_chain_path": "x"}
        )


class TestExecute:
    def test_chain_missing_reports_error_returns_empty(self, tmp_path):
        chain = tmp_path / "nope.jsonl"
        r = _run({"audit_chain_path": str(chain)})
        assert r.output["summary"]["decision_count"] == 0
        assert r.output["summary"]["deferral_count"] == 0
        assert any("not found" in e for e in r.output["errors"])

    def test_empty_chain_returns_empty_digest(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        chain.write_text("")
        r = _run({"audit_chain_path": str(chain)})
        assert r.output["summary"] == {
            "decision_count": 0,
            "deferral_count": 0,
            "pattern_count":  0,
        }

    def test_decision_via_event_type(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(
            chain,
            [
                _mk_entry(
                    ts=now - 100,
                    event_type="operator_decision",
                    content="picked option A",
                    entry_id="e1",
                )
            ],
        )
        r = _run({"audit_chain_path": str(chain), "window_hours": 1})
        assert r.output["summary"]["decision_count"] == 1
        assert r.output["decisions"][0]["entry_id"] == "e1"

    def test_decision_via_decision_substring(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(
            chain,
            [
                _mk_entry(
                    ts=now - 100,
                    event_type="agent_decision_made",
                    content="hi",
                    entry_id="e2",
                )
            ],
        )
        r = _run({"audit_chain_path": str(chain), "window_hours": 1})
        assert r.output["summary"]["decision_count"] == 1

    def test_decision_via_tag(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(
            chain,
            [
                _mk_entry(
                    ts=now - 100,
                    tags=["decision", "topic:planning"],
                    content="picked",
                    entry_id="e3",
                )
            ],
        )
        r = _run({"audit_chain_path": str(chain), "window_hours": 1})
        assert r.output["summary"]["decision_count"] == 1
        assert r.output["decisions"][0]["topic"] == "planning"

    def test_decision_via_decision_prefix_tag(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(
            chain,
            [
                _mk_entry(
                    ts=now - 100,
                    tags=["decision:approved_PR_42"],
                    content="approved",
                    entry_id="e4",
                )
            ],
        )
        r = _run({"audit_chain_path": str(chain), "window_hours": 1})
        assert r.output["summary"]["decision_count"] == 1

    def test_deferral_via_tag(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(
            chain,
            [
                _mk_entry(
                    ts=now - 100,
                    tags=["deferred", "topic:research"],
                    content="pushed to next week",
                    entry_id="d1",
                )
            ],
        )
        r = _run({"audit_chain_path": str(chain), "window_hours": 1})
        assert r.output["summary"]["deferral_count"] == 1
        assert r.output["deferrals"][0]["topic"] == "research"

    def test_pattern_threshold_three(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(
            chain,
            [
                _mk_entry(ts=now - 10, tags=["topic:foo"]),
                _mk_entry(ts=now - 20, tags=["topic:foo"]),
                _mk_entry(ts=now - 30, tags=["topic:foo"]),
                _mk_entry(ts=now - 40, tags=["topic:bar"]),
                _mk_entry(ts=now - 50, tags=["topic:bar"]),
            ],
        )
        r = _run({"audit_chain_path": str(chain), "window_hours": 1})
        # foo has 3 -> pattern; bar has 2 -> not a pattern
        assert r.output["patterns"] == {"foo": 3}
        assert r.output["summary"]["pattern_count"] == 1

    def test_window_excludes_old_entries(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(
            chain,
            [
                _mk_entry(
                    ts=now - 7200,  # 2 hours ago
                    event_type="operator_decision",
                    entry_id="old",
                ),
                _mk_entry(
                    ts=now - 60,
                    event_type="operator_decision",
                    entry_id="new",
                ),
            ],
        )
        r = _run({"audit_chain_path": str(chain), "window_hours": 1})
        assert r.output["summary"]["decision_count"] == 1
        assert r.output["decisions"][0]["entry_id"] == "new"

    def test_window_includes_recent(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(
            chain,
            [
                _mk_entry(
                    ts=now - 100,
                    event_type="operator_decision",
                    entry_id="r1",
                ),
            ],
        )
        r = _run({"audit_chain_path": str(chain), "window_hours": 24})
        assert r.output["summary"]["decision_count"] == 1

    def test_malformed_lines_skipped(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        chain.parent.mkdir(parents=True, exist_ok=True)
        with chain.open("w") as f:
            f.write("not valid json\n")
            f.write(
                json.dumps(
                    _mk_entry(
                        ts=time.time() - 10,
                        event_type="operator_decision",
                        entry_id="ok",
                    )
                ) + "\n"
            )
        r = _run({"audit_chain_path": str(chain), "window_hours": 1})
        # Bad line silently skipped, good line counted
        assert r.output["summary"]["decision_count"] == 1

    def test_attestor_extracted_from_tags(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(
            chain,
            [
                _mk_entry(
                    ts=now - 100,
                    tags=["decision", "attestor:OpAlex"],
                    content="x",
                )
            ],
        )
        r = _run({"audit_chain_path": str(chain), "window_hours": 1})
        assert r.output["decisions"][0]["attestor"] == "OpAlex"

    def test_content_excerpt_truncated_at_200(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        long_content = "x" * 500
        _write_chain(
            chain,
            [
                _mk_entry(
                    ts=now - 100,
                    tags=["decision"],
                    content=long_content,
                )
            ],
        )
        r = _run({"audit_chain_path": str(chain), "window_hours": 1})
        assert len(r.output["decisions"][0]["content_excerpt"]) == 200

    def test_multiple_deferral_tags(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(
            chain,
            [
                _mk_entry(ts=now - 100, tags=["deferred"]),
                _mk_entry(ts=now - 200, tags=["pending"]),
                _mk_entry(ts=now - 300, tags=["open_item"]),
                _mk_entry(ts=now - 400, tags=["carry_forward"]),
                _mk_entry(ts=now - 500, tags=["blocked"]),
            ],
        )
        r = _run({"audit_chain_path": str(chain), "window_hours": 1})
        assert r.output["summary"]["deferral_count"] == 5

    def test_decisions_and_deferrals_counted_independently(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(
            chain,
            [
                _mk_entry(
                    ts=now - 100,
                    tags=["decision", "deferred"],
                    content="weird, both",
                )
            ],
        )
        r = _run({"audit_chain_path": str(chain), "window_hours": 1})
        # Both buckets get the entry — they're independent classifications
        assert r.output["summary"]["decision_count"] == 1
        assert r.output["summary"]["deferral_count"] == 1

    def test_iso_ts_supported(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        from datetime import datetime, timezone as tz
        iso = (
            datetime.now(tz.utc).replace(tzinfo=None)
            .isoformat(timespec="seconds")
        )
        _write_chain(
            chain,
            [
                {
                    "ts": iso,
                    "event_type": "operator_decision",
                    "tags": [],
                    "content": "iso",
                    "payload": {"entry_id": "iso1"},
                }
            ],
        )
        r = _run({"audit_chain_path": str(chain), "window_hours": 24})
        assert r.output["summary"]["decision_count"] == 1

    def test_seq_fallback_for_entry_id(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(
            chain,
            [
                {
                    "ts": now - 100,
                    "tags": ["decision"],
                    "content": "x",
                    "seq": 42,
                }
            ],
        )
        r = _run({"audit_chain_path": str(chain), "window_hours": 1})
        assert r.output["decisions"][0]["entry_id"] == "seq:42"

    def test_generated_at_present(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        chain.write_text("")
        r = _run({"audit_chain_path": str(chain)})
        assert r.output["generated_at"].endswith("Z")

    def test_default_window_24(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        chain.write_text("")
        r = _run({"audit_chain_path": str(chain)})
        assert r.output["window_hours"] == 24

    def test_window_echoed(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        chain.write_text("")
        r = _run({"audit_chain_path": str(chain), "window_hours": 168})
        assert r.output["window_hours"] == 168

    def test_side_effect_summary_format(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        chain.write_text("")
        r = _run({"audit_chain_path": str(chain)})
        assert "decision_journal" in r.side_effect_summary


class TestSchema:
    def test_name_version(self):
        t = DecisionJournalCompileTool()
        assert t.name == "decision_journal_compile"
        assert t.version == "1"

    def test_side_effects_read_only(self):
        assert DecisionJournalCompileTool().side_effects == "read_only"
