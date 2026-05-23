"""Tests for ADR-0086 Phase D — daily_knowledge_delta.v1.

Coverage:
- Argument validation (window_hours, topic_filter shape,
  audit_chain_path type)
- Missing chain → error reported, empty buckets
- Empty chain → empty result, no errors
- Catalog-tagged entries flow to catalog_writes
- Prospector-tagged entries flow to prospector_pulls
- Contradiction-flag-tagged entries flow to contradiction_flags
- Topic bucketing groups by topic:<slug>
- Topic filter narrows to one topic
- Window cutoff excludes old entries
- Summary counts match the bucketed contents
- Side-effects = read_only
- Output shape includes generated_at + summary
- Entries without a topic tag are skipped
- Synthetic seq fallback for missing entry_id
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.daily_knowledge_delta import (
    DailyKnowledgeDeltaTool,
)


def _ctx():
    return ToolContext(
        instance_id="t", agent_dna="a" * 12,
        role="synthesizer", genre="researcher",
        session_id=None,
    )


def _run(args):
    return asyncio.run(DailyKnowledgeDeltaTool().execute(args, _ctx()))


def _write_chain(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _mk_entry(
    entry_id: str,
    ts: float,
    tags: list[str],
    content: str = "",
) -> dict:
    return {
        "ts":      ts,
        "tags":    tags,
        "content": content,
        "payload": {"entry_id": entry_id},
    }


class TestValidation:
    def test_window_hours_positive(self):
        with pytest.raises(ToolValidationError, match="window_hours"):
            DailyKnowledgeDeltaTool().validate({"window_hours": 0})

    def test_window_hours_capped(self):
        with pytest.raises(ToolValidationError, match="window_hours"):
            DailyKnowledgeDeltaTool().validate({"window_hours": 1000})

    def test_topic_filter_kebab_case(self):
        with pytest.raises(ToolValidationError, match="topic_filter"):
            DailyKnowledgeDeltaTool().validate({
                "topic_filter": "Bad Filter",
            })

    def test_topic_filter_empty_allowed(self):
        DailyKnowledgeDeltaTool().validate({"topic_filter": ""})

    def test_audit_chain_path_type(self):
        with pytest.raises(
            ToolValidationError, match="audit_chain_path",
        ):
            DailyKnowledgeDeltaTool().validate({
                "audit_chain_path": 123,
            })

    def test_valid_args_accept(self):
        DailyKnowledgeDeltaTool().validate({
            "window_hours": 48,
            "topic_filter": "diffusion-models",
        })


class TestEmptyAndMissing:
    def test_missing_chain(self, tmp_path):
        result = _run({
            "audit_chain_path": str(tmp_path / "no.jsonl"),
        })
        assert result.output["summary"]["catalog_write_count"] == 0
        assert any(
            "audit chain not found" in e
            for e in result.output["errors"]
        )

    def test_empty_chain(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        chain.write_text("", encoding="utf-8")
        result = _run({
            "audit_chain_path": str(chain),
        })
        assert result.output["summary"]["catalog_write_count"] == 0
        assert result.output["summary"]["prospector_pull_count"] == 0
        assert result.output["summary"]["contradiction_flag_count"] == 0
        assert result.output["errors"] == []


class TestBucketing:
    def test_catalog_tagged_entries_in_catalog_writes(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry("e1", now, [
                "knowledge_catalog_entry",
                "topic:diffusion-models",
                "attestor:Librarian-D1",
            ], "catalog entry text"),
        ])
        result = _run({"audit_chain_path": str(chain)})
        assert "diffusion-models" in result.output["catalog_writes"]
        assert len(
            result.output["catalog_writes"]["diffusion-models"],
        ) == 1
        assert result.output["summary"]["catalog_write_count"] == 1

    def test_prospector_tagged_entries_in_prospector_pulls(
        self, tmp_path,
    ):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry("e2", now, [
                "knowledge_prospector_inbox",
                "topic:diffusion-models",
                "provenance:https://arxiv.org/abs/2010.02502",
                "attestor:Prospector-D1",
            ], "brief"),
        ])
        result = _run({"audit_chain_path": str(chain)})
        assert "diffusion-models" in result.output["prospector_pulls"]
        node = result.output["prospector_pulls"]["diffusion-models"][0]
        assert node["source_url"] == "https://arxiv.org/abs/2010.02502"
        assert result.output["summary"]["prospector_pull_count"] == 1

    def test_contradiction_flag_tagged_entries_in_flags(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry("e3", now, [
                "contradiction_flag",
                "topic:diffusion-models",
                "attestor:KnowledgeVerifier-D1",
            ], "flagged pair"),
        ])
        result = _run({"audit_chain_path": str(chain)})
        assert "diffusion-models" in (
            result.output["contradiction_flags"]
        )
        assert result.output["summary"]["contradiction_flag_count"] == 1

    def test_no_topic_tag_skipped(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry("e4", now, [
                "knowledge_catalog_entry",
                "attestor:Librarian-D1",
            ], "no topic tag"),
        ])
        result = _run({"audit_chain_path": str(chain)})
        assert result.output["summary"]["catalog_write_count"] == 0

    def test_synthetic_seq_id_fallback(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            {
                "ts":      now,
                "tags":    [
                    "knowledge_catalog_entry",
                    "topic:t",
                ],
                "content": "claim",
                "seq":     99,
            },
        ])
        result = _run({"audit_chain_path": str(chain)})
        node = result.output["catalog_writes"]["t"][0]
        assert node["entry_id"] == "seq:99"


class TestFilters:
    def test_window_cutoff_excludes_old(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        old = now - 86400 * 7  # one week ago
        _write_chain(chain, [
            _mk_entry("old", old, [
                "knowledge_catalog_entry",
                "topic:t",
            ], "old"),
            _mk_entry("new", now, [
                "knowledge_catalog_entry",
                "topic:t",
            ], "new"),
        ])
        result = _run({
            "audit_chain_path": str(chain),
            "window_hours":     24,
        })
        assert result.output["summary"]["catalog_write_count"] == 1
        assert (
            result.output["catalog_writes"]["t"][0]["entry_id"]
            == "new"
        )

    def test_topic_filter_narrows(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry("a", now, [
                "knowledge_catalog_entry",
                "topic:diffusion-models",
            ], "a"),
            _mk_entry("b", now, [
                "knowledge_catalog_entry",
                "topic:llm-training",
            ], "b"),
        ])
        result = _run({
            "audit_chain_path": str(chain),
            "topic_filter":     "diffusion-models",
        })
        assert (
            list(result.output["catalog_writes"].keys())
            == ["diffusion-models"]
        )
        assert result.output["topic_filter"] == "diffusion-models"


class TestOutputShape:
    def test_summary_counts(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry("c1", now - 5, [
                "knowledge_catalog_entry",
                "topic:t",
            ], "catalog"),
            _mk_entry("p1", now - 3, [
                "knowledge_prospector_inbox",
                "topic:t",
            ], "prospector"),
            _mk_entry("f1", now, [
                "contradiction_flag",
                "topic:t",
            ], "flag"),
        ])
        result = _run({"audit_chain_path": str(chain)})
        s = result.output["summary"]
        assert s["catalog_write_count"] == 1
        assert s["prospector_pull_count"] == 1
        assert s["contradiction_flag_count"] == 1
        assert s["topic_count"] == 1

    def test_generated_at_iso(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        chain.write_text("", encoding="utf-8")
        result = _run({"audit_chain_path": str(chain)})
        assert result.output["generated_at"].endswith("Z")

    def test_side_effects_read_only(self):
        assert DailyKnowledgeDeltaTool().side_effects == "read_only"

    def test_metadata_contains_summary(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        chain.write_text("", encoding="utf-8")
        result = _run({"audit_chain_path": str(chain)})
        for key in (
            "catalog_write_count",
            "prospector_pull_count",
            "contradiction_flag_count",
            "topic_count",
        ):
            assert key in result.metadata
