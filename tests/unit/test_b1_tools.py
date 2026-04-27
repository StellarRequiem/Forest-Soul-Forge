"""Unit tests for ADR-0033 Phase B1 — pure-Python low-tier tools.

Covers:
- audit_chain_verify.v1
- file_integrity.v1
- log_scan.v1
- log_aggregate.v1

Each tool gets validation-refusal coverage + a happy-path test.
The full end-to-end behaviors (catastrophic-backtracking refusal,
disclosed-copy diff, timestamp normalization across formats) are
exercised by scripts/live-smoke.sh once it gets B1 stages.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin import (
    AuditChainVerifyTool,
    FileIntegrityTool,
    LogAggregateTool,
    LogScanTool,
)


def _run(coro):
    return asyncio.run(coro)


def _ctx(**kwargs):
    base = dict(
        instance_id="x", agent_dna="x" * 12,
        role="observer", genre="security_low",
        session_id="s",
    )
    base.update(kwargs)
    return ToolContext(**base)


# ===========================================================================
# audit_chain_verify.v1
# ===========================================================================
class TestAuditChainVerify:
    def test_no_chain_bound_refuses(self):
        with pytest.raises(ToolValidationError, match="no AuditChain"):
            _run(AuditChainVerifyTool().execute({}, _ctx()))

    def test_clean_chain_returns_ok(self, tmp_path):
        chain = AuditChain(tmp_path / "audit.jsonl")
        chain.append("agent_created", {"role": "observer"}, agent_dna="a" * 12)
        chain.append("agent_created", {"role": "observer"}, agent_dna="b" * 12)
        ctx = _ctx(constraints={"audit_chain": chain})
        result = _run(AuditChainVerifyTool().execute({}, ctx))
        assert result.output["ok"] is True
        assert result.output["entries_verified"] >= 3  # genesis + 2 appends
        assert result.output["broken_at_seq"] is None

    def test_max_unknown_validation(self):
        for bad in (0, 1001, "foo", -1):
            with pytest.raises(ToolValidationError, match="max_unknown_to_report"):
                AuditChainVerifyTool().validate({"max_unknown_to_report": bad})


# ===========================================================================
# file_integrity.v1
# ===========================================================================
class TestFileIntegrity:
    def test_validation_refusals(self):
        for bad in [
            {},
            {"paths": []},
            {"paths": ["x"], "mode": "bananas"},
            {"paths": ["x"], "mode": "diff"},  # missing baseline
            {"paths": [123]},
            {"paths": ["x" for _ in range(201)]},  # over cap
        ]:
            with pytest.raises(ToolValidationError):
                FileIntegrityTool().validate(bad)

    def test_snapshot_basic(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "b.txt").write_text("world")
        result = _run(FileIntegrityTool().execute(
            {"paths": [str(tmp_path)]}, _ctx(),
        ))
        assert result.output["mode"] == "snapshot"
        assert result.output["files"] == 2
        digests = result.output["digests"]
        assert all(v.startswith("sha256:") for v in digests.values())

    def test_diff_finds_changes(self, tmp_path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("v1")
        b.write_text("stable")
        snap = _run(FileIntegrityTool().execute({"paths": [str(tmp_path)]}, _ctx()))
        baseline = dict(snap.output["digests"])

        # Modify a, add c, delete nothing
        a.write_text("v2")
        (tmp_path / "c.txt").write_text("new")
        result = _run(FileIntegrityTool().execute({
            "paths": [str(tmp_path)],
            "mode": "diff",
            "baseline": baseline,
        }, _ctx()))
        assert len(result.output["changed"]) == 1
        assert result.output["changed"][0]["path"].endswith("a.txt")
        assert len(result.output["added"]) == 1
        assert next(iter(result.output["added"])).endswith("c.txt")
        assert result.output["unchanged"] == 1  # b.txt

    def test_symlinks_not_followed(self, tmp_path):
        # Plant a real file outside the watched dir, then symlink to
        # it from inside. The tool should record the symlink target
        # rather than hashing it through.
        if os.name == "nt":
            pytest.skip("symlink behavior differs on Windows")
        outside = tmp_path / "outside.txt"
        outside.write_text("secret")
        watched = tmp_path / "watched"
        watched.mkdir()
        link = watched / "link"
        os.symlink(outside, link)
        result = _run(FileIntegrityTool().execute(
            {"paths": [str(watched)]}, _ctx(),
        ))
        # Find the symlink entry
        link_digest = None
        for path, digest in result.output["digests"].items():
            if path.endswith("link"):
                link_digest = digest
                break
        assert link_digest is not None
        assert link_digest.startswith("symlink:"), (
            f"symlink should be recorded as 'symlink:<target>', got {link_digest!r}"
        )

    def test_missing_path_in_skipped(self, tmp_path):
        result = _run(FileIntegrityTool().execute(
            {"paths": [str(tmp_path / "nope")]}, _ctx(),
        ))
        assert any(
            s["reason"] == "not_found" for s in result.output["skipped"]
        )


# ===========================================================================
# log_scan.v1
# ===========================================================================
class TestLogScan:
    def test_validation_refusals(self):
        for bad, hint in [
            ({}, "paths"),
            ({"paths": []}, "non-empty"),
            ({"paths": ["x"], "pattern": ""}, "pattern"),
            ({"paths": ["x"], "pattern": "x" * 300}, "≤"),
            ({"paths": ["x"], "pattern": "(a+)+"}, "catastrophic"),
            ({"paths": ["x"], "pattern": "[bad"}, "compile"),
            ({"paths": ["x"], "pattern": "ok", "context_lines": 10}, "context_lines"),
            ({"paths": ["x"], "pattern": "ok", "max_matches": 0}, "max_matches"),
            ({"paths": ["x"], "pattern": "ok", "flags": ["BAD"]}, "flag"),
        ]:
            with pytest.raises(ToolValidationError, match=hint):
                LogScanTool().validate(bad)

    def test_basic_match(self, tmp_path):
        log = tmp_path / "sys.log"
        log.write_text(
            "ok 1\n"
            "FAIL: bad\n"
            "ok 2\n"
            "FAIL: worse\n"
        )
        result = _run(LogScanTool().execute(
            {"paths": [str(log)], "pattern": r"FAIL"},
            _ctx(),
        ))
        assert result.output["match_count"] == 2
        assert all(m["line"].startswith("FAIL") for m in result.output["matches"])

    def test_context_lines(self, tmp_path):
        log = tmp_path / "sys.log"
        log.write_text(
            "before-1\n"
            "before-2\n"
            "TARGET\n"
            "after-1\n"
            "after-2\n"
        )
        result = _run(LogScanTool().execute(
            {"paths": [str(log)], "pattern": r"TARGET", "context_lines": 2},
            _ctx(),
        ))
        m = result.output["matches"][0]
        assert m["before"] == ["before-1", "before-2"]
        assert m["after"] == ["after-1", "after-2"]

    def test_directory_walk(self, tmp_path):
        (tmp_path / "a.log").write_text("MATCH a\n")
        (tmp_path / "b.log").write_text("MATCH b\n")
        sub = tmp_path / "sub"; sub.mkdir()
        (sub / "c.log").write_text("MATCH c\n")
        result = _run(LogScanTool().execute(
            {"paths": [str(tmp_path)], "pattern": r"MATCH"},
            _ctx(),
        ))
        assert result.output["match_count"] == 3

    def test_max_matches_truncates(self, tmp_path):
        log = tmp_path / "many.log"
        log.write_text("\n".join("HIT {}".format(i) for i in range(20)))
        result = _run(LogScanTool().execute(
            {"paths": [str(log)], "pattern": r"HIT", "max_matches": 5},
            _ctx(),
        ))
        assert result.output["match_count"] == 5
        assert result.output["truncated"] is True

    def test_flags_ignorecase(self, tmp_path):
        log = tmp_path / "x.log"
        log.write_text("Error\nERROR\nerror\n")
        result = _run(LogScanTool().execute(
            {"paths": [str(log)], "pattern": r"^error$", "flags": ["IGNORECASE"]},
            _ctx(),
        ))
        assert result.output["match_count"] == 3


# ===========================================================================
# log_aggregate.v1
# ===========================================================================
class TestLogAggregate:
    def test_validation_refusals(self):
        for bad, hint in [
            ({}, "paths"),
            ({"paths": []}, "non-empty"),
            ({"paths": [123]}, "string"),
            ({"paths": ["x"], "max_lines_out": 0}, "max_lines_out"),
            ({"paths": ["x"] * 60}, "≤"),
        ]:
            with pytest.raises(ToolValidationError, match=hint):
                LogAggregateTool().validate(bad)

    def test_iso_and_syslog_sort_together(self, tmp_path):
        a = tmp_path / "a.log"
        a.write_text(
            "2026-04-27T10:00:00Z app started\n"
            "2026-04-27T10:01:00Z app ready\n"
        )
        b = tmp_path / "b.log"
        b.write_text("Apr 27 10:00:30 host sshd connect\n")
        result = _run(LogAggregateTool().execute(
            {"paths": [str(a), str(b)]}, _ctx(),
        ))
        ts_only = [l for l in result.output["lines"] if l["timestamp"]]
        assert ts_only == sorted(ts_only, key=lambda l: l["timestamp"])
        # Three timestamped lines; syslog one has the current year baked in
        assert len(ts_only) == 3

    def test_untimestamped_floats_to_front(self, tmp_path):
        a = tmp_path / "x.log"
        a.write_text(
            "no timestamp here\n"
            "2026-04-27T10:00:00Z something happened\n"
        )
        result = _run(LogAggregateTool().execute(
            {"paths": [str(a)]}, _ctx(),
        ))
        first = result.output["lines"][0]
        assert first["timestamp"] is None
        assert "no timestamp" in first["text"]

    def test_apache_format_normalizes(self, tmp_path):
        log = tmp_path / "access.log"
        log.write_text(
            '127.0.0.1 - - [27/Apr/2026:10:32:01 +0000] "GET / HTTP/1.1" 200 1024\n'
        )
        result = _run(LogAggregateTool().execute(
            {"paths": [str(log)]}, _ctx(),
        ))
        assert result.output["lines_out"] == 1
        assert result.output["lines"][0]["timestamp"] == "2026-04-27T10:32:01Z"

    def test_unix_epoch_normalizes(self, tmp_path):
        log = tmp_path / "epoch.log"
        # Pure epoch on a line by itself; the regex anchors so this
        # works as the only content. 1714125600 = 2024-04-26 10:00:00 UTC
        log.write_text("1714125600\n")
        result = _run(LogAggregateTool().execute(
            {"paths": [str(log)]}, _ctx(),
        ))
        assert result.output["lines"][0]["timestamp"] is not None
        assert result.output["lines"][0]["timestamp"].startswith("2024-04-26T")

    def test_truncation_capped(self, tmp_path):
        log = tmp_path / "many.log"
        log.write_text("\n".join(
            f"2026-04-27T{i:02d}:00:00Z entry {i}" for i in range(0, 20)
        ))
        result = _run(LogAggregateTool().execute(
            {"paths": [str(log)], "max_lines_out": 5}, _ctx(),
        ))
        assert result.output["lines_out"] == 5
        assert result.output["truncated"] is True


# ===========================================================================
# Registration sanity
# ===========================================================================
class TestRegistration:
    def test_all_b1_tools_register(self):
        from forest_soul_forge.tools import ToolRegistry
        from forest_soul_forge.tools.builtin import register_builtins
        reg = ToolRegistry()
        register_builtins(reg)
        for tool_name in ("audit_chain_verify", "file_integrity",
                          "log_scan", "log_aggregate"):
            assert reg.has(tool_name, "1"), f"{tool_name} not registered"
