"""Tests for ADR-0086 Phase B — topic_genealogy_build.v1 builtin tool.

Coverage:
- Argument validation (topic_slug shape, window cap, max_entries
  cap, audit_chain_path type)
- Missing audit chain → error reported, empty graph
- Empty chain → empty graph, no errors
- Topic filtering (only entries with topic:<slug> tag)
- Window cutoff excludes old entries
- Window cutoff includes recent entries
- Node deduplication on entry_id
- Provenance + attestor + content excerpt populated correctly
- Edge derivation from explicit Relationship: refines
- Edge derivation from Relationship: confirms
- Edge derivation from Relationship: contradicts +
  potential_contradiction (both → "contradicts")
- "new" relationship line emits no edge
- Temporal-only fallback edge when no Relationship line
- Edge to a target outside the topic window is dropped
- Multiple relationship kinds in one graph
- Edge counts per kind in metadata
- Generated_at field present in output
- Topic slug echoed in output
- Side-effects = read_only
- Max entries cap honored
- Synthetic seq fallback when no explicit entry_id
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.topic_genealogy_build import (
    TopicGenealogyBuildTool,
)


def _ctx():
    return ToolContext(
        instance_id="t", agent_dna="a" * 12,
        role="synthesizer", genre="researcher",
        session_id=None,
    )


def _run(args):
    return asyncio.run(TopicGenealogyBuildTool().execute(args, _ctx()))


def _write_chain(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _mk_entry(
    entry_id: str,
    ts: float,
    tags: list[str],
    content: str,
) -> dict:
    return {
        "ts":      ts,
        "tags":    tags,
        "content": content,
        "payload": {"entry_id": entry_id},
    }


class TestValidation:
    def test_topic_slug_required(self):
        with pytest.raises(ToolValidationError, match="topic_slug"):
            TopicGenealogyBuildTool().validate({})

    def test_topic_slug_must_be_kebab_case(self):
        with pytest.raises(ToolValidationError, match="kebab-case"):
            TopicGenealogyBuildTool().validate({
                "topic_slug": "Bad Slug",
            })

    def test_topic_slug_blocks_traversal(self):
        with pytest.raises(ToolValidationError, match="kebab-case"):
            TopicGenealogyBuildTool().validate({
                "topic_slug": "../etc/passwd",
            })

    def test_window_days_must_be_positive(self):
        with pytest.raises(ToolValidationError, match="window_days"):
            TopicGenealogyBuildTool().validate({
                "topic_slug":   "diffusion-models",
                "window_days":  0,
            })

    def test_window_days_capped_at_730(self):
        with pytest.raises(ToolValidationError, match="window_days"):
            TopicGenealogyBuildTool().validate({
                "topic_slug":   "diffusion-models",
                "window_days":  10_000,
            })

    def test_max_entries_capped(self):
        with pytest.raises(ToolValidationError, match="max_entries"):
            TopicGenealogyBuildTool().validate({
                "topic_slug":  "diffusion-models",
                "max_entries": 10_000,
            })

    def test_audit_chain_path_must_be_string(self):
        with pytest.raises(
            ToolValidationError, match="audit_chain_path",
        ):
            TopicGenealogyBuildTool().validate({
                "topic_slug":       "diffusion-models",
                "audit_chain_path": 42,
            })

    def test_valid_args_accept(self):
        TopicGenealogyBuildTool().validate({
            "topic_slug":   "diffusion-models",
            "window_days":  30,
            "max_entries":  100,
        })  # no raise


class TestExecuteEmptyAndMissing:
    def test_missing_chain_reports_error(self, tmp_path):
        result = _run({
            "topic_slug":       "diffusion-models",
            "audit_chain_path": str(tmp_path / "missing.jsonl"),
        })
        assert result.output["node_count"] == 0
        assert result.output["edge_count"] == 0
        assert any(
            "audit chain not found" in e
            for e in result.output["errors"]
        )

    def test_empty_chain_returns_empty_graph(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        chain.write_text("", encoding="utf-8")
        result = _run({
            "topic_slug":       "diffusion-models",
            "audit_chain_path": str(chain),
        })
        assert result.output["node_count"] == 0
        assert result.output["edge_count"] == 0
        assert result.output["errors"] == []

    def test_no_matching_topic_returns_empty(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry("e1", now, ["topic:other-topic"], "claim"),
            _mk_entry("e2", now, ["topic:another-topic"], "claim"),
        ])
        result = _run({
            "topic_slug":       "diffusion-models",
            "audit_chain_path": str(chain),
        })
        assert result.output["node_count"] == 0
        assert result.output["edges"] == []


class TestNodeCollection:
    def test_filters_by_topic_tag(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry("e1", now - 100, ["topic:diffusion-models"], "c1"),
            _mk_entry("e2", now - 90, ["topic:other"], "c2"),
            _mk_entry("e3", now - 80, ["topic:diffusion-models"], "c3"),
        ])
        result = _run({
            "topic_slug":       "diffusion-models",
            "audit_chain_path": str(chain),
        })
        assert result.output["node_count"] == 2
        ids = {n["entry_id"] for n in result.output["nodes"]}
        assert ids == {"e1", "e3"}

    def test_excludes_old_entries_via_window(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        old = now - 86400 * 30  # 30 days ago
        _write_chain(chain, [
            _mk_entry("e1", old, ["topic:t"], "old"),
            _mk_entry("e2", now, ["topic:t"], "new"),
        ])
        result = _run({
            "topic_slug":       "t",
            "window_days":      7,
            "audit_chain_path": str(chain),
        })
        ids = {n["entry_id"] for n in result.output["nodes"]}
        assert ids == {"e2"}

    def test_deduplicates_entry_ids(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry("dup", now - 10, ["topic:t"], "first"),
            _mk_entry("dup", now, ["topic:t"], "second"),
        ])
        result = _run({
            "topic_slug":       "t",
            "audit_chain_path": str(chain),
        })
        assert result.output["node_count"] == 1

    def test_extracts_attestor_and_source_url(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry("e1", now, [
                "topic:t",
                "attestor:Librarian-D1",
                "provenance:https://arxiv.org/abs/2010.02502",
            ], "claim text"),
        ])
        result = _run({
            "topic_slug":       "t",
            "audit_chain_path": str(chain),
        })
        node = result.output["nodes"][0]
        assert node["attestor"] == "Librarian-D1"
        assert node["source_url"] == "https://arxiv.org/abs/2010.02502"
        assert "claim text" in node["content_excerpt"]

    def test_synthetic_seq_id_when_no_explicit_id(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        # Entry without payload.entry_id but with a seq field.
        _write_chain(chain, [
            {
                "ts":      now,
                "tags":    ["topic:t"],
                "content": "claim",
                "seq":     42,
            },
        ])
        result = _run({
            "topic_slug":       "t",
            "audit_chain_path": str(chain),
        })
        assert result.output["node_count"] == 1
        assert result.output["nodes"][0]["entry_id"] == "seq:42"


class TestEdgeDerivation:
    def test_refines_edge(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry("seed", now - 100, ["topic:t"], "Relationship: new\nclaim"),
            _mk_entry(
                "ref", now,
                ["topic:t"],
                "Relationship: refines:seed\nimproved claim",
            ),
        ])
        result = _run({
            "topic_slug":       "t",
            "audit_chain_path": str(chain),
        })
        edges = result.output["edges"]
        assert len(edges) == 1
        assert edges[0]["from_entry_id"] == "ref"
        assert edges[0]["to_entry_id"] == "seed"
        assert edges[0]["edge_kind"] == "refines"

    def test_confirms_edge(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry("seed", now - 100, ["topic:t"], "Relationship: new"),
            _mk_entry(
                "conf", now,
                ["topic:t"],
                "Relationship: confirms:seed\nsecond source",
            ),
        ])
        result = _run({
            "topic_slug":       "t",
            "audit_chain_path": str(chain),
        })
        edges = result.output["edges"]
        assert any(
            e["edge_kind"] == "confirms"
            and e["from_entry_id"] == "conf"
            and e["to_entry_id"] == "seed"
            for e in edges
        )

    def test_potential_contradiction_maps_to_contradicts(
        self, tmp_path,
    ):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry("seed", now - 100, ["topic:t"], "Relationship: new"),
            _mk_entry(
                "anti", now,
                ["topic:t"],
                "Relationship: potential_contradiction:seed\nflag",
            ),
        ])
        result = _run({
            "topic_slug":       "t",
            "audit_chain_path": str(chain),
        })
        edges = result.output["edges"]
        assert any(
            e["edge_kind"] == "contradicts"
            and e["to_entry_id"] == "seed"
            for e in edges
        )

    def test_contradicts_edge_keyword(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry("seed", now - 100, ["topic:t"], "Relationship: new"),
            _mk_entry(
                "anti", now,
                ["topic:t"],
                "Relationship: contradicts:seed\nopposing claim",
            ),
        ])
        result = _run({
            "topic_slug":       "t",
            "audit_chain_path": str(chain),
        })
        assert any(
            e["edge_kind"] == "contradicts"
            for e in result.output["edges"]
        )

    def test_new_relationship_emits_no_edge(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry("e1", now, ["topic:t"], "Relationship: new\nseed claim"),
        ])
        result = _run({
            "topic_slug":       "t",
            "audit_chain_path": str(chain),
        })
        assert result.output["edge_count"] == 0

    def test_temporal_only_fallback(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry("e1", now - 100, ["topic:t"], "claim one"),
            _mk_entry("e2", now, ["topic:t"], "claim two"),
        ])
        result = _run({
            "topic_slug":       "t",
            "audit_chain_path": str(chain),
        })
        edges = result.output["edges"]
        assert len(edges) == 1
        assert edges[0]["edge_kind"] == "temporal_only"
        assert edges[0]["from_entry_id"] == "e2"
        assert edges[0]["to_entry_id"] == "e1"

    def test_edge_to_out_of_window_target_drops(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        old = now - 86400 * 30  # 30 days ago
        _write_chain(chain, [
            _mk_entry("old", old, ["topic:t"], "Relationship: new"),
            _mk_entry(
                "ref", now,
                ["topic:t"],
                "Relationship: refines:old\nstill referring to old",
            ),
        ])
        result = _run({
            "topic_slug":       "t",
            "window_days":      7,
            "audit_chain_path": str(chain),
        })
        # The "old" target isn't in this window so the refines
        # edge can't be drawn; the only node is "ref" with no
        # prior node to fall back to.
        assert result.output["node_count"] == 1
        assert result.output["edges"] == []

    def test_mixed_relationship_kinds_in_one_graph(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry("a", now - 300, ["topic:t"], "Relationship: new\nclaim A"),
            _mk_entry(
                "b", now - 200,
                ["topic:t"],
                "Relationship: refines:a\nclaim B refines A",
            ),
            _mk_entry(
                "c", now - 100,
                ["topic:t"],
                "Relationship: confirms:a\nclaim C confirms A",
            ),
            _mk_entry(
                "d", now,
                ["topic:t"],
                "Relationship: contradicts:b\nclaim D contradicts B",
            ),
        ])
        result = _run({
            "topic_slug":       "t",
            "audit_chain_path": str(chain),
        })
        kinds = result.metadata["edge_kinds"]
        assert kinds.get("refines") == 1
        assert kinds.get("confirms") == 1
        assert kinds.get("contradicts") == 1


class TestOutputShape:
    def test_topic_slug_echoed(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        chain.write_text("", encoding="utf-8")
        result = _run({
            "topic_slug":       "diffusion-models",
            "audit_chain_path": str(chain),
        })
        assert result.output["topic_slug"] == "diffusion-models"

    def test_generated_at_iso(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        chain.write_text("", encoding="utf-8")
        result = _run({
            "topic_slug":       "t",
            "audit_chain_path": str(chain),
        })
        assert result.output["generated_at"].endswith("Z")

    def test_side_effects_is_read_only(self):
        assert TopicGenealogyBuildTool().side_effects == "read_only"

    def test_max_entries_cap_respected(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        entries = [
            _mk_entry(f"e{i}", now - (100 - i), ["topic:t"], f"c{i}")
            for i in range(10)
        ]
        _write_chain(chain, entries)
        result = _run({
            "topic_slug":       "t",
            "audit_chain_path": str(chain),
            "max_entries":      3,
        })
        assert result.output["node_count"] == 3
