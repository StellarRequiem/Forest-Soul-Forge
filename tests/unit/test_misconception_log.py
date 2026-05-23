"""Tests for ADR-0089 Phase B — misconception_log.v1 builtin tool."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.misconception_log import (
    MisconceptionLogTool,
)


def _ctx():
    return ToolContext(
        instance_id="assessor_test",
        agent_dna="a" * 12,
        role="assessor",
        genre="guardian",
        session_id=None,
    )


def _run(args):
    return asyncio.run(MisconceptionLogTool().execute(args, _ctx()))


def _good_args(tmp_path: Path):
    return {
        "topic_slug": "diffusion-forward",
        "claim_summary": "Forward diffusion is reversible",
        "correction": "Forward diffusion is irreversible by design",
        "severity": "moderate",
        "source_item_id": "item_abc",
        "ledger_path": str(tmp_path / "misconceptions.jsonl"),
    }


class TestValidation:
    def test_topic_slug_required(self):
        with pytest.raises(ToolValidationError, match="topic_slug"):
            MisconceptionLogTool().validate(
                {"claim_summary": "c", "correction": "r"}
            )

    def test_claim_summary_required(self):
        with pytest.raises(ToolValidationError, match="claim_summary"):
            MisconceptionLogTool().validate(
                {"topic_slug": "x", "correction": "r"}
            )

    def test_correction_required(self):
        with pytest.raises(ToolValidationError, match="correction"):
            MisconceptionLogTool().validate(
                {"topic_slug": "x", "claim_summary": "c"}
            )

    def test_correction_empty_rejected(self):
        with pytest.raises(ToolValidationError, match="correction"):
            MisconceptionLogTool().validate(
                {"topic_slug": "x", "claim_summary": "c", "correction": "  "}
            )

    def test_invalid_severity(self):
        with pytest.raises(ToolValidationError, match="severity"):
            MisconceptionLogTool().validate(
                {
                    "topic_slug": "x", "claim_summary": "c",
                    "correction": "r", "severity": "catastrophic",
                }
            )

    def test_correction_too_long(self):
        with pytest.raises(ToolValidationError, match="correction"):
            MisconceptionLogTool().validate(
                {
                    "topic_slug": "x", "claim_summary": "c",
                    "correction": "x" * 3001,
                }
            )

    def test_topic_slug_too_long(self):
        with pytest.raises(ToolValidationError, match="topic_slug"):
            MisconceptionLogTool().validate(
                {
                    "topic_slug": "x" * 201,
                    "claim_summary": "c",
                    "correction": "r",
                }
            )

    def test_source_item_id_must_be_string(self):
        with pytest.raises(ToolValidationError, match="source_item_id"):
            MisconceptionLogTool().validate(
                {
                    "topic_slug": "x", "claim_summary": "c",
                    "correction": "r", "source_item_id": 42,
                }
            )

    def test_default_severity_accepted(self):
        MisconceptionLogTool().validate(
            {"topic_slug": "x", "claim_summary": "c", "correction": "r"}
        )


class TestLedgerWrite:
    def test_writes_jsonl_record(self, tmp_path: Path):
        result = _run(_good_args(tmp_path))
        ledger = Path(result.output["ledger_path"])
        assert ledger.exists()
        lines = ledger.read_text().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["topic_slug"] == "diffusion-forward"
        assert rec["correction"].startswith("Forward diffusion is")
        assert rec["severity"] == "moderate"
        assert rec["source_item_id"] == "item_abc"
        assert rec["misconception_id"].startswith("mis_")

    def test_appends_rather_than_overwrites(self, tmp_path: Path):
        a = _good_args(tmp_path)
        _run(a)
        a2 = dict(a)
        a2["claim_summary"] = "Another wrong claim"
        a2["correction"] = "Another correction"
        _run(a2)
        ledger = Path(a["ledger_path"])
        assert len(ledger.read_text().splitlines()) == 2

    def test_default_assessor_id_from_ctx(self, tmp_path: Path):
        result = _run(_good_args(tmp_path))
        rec = json.loads(Path(result.output["ledger_path"]).read_text())
        assert rec["assessor_id"] == "assessor_test"

    def test_explicit_assessor_id_overrides(self, tmp_path: Path):
        args = _good_args(tmp_path)
        args["assessor_id"] = "Assessor-D9-custom"
        result = _run(args)
        rec = json.loads(Path(result.output["ledger_path"]).read_text())
        assert rec["assessor_id"] == "Assessor-D9-custom"

    def test_creates_parent_dir(self, tmp_path: Path):
        args = _good_args(tmp_path)
        args["ledger_path"] = str(tmp_path / "nested" / "deeper" / "log.jsonl")
        result = _run(args)
        assert Path(result.output["ledger_path"]).exists()


class TestMetadata:
    def test_metadata_carries_id_and_severity(self, tmp_path: Path):
        result = _run(_good_args(tmp_path))
        assert result.metadata["misconception_id"].startswith("mis_")
        assert result.metadata["severity"] == "moderate"
        assert result.metadata["topic_slug"] == "diffusion-forward"

    def test_side_effect_summary_mentions_slug(self, tmp_path: Path):
        result = _run(_good_args(tmp_path))
        assert "diffusion-forward" in result.side_effect_summary
        assert "moderate" in result.side_effect_summary


class TestSideEffects:
    def test_side_effects_filesystem(self):
        assert MisconceptionLogTool.side_effects == "filesystem"

    def test_version_is_bare_string(self):
        assert MisconceptionLogTool.version == "1"
