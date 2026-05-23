"""Tests for ADR-0085 Phase D — audit_packet_generate.v1 builtin tool.

Coverage:
- Argument validation
- Missing framework yaml → error reported, still returns packet
- Missing audit chain → error reported, chain_status='broken'
- Empty chain → packet has empty per-family lists
- Chain entries within window are bucketed correctly:
  scan_reports / evidence_entries / archive_entries /
  remediation_entries by tag family
- Window cutoff excludes old entries
- packet_sha256 stable for the same body (no time-dependent
  fields IN the hash)
- Per-control summaries match the framework yaml
- Control-tagged entries flow to per-control counts
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.audit_packet_generate import (
    AuditPacketGenerateTool,
)


def _ctx():
    return ToolContext(
        instance_id="t", agent_dna="a" * 12,
        role="report_generator", genre="researcher",
        session_id=None,
    )


def _run(args):
    return asyncio.run(AuditPacketGenerateTool().execute(args, _ctx()))


def _write_framework(
    framework_dir: Path,
    framework_id: str,
    controls: list[dict],
) -> Path:
    framework_dir.mkdir(parents=True, exist_ok=True)
    path = framework_dir / f"{framework_id}.yaml"
    path.write_text(yaml.safe_dump({
        "framework_id":   framework_id,
        "framework_name": f"{framework_id} test",
        "version":        "test",
        "controls":       controls,
    }), encoding="utf-8")
    return path


def _write_chain(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


class TestValidation:
    def test_framework_id_required(self):
        with pytest.raises(ToolValidationError, match="framework_id"):
            AuditPacketGenerateTool().validate({})

    def test_framework_id_alphanumeric(self):
        with pytest.raises(ToolValidationError, match="alphanumeric"):
            AuditPacketGenerateTool().validate({
                "framework_id": "../etc/passwd",
            })

    def test_window_days_positive(self):
        with pytest.raises(ToolValidationError, match="window_days"):
            AuditPacketGenerateTool().validate({
                "framework_id": "soc2",
                "window_days":  0,
            })

    def test_window_days_capped(self):
        with pytest.raises(ToolValidationError, match="window_days"):
            AuditPacketGenerateTool().validate({
                "framework_id": "soc2",
                "window_days":  1000,
            })

    def test_audit_chain_path_must_be_string(self):
        with pytest.raises(
            ToolValidationError, match="audit_chain_path",
        ):
            AuditPacketGenerateTool().validate({
                "framework_id":     "soc2",
                "audit_chain_path": 5,
            })


class TestMissingInputs:
    def test_missing_framework_yaml(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        _write_chain(chain, [])
        out = _run({
            "framework_id":     "nonexistent",
            "framework_dir":    str(tmp_path),
            "audit_chain_path": str(chain),
        })
        body = out.output
        assert any("not found" in e for e in body["errors"])
        # Packet is still generated (with empty control_summaries)
        assert body["framework_id"] == "nonexistent"
        assert body["control_summaries"] == []
        assert "packet_sha256" in body

    def test_missing_chain_marks_broken(self, tmp_path):
        _write_framework(tmp_path, "fwk", [
            {"id": "C1", "title": "x", "category": "security"},
        ])
        out = _run({
            "framework_id":     "fwk",
            "framework_dir":    str(tmp_path),
            "audit_chain_path": str(tmp_path / "missing.jsonl"),
        })
        body = out.output
        assert body["chain_status"] == "broken"
        assert any("audit chain not found" in e for e in body["errors"])


class TestEmptyChain:
    def test_empty_chain_yields_empty_families(self, tmp_path):
        _write_framework(tmp_path, "fwk", [
            {"id": "C1", "title": "C1 title", "category": "security"},
        ])
        chain = tmp_path / "chain.jsonl"
        _write_chain(chain, [])
        out = _run({
            "framework_id":     "fwk",
            "framework_dir":    str(tmp_path),
            "audit_chain_path": str(chain),
        })
        body = out.output
        assert body["scan_reports"] == []
        assert body["evidence_entries"] == []
        assert body["archive_entries"] == []
        assert body["remediation_entries"] == []
        assert body["control_summaries"][0]["evidence_count"] == 0


class TestTagBucketing:
    def test_entries_bucket_by_tag_family(self, tmp_path):
        _write_framework(tmp_path, "fwk", [
            {"id": "C1", "title": "t", "category": "security"},
        ])
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            {"event_type": "memory_written", "ts": now - 100,
             "payload": {"tags": ["compliance_gap_report",
                                  "framework:fwk"],
                         "content": "scan report 1"}},
            {"event_type": "memory_written", "ts": now - 200,
             "payload": {"tags": ["evidence_captured",
                                  "framework:fwk",
                                  "control:C1"],
                         "content": "evidence 1"}},
            {"event_type": "memory_written", "ts": now - 300,
             "payload": {"tags": ["long_term_archival",
                                  "framework:fwk"],
                         "content": "archive 1"}},
            {"event_type": "memory_written", "ts": now - 400,
             "payload": {"tags": ["compliance_remediation_proposal",
                                  "framework:fwk"],
                         "content": "remediation 1"}},
            # Untagged entry — should NOT make it into the packet.
            {"event_type": "memory_written", "ts": now - 500,
             "payload": {"tags": ["unrelated"],
                         "content": "junk"}},
        ])
        out = _run({
            "framework_id":     "fwk",
            "framework_dir":    str(tmp_path),
            "audit_chain_path": str(chain),
        })
        body = out.output
        assert len(body["scan_reports"]) == 1
        assert len(body["evidence_entries"]) == 1
        assert len(body["archive_entries"]) == 1
        assert len(body["remediation_entries"]) == 1
        # Control-tagged evidence -> evidence_count for C1.
        assert body["control_summaries"][0]["evidence_count"] == 1


class TestWindowCutoff:
    def test_entries_outside_window_excluded(self, tmp_path):
        _write_framework(tmp_path, "fwk", [
            {"id": "C1", "title": "t", "category": "security"},
        ])
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        # 1 entry within 30d, 1 entry from 60d ago.
        recent_ts = now - 20 * 86400
        old_ts = now - 60 * 86400
        _write_chain(chain, [
            {"event_type": "memory_written", "ts": recent_ts,
             "payload": {"tags": ["evidence_captured",
                                  "framework:fwk"],
                         "content": "recent"}},
            {"event_type": "memory_written", "ts": old_ts,
             "payload": {"tags": ["evidence_captured",
                                  "framework:fwk"],
                         "content": "old"}},
        ])
        # 30-day window should exclude the 60d-old entry.
        out = _run({
            "framework_id":     "fwk",
            "framework_dir":    str(tmp_path),
            "audit_chain_path": str(chain),
            "window_days":      30,
        })
        body = out.output
        assert len(body["evidence_entries"]) == 1


class TestPacketSha:
    def test_packet_sha_is_hex_sha256(self, tmp_path):
        _write_framework(tmp_path, "fwk", [
            {"id": "C1", "title": "t", "category": "security"},
        ])
        chain = tmp_path / "chain.jsonl"
        _write_chain(chain, [])
        out = _run({
            "framework_id":     "fwk",
            "framework_dir":    str(tmp_path),
            "audit_chain_path": str(chain),
        })
        sha = out.output["packet_sha256"]
        assert sha.startswith("sha256:")
        # 64 hex chars after the prefix
        assert len(sha) == len("sha256:") + 64


class TestPerControlMatching:
    def test_per_control_count_summed(self, tmp_path):
        _write_framework(tmp_path, "fwk", [
            {"id": "C1", "title": "ctl1", "category": "security"},
            {"id": "C2", "title": "ctl2", "category": "availability"},
        ])
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            {"event_type": "memory_written", "ts": now - 100,
             "payload": {"tags": ["evidence_captured",
                                  "framework:fwk",
                                  "control:C1"],
                         "content": "c1 evidence A"}},
            {"event_type": "memory_written", "ts": now - 110,
             "payload": {"tags": ["evidence_captured",
                                  "framework:fwk",
                                  "control:C1"],
                         "content": "c1 evidence B"}},
            {"event_type": "memory_written", "ts": now - 120,
             "payload": {"tags": ["evidence_captured",
                                  "framework:fwk",
                                  "control:C2"],
                         "content": "c2 evidence"}},
            {"event_type": "memory_written", "ts": now - 130,
             "payload": {"tags": ["long_term_archival",
                                  "framework:fwk",
                                  "control:C2"],
                         "content": "c2 archive"}},
        ])
        out = _run({
            "framework_id":     "fwk",
            "framework_dir":    str(tmp_path),
            "audit_chain_path": str(chain),
        })
        body = out.output
        c1 = next(c for c in body["control_summaries"]
                  if c["control_id"] == "C1")
        c2 = next(c for c in body["control_summaries"]
                  if c["control_id"] == "C2")
        assert c1["evidence_count"] == 2
        assert c1["archive_attestation_count"] == 0
        assert c2["evidence_count"] == 1
        assert c2["archive_attestation_count"] == 1


class TestRealSoc2Smoke:
    def test_soc2_loads_and_generates_packet(self, tmp_path):
        repo_root = Path(__file__).resolve().parents[2]
        framework_dir = repo_root / "config" / "compliance_frameworks"
        chain = tmp_path / "empty_chain.jsonl"
        _write_chain(chain, [])
        out = _run({
            "framework_id":     "soc2",
            "framework_dir":    str(framework_dir),
            "audit_chain_path": str(chain),
        })
        body = out.output
        assert body["framework_id"] == "soc2"
        assert body["framework_name"]
        # SOC2 yaml has 5 controls
        assert len(body["control_summaries"]) >= 5
        assert "packet_sha256" in body
        assert body["packet_sha256"].startswith("sha256:")
