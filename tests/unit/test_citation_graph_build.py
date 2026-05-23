"""Tests for ADR-0090 Phase B — citation_graph_build.v1 builtin tool."""
from __future__ import annotations

import asyncio

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.citation_graph_build import (
    CitationGraphBuildTool,
)


def _ctx():
    return ToolContext(
        instance_id="lab_synth_test",
        agent_dna="a" * 12,
        role="lab_synthesizer",
        genre="researcher",
        session_id=None,
    )


def _run(args):
    return asyncio.run(CitationGraphBuildTool().execute(args, _ctx()))


class TestValidation:
    def test_claim_records_required(self):
        with pytest.raises(ToolValidationError, match="claim_records"):
            CitationGraphBuildTool().validate({})

    def test_claim_records_must_be_nonempty(self):
        with pytest.raises(ToolValidationError, match="claim_records"):
            CitationGraphBuildTool().validate({"claim_records": []})

    def test_claim_text_required(self):
        with pytest.raises(ToolValidationError, match="claim"):
            CitationGraphBuildTool().validate(
                {"claim_records": [{"sources": []}]}
            )

    def test_claim_kind_must_be_valid(self):
        with pytest.raises(ToolValidationError, match="claim_kind"):
            CitationGraphBuildTool().validate(
                {"claim_records": [{
                    "claim": "x", "sources": [], "claim_kind": "bogus",
                }]}
            )

    def test_verdict_must_be_valid(self):
        with pytest.raises(ToolValidationError, match="verdict"):
            CitationGraphBuildTool().validate(
                {"claim_records": [{
                    "claim": "x", "sources": [], "verdict": "maybe",
                }]}
            )

    def test_sources_must_be_list(self):
        with pytest.raises(ToolValidationError, match="sources"):
            CitationGraphBuildTool().validate(
                {"claim_records": [{"claim": "x", "sources": "not a list"}]}
            )

    def test_source_type_must_be_valid(self):
        with pytest.raises(ToolValidationError, match="source_type"):
            CitationGraphBuildTool().validate(
                {"claim_records": [{
                    "claim": "x",
                    "sources": [{"source_type": "bogus"}],
                }]}
            )

    def test_excerpt_length_capped(self):
        with pytest.raises(ToolValidationError, match="excerpt"):
            CitationGraphBuildTool().validate(
                {"claim_records": [{
                    "claim": "x",
                    "sources": [{"excerpt": "y" * 1000}],
                }]}
            )


class TestGraphConstruction:
    def test_single_claim_single_source(self):
        result = _run({
            "topic_slug": "t1",
            "claim_records": [{
                "claim": "Diffusion models add noise progressively.",
                "sources": [{
                    "source_url": "https://example.com/paper",
                    "excerpt": "noise is added at each step",
                }],
            }],
        })
        out = result.output
        assert out["node_count"] == 1
        assert out["edge_count"] == 1
        assert out["source_count"] == 1
        assert out["nodes"][0]["claim_text"].startswith("Diffusion models")
        assert out["edges"][0]["from_node"] == out["nodes"][0]["node_id"]

    def test_deterministic_node_id(self):
        a = _run({
            "claim_records": [{
                "claim": "Same claim text.",
                "sources": [{"source_url": "https://e.com/1"}],
            }],
        })
        b = _run({
            "claim_records": [{
                "claim": "Same claim text.",
                "sources": [{"source_url": "https://e.com/1"}],
            }],
        })
        assert a.output["nodes"][0]["node_id"] == b.output["nodes"][0]["node_id"]
        assert a.output["sources"][0]["source_id"] == b.output["sources"][0]["source_id"]

    def test_normalized_claim_collapses_whitespace(self):
        result = _run({
            "claim_records": [
                {"claim": "Same  claim",  "sources": []},
                {"claim": "Same claim",   "sources": []},
                {"claim": "  same claim ", "sources": []},
            ],
        })
        # all three normalize to "same claim"
        assert result.output["node_count"] == 1

    def test_multiple_sources_for_one_claim(self):
        result = _run({
            "claim_records": [{
                "claim": "X is true.",
                "sources": [
                    {"source_url": "https://a.com/1"},
                    {"source_url": "https://a.com/2"},
                    {"source_url": "https://a.com/3"},
                ],
            }],
        })
        assert result.output["node_count"] == 1
        assert result.output["edge_count"] == 3
        assert result.output["source_count"] == 3
        assert len(result.output["nodes"][0]["source_ids"]) == 3

    def test_shared_source_across_claims(self):
        result = _run({
            "claim_records": [
                {"claim": "A is true.",
                 "sources": [{"source_url": "https://shared.com"}]},
                {"claim": "B is true.",
                 "sources": [{"source_url": "https://shared.com"}]},
            ],
        })
        assert result.output["node_count"] == 2
        assert result.output["edge_count"] == 2
        assert result.output["source_count"] == 1

    def test_claim_with_no_sources_records_metric(self):
        result = _run({
            "claim_records": [{
                "claim": "Unsourced claim.",
                "sources": [],
            }],
        })
        assert result.output["node_count"] == 1
        assert result.output["edge_count"] == 0
        assert result.output["metrics"]["claims_without_sources"] == 1
        assert result.output["metrics"]["claims_with_sources"] == 0

    def test_source_type_inference_web(self):
        result = _run({
            "claim_records": [{
                "claim": "X.", "sources": [{"source_url": "https://e.com"}],
            }],
        })
        assert result.output["sources"][0]["source_type"] == "web"

    def test_source_type_inference_catalog(self):
        result = _run({
            "claim_records": [{
                "claim": "X.", "sources": [{"catalog_entry_id": "cat_42"}],
            }],
        })
        assert result.output["sources"][0]["source_type"] == "catalog"

    def test_source_type_explicit_overrides_inference(self):
        result = _run({
            "claim_records": [{
                "claim": "X.",
                "sources": [{
                    "source_url": "https://e.com",
                    "source_type": "memory",
                }],
            }],
        })
        assert result.output["sources"][0]["source_type"] == "memory"

    def test_verdict_counts_aggregated(self):
        result = _run({
            "claim_records": [
                {"claim": "A.", "sources": [], "verdict": "CONFIRMED"},
                {"claim": "B.", "sources": [], "verdict": "CONFIRMED"},
                {"claim": "C.", "sources": [], "verdict": "REFUTED"},
                {"claim": "D.", "sources": [], "verdict": "INCONCLUSIVE"},
            ],
        })
        vc = result.output["metrics"]["verdict_counts"]
        assert vc["CONFIRMED"] == 2
        assert vc["REFUTED"] == 1
        assert vc["INCONCLUSIVE"] == 1

    def test_kind_counts_aggregated(self):
        result = _run({
            "claim_records": [
                {"claim": "A.", "sources": [], "claim_kind": "primary"},
                {"claim": "B.", "sources": [], "claim_kind": "sub_claim"},
                {"claim": "C.", "sources": [], "claim_kind": "counter"},
            ],
        })
        kc = result.output["metrics"]["kind_counts"]
        assert kc == {"primary": 1, "sub_claim": 1, "counter": 1}

    def test_avg_sources_per_claim(self):
        result = _run({
            "claim_records": [
                {"claim": "A.", "sources": [
                    {"source_url": "https://e.com/1"},
                    {"source_url": "https://e.com/2"},
                ]},
                {"claim": "B.", "sources": [
                    {"source_url": "https://e.com/3"},
                ]},
            ],
        })
        # 3 edges / 2 claims = 1.5
        assert result.output["metrics"]["avg_sources_per_claim"] == 1.5

    def test_source_id_uses_explicit_when_provided(self):
        result = _run({
            "claim_records": [{
                "claim": "X.",
                "sources": [{"source_id": "src_custom",
                             "source_url": "https://e.com"}],
            }],
        })
        assert result.output["sources"][0]["source_id"] == "src_custom"
