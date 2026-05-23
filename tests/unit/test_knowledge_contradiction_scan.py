"""Tests for ADR-0086 Phase C — knowledge_contradiction_scan.v1.

Coverage:
- Argument validation: topic_slug shape, window cap, min_confidence
  range, scope gating (cross_agent forbidden), audit_chain_path type,
  agent_role type
- Missing chain → error reported, empty pairs
- Empty chain → empty result
- Single-agent scope gate: entries from other roles are filtered out
- Explicit-flag detection from "Relationship: contradicts:<id>"
- Explicit-flag detection from "Relationship: potential_contradiction:<id>"
- Lexical-cue detection: opposite-pair (always vs never)
- Lexical-cue detection: opposite-pair (more vs less)
- Lexical-cue detection: negation-flip on shared term
- Lexical-cue with no shared terms returns no pair
- Pair dedup: explicit-flag wins over lexical-cue
- min_confidence threshold drops lexical-cue pairs
- min_confidence=1.0 keeps only explicit_flag
- Topic-tag filtering: only matching topic entries
- Window cutoff excludes old entries
- Metadata counts (explicit_flag_count, lexical_cue_count)
- Output scope field is always "single_agent"
- Side-effects=read_only
- agent_role override from args
- Synthetic seq id fallback
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.knowledge_contradiction_scan import (
    KnowledgeContradictionScanTool,
)


def _ctx(role="knowledge_verifier"):
    return ToolContext(
        instance_id="t", agent_dna="a" * 12,
        role=role, genre="guardian",
        session_id=None,
    )


def _run(args, role="knowledge_verifier"):
    return asyncio.run(
        KnowledgeContradictionScanTool().execute(args, _ctx(role)),
    )


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
    role: str = "knowledge_verifier",
) -> dict:
    return {
        "ts":      ts,
        "tags":    tags,
        "content": content,
        "role":    role,
        "payload": {"entry_id": entry_id},
    }


class TestValidation:
    def test_topic_slug_required(self):
        with pytest.raises(ToolValidationError, match="topic_slug"):
            KnowledgeContradictionScanTool().validate({})

    def test_topic_slug_kebab_case_only(self):
        with pytest.raises(ToolValidationError, match="kebab-case"):
            KnowledgeContradictionScanTool().validate({
                "topic_slug": "Mixed Case Bad",
            })

    def test_window_days_positive(self):
        with pytest.raises(ToolValidationError, match="window_days"):
            KnowledgeContradictionScanTool().validate({
                "topic_slug":  "t",
                "window_days": -1,
            })

    def test_window_days_capped(self):
        with pytest.raises(ToolValidationError, match="window_days"):
            KnowledgeContradictionScanTool().validate({
                "topic_slug":  "t",
                "window_days": 999,
            })

    def test_min_confidence_in_unit_range(self):
        with pytest.raises(ToolValidationError, match="min_confidence"):
            KnowledgeContradictionScanTool().validate({
                "topic_slug":     "t",
                "min_confidence": 1.5,
            })

    def test_cross_agent_scope_forbidden(self):
        with pytest.raises(
            ToolValidationError, match="cross_agent",
        ):
            KnowledgeContradictionScanTool().validate({
                "topic_slug": "t",
                "scope":      "cross_agent",
            })

    def test_unknown_scope_rejected(self):
        with pytest.raises(ToolValidationError, match="scope"):
            KnowledgeContradictionScanTool().validate({
                "topic_slug": "t",
                "scope":      "weird",
            })

    def test_single_agent_scope_accepted(self):
        KnowledgeContradictionScanTool().validate({
            "topic_slug": "t",
            "scope":      "single_agent",
        })

    def test_audit_chain_path_type(self):
        with pytest.raises(
            ToolValidationError, match="audit_chain_path",
        ):
            KnowledgeContradictionScanTool().validate({
                "topic_slug":       "t",
                "audit_chain_path": 1,
            })


class TestEmptyAndMissing:
    def test_missing_chain(self, tmp_path):
        result = _run({
            "topic_slug":       "t",
            "audit_chain_path": str(tmp_path / "missing.jsonl"),
        })
        assert result.output["candidate_count"] == 0
        assert any(
            "audit chain not found" in e
            for e in result.output["errors"]
        )

    def test_empty_chain(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        chain.write_text("", encoding="utf-8")
        result = _run({
            "topic_slug":       "t",
            "audit_chain_path": str(chain),
        })
        assert result.output["candidate_count"] == 0
        assert result.output["contradiction_pairs"] == []
        assert result.output["scope"] == "single_agent"


class TestSingleAgentScopeGate:
    def test_filters_out_other_roles(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry(
                "mine", now - 100,
                ["topic:t"], "claim from mine",
                role="knowledge_verifier",
            ),
            _mk_entry(
                "theirs", now - 90,
                ["topic:t"], "claim from theirs",
                role="other_role",
            ),
        ])
        result = _run({
            "topic_slug":       "t",
            "audit_chain_path": str(chain),
        })
        assert result.output["candidate_count"] == 1

    def test_agent_role_override(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry(
                "x", now,
                ["topic:t"], "claim",
                role="librarian",
            ),
        ])
        result = _run({
            "topic_slug":       "t",
            "agent_role":       "librarian",
            "audit_chain_path": str(chain),
        })
        assert result.output["candidate_count"] == 1


class TestExplicitFlagDetection:
    def test_contradicts_relationship(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry("seed", now - 100, ["topic:t"], "Relationship: new"),
            _mk_entry(
                "anti", now,
                ["topic:t"],
                "Relationship: contradicts:seed\nclaim",
            ),
        ])
        result = _run({
            "topic_slug":       "t",
            "audit_chain_path": str(chain),
        })
        pairs = [
            p for p in result.output["contradiction_pairs"]
            if p["detection_kind"] == "explicit_flag"
        ]
        assert len(pairs) == 1
        assert pairs[0]["confidence"] == 1.0

    def test_potential_contradiction_relationship(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry("seed", now - 100, ["topic:t"], "Relationship: new"),
            _mk_entry(
                "flag", now,
                ["topic:t"],
                "Relationship: potential_contradiction:seed\nclaim",
            ),
        ])
        result = _run({
            "topic_slug":       "t",
            "audit_chain_path": str(chain),
        })
        explicit = [
            p for p in result.output["contradiction_pairs"]
            if p["detection_kind"] == "explicit_flag"
        ]
        assert len(explicit) == 1


class TestLexicalCueDetection:
    def test_opposite_pair_always_never(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry(
                "a", now - 100,
                ["topic:t"],
                "Models always converge under SGD",
            ),
            _mk_entry(
                "b", now,
                ["topic:t"],
                "Models never converge under SGD",
            ),
        ])
        result = _run({
            "topic_slug":       "t",
            "audit_chain_path": str(chain),
        })
        pairs = [
            p for p in result.output["contradiction_pairs"]
            if p["detection_kind"] == "lexical_cue"
        ]
        assert len(pairs) == 1
        assert pairs[0]["confidence"] == 0.4

    def test_opposite_pair_more_less(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry(
                "a", now - 100,
                ["topic:t"],
                "Higher learning rate gives more stable training",
            ),
            _mk_entry(
                "b", now,
                ["topic:t"],
                "Higher learning rate gives less stable training",
            ),
        ])
        result = _run({
            "topic_slug":       "t",
            "audit_chain_path": str(chain),
        })
        pairs = result.output["contradiction_pairs"]
        assert any(p["detection_kind"] == "lexical_cue" for p in pairs)

    def test_negation_flip_on_shared_term(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry(
                "a", now - 100,
                ["topic:t"],
                "Diffusion models require careful schedule tuning",
            ),
            _mk_entry(
                "b", now,
                ["topic:t"],
                "Diffusion models do not require careful schedule tuning",
            ),
        ])
        result = _run({
            "topic_slug":       "t",
            "audit_chain_path": str(chain),
        })
        pairs = result.output["contradiction_pairs"]
        assert any(p["detection_kind"] == "lexical_cue" for p in pairs)

    def test_no_shared_terms_no_pair(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry("a", now - 100, ["topic:t"], "alpha"),
            _mk_entry("b", now, ["topic:t"], "beta"),
        ])
        result = _run({
            "topic_slug":       "t",
            "audit_chain_path": str(chain),
        })
        # 2 candidates, but no lexical cue can fire on
        # 5-character single-word entries → no pair
        assert result.output["contradiction_pairs"] == []


class TestDedupAndFiltering:
    def test_explicit_flag_beats_lexical_cue(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry(
                "a", now - 100,
                ["topic:t"],
                "Always converges with negation discussion",
            ),
            _mk_entry(
                "b", now,
                ["topic:t"],
                "Relationship: contradicts:a\n"
                "Never converges with negation discussion",
            ),
        ])
        result = _run({
            "topic_slug":       "t",
            "audit_chain_path": str(chain),
        })
        pairs = result.output["contradiction_pairs"]
        # Only one pair total — the explicit_flag dedups the
        # lexical_cue candidate.
        assert len(pairs) == 1
        assert pairs[0]["detection_kind"] == "explicit_flag"
        assert pairs[0]["confidence"] == 1.0

    def test_min_confidence_drops_lexical_cue(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry(
                "a", now - 100,
                ["topic:t"],
                "Always converges under SGD",
            ),
            _mk_entry(
                "b", now,
                ["topic:t"],
                "Never converges under SGD",
            ),
        ])
        result = _run({
            "topic_slug":       "t",
            "audit_chain_path": str(chain),
            "min_confidence":   0.5,
        })
        assert result.output["contradiction_pairs"] == []

    def test_min_confidence_keeps_explicit_flag(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry("a", now - 100, ["topic:t"], "Relationship: new"),
            _mk_entry(
                "b", now,
                ["topic:t"],
                "Relationship: contradicts:a\nclaim",
            ),
        ])
        result = _run({
            "topic_slug":       "t",
            "audit_chain_path": str(chain),
            "min_confidence":   0.9,
        })
        assert len(result.output["contradiction_pairs"]) == 1


class TestFilteringAndOutput:
    def test_topic_filter(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry("a", now, ["topic:other"], "Always X"),
            _mk_entry("b", now, ["topic:other"], "Never X"),
        ])
        result = _run({
            "topic_slug":       "diffusion-models",
            "audit_chain_path": str(chain),
        })
        assert result.output["candidate_count"] == 0

    def test_window_filter(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        old = now - 86400 * 30
        _write_chain(chain, [
            _mk_entry(
                "old", old, ["topic:t"], "Always converges",
            ),
            _mk_entry(
                "new", now, ["topic:t"], "Never converges",
            ),
        ])
        result = _run({
            "topic_slug":       "t",
            "window_days":      7,
            "audit_chain_path": str(chain),
        })
        # Only "new" is in the window; no pair can form.
        assert result.output["candidate_count"] == 1
        assert result.output["contradiction_pairs"] == []

    def test_metadata_counts(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [
            _mk_entry(
                "a", now - 200, ["topic:t"], "Relationship: new",
            ),
            _mk_entry(
                "b", now - 100, ["topic:t"],
                "Relationship: contradicts:a\nclaim",
            ),
            _mk_entry(
                "c", now - 50, ["topic:t"],
                "Always converges under negation testing",
            ),
            _mk_entry(
                "d", now, ["topic:t"],
                "Never converges under negation testing",
            ),
        ])
        result = _run({
            "topic_slug":       "t",
            "audit_chain_path": str(chain),
        })
        md = result.metadata
        assert md["explicit_flag_count"] == 1
        assert md["lexical_cue_count"] >= 1

    def test_scope_always_single_agent(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        chain.write_text("", encoding="utf-8")
        result = _run({
            "topic_slug":       "t",
            "audit_chain_path": str(chain),
        })
        assert result.output["scope"] == "single_agent"

    def test_side_effects_read_only(self):
        assert (
            KnowledgeContradictionScanTool().side_effects
            == "read_only"
        )

    def test_synthetic_seq_id_fallback(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        # Entry with no payload.entry_id but with seq
        _write_chain(chain, [
            {
                "ts":      now,
                "tags":    ["topic:t"],
                "content": "claim",
                "role":    "knowledge_verifier",
                "seq":     7,
            },
        ])
        result = _run({
            "topic_slug":       "t",
            "audit_chain_path": str(chain),
        })
        assert result.output["candidate_count"] == 1
