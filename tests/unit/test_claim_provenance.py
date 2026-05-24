"""Tests for ADR-0090 Phase C — claim_provenance.v1 builtin tool."""
from __future__ import annotations

import asyncio

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.claim_provenance import (
    ClaimProvenanceTool,
)


def _ctx():
    return ToolContext(
        instance_id="debate_mod_test",
        agent_dna="a" * 12,
        role="debate_moderator",
        genre="researcher",
        session_id=None,
    )


def _run(args):
    return asyncio.run(ClaimProvenanceTool().execute(args, _ctx()))


# Sample graph fixture used by multiple tests
def _graph():
    return {
        "nodes": [
            {
                "node_id":    "cl_aaa111",
                "claim_text": "X is true.",
                "claim_kind": "primary",
                "verdict":    "CONFIRMED",
                "source_ids": ["src_111", "src_222"],
            },
            {
                "node_id":    "cl_bbb222",
                "claim_text": "Y supports X.",
                "claim_kind": "sub_claim",
                "verdict":    "INCONCLUSIVE",
                "source_ids": ["src_222", "src_333"],
            },
            {
                "node_id":    "cl_ccc333",
                "claim_text": "Z is unrelated.",
                "claim_kind": "primary",
                "verdict":    "UNKNOWN",
                "source_ids": ["src_444"],
            },
        ],
        "sources": [
            {"source_id": "src_111", "source_type": "web",
             "source_url": "https://a.example", "catalog_entry_id": ""},
            {"source_id": "src_222", "source_type": "web",
             "source_url": "https://b.example", "catalog_entry_id": ""},
            {"source_id": "src_333", "source_type": "catalog",
             "source_url": "", "catalog_entry_id": "cat_42"},
            {"source_id": "src_444", "source_type": "memory",
             "source_url": "", "catalog_entry_id": ""},
        ],
        "edges": [],
    }


class TestValidation:
    def test_citation_graph_required(self):
        with pytest.raises(ToolValidationError, match="citation_graph"):
            ClaimProvenanceTool().validate({"target_node_id": "cl_x"})

    def test_nodes_required(self):
        with pytest.raises(ToolValidationError, match="nodes"):
            ClaimProvenanceTool().validate(
                {"citation_graph": {"sources": []},
                 "target_node_id": "cl_x"}
            )

    def test_sources_required(self):
        with pytest.raises(ToolValidationError, match="sources"):
            ClaimProvenanceTool().validate(
                {"citation_graph": {"nodes": []},
                 "target_node_id": "cl_x"}
            )

    def test_target_required(self):
        with pytest.raises(ToolValidationError, match="target"):
            ClaimProvenanceTool().validate(
                {"citation_graph": {"nodes": [], "sources": []}}
            )

    def test_include_siblings_must_be_bool(self):
        with pytest.raises(ToolValidationError, match="include_siblings"):
            ClaimProvenanceTool().validate({
                "citation_graph": {"nodes": [], "sources": []},
                "target_node_id": "cl_x",
                "include_siblings": "yes",
            })


class TestWalk:
    def test_found_target_returns_sources(self):
        r = _run({
            "citation_graph": _graph(),
            "target_node_id": "cl_aaa111",
        })
        out = r.output
        assert out["found"] is True
        assert out["target_node_id"] == "cl_aaa111"
        assert out["target_claim_text"] == "X is true."
        assert out["target_claim_kind"] == "primary"
        assert out["target_verdict"] == "CONFIRMED"
        assert set(out["source_ids"]) == {"src_111", "src_222"}
        assert len(out["sources"]) == 2

    def test_not_found_returns_found_false(self):
        r = _run({
            "citation_graph": _graph(),
            "target_node_id": "cl_does_not_exist",
        })
        assert r.output["found"] is False
        assert r.output["target_node_id"] == "cl_does_not_exist"
        assert r.output["source_ids"] == []
        assert r.output["siblings"] == []

    def test_target_claim_text_resolves_to_node_id(self):
        # X is true. -> normalized -> "x is true." -> SHA-256 hash
        # The fixture's node_id is "cl_aaa111" which is NOT derived
        # from the text, so we expect "not found" but the
        # node_id derivation should be deterministic.
        r1 = _run({
            "citation_graph": _graph(),
            "target_claim_text": "Some Other Claim",
        })
        r2 = _run({
            "citation_graph": _graph(),
            "target_claim_text": "some  other  claim",
        })
        # Both normalize to "some other claim" -> same node_id
        assert r1.output["target_node_id"] == r2.output["target_node_id"]

    def test_siblings_share_source_with_target(self):
        r = _run({
            "citation_graph": _graph(),
            "target_node_id": "cl_aaa111",
        })
        # cl_bbb222 shares src_222 with cl_aaa111; cl_ccc333 has
        # no shared source.
        sibling_ids = [s["node_id"] for s in r.output["siblings"]]
        assert "cl_bbb222" in sibling_ids
        assert "cl_ccc333" not in sibling_ids

    def test_siblings_sorted_by_shared_count_desc(self):
        # Build a graph where multiple siblings share different
        # numbers of sources.
        g = _graph()
        g["nodes"].append({
            "node_id":    "cl_ddd444",
            "claim_text": "Doubly cites.",
            "claim_kind": "primary",
            "verdict":    "UNKNOWN",
            "source_ids": ["src_111", "src_222"],  # shares both with cl_aaa111
        })
        r = _run({
            "citation_graph": g,
            "target_node_id": "cl_aaa111",
        })
        sibs = r.output["siblings"]
        # cl_ddd444 shares 2; cl_bbb222 shares 1 -> ddd first
        assert sibs[0]["node_id"] == "cl_ddd444"
        assert sibs[0]["shared_count"] == 2
        assert sibs[1]["node_id"] == "cl_bbb222"
        assert sibs[1]["shared_count"] == 1

    def test_include_siblings_false_disables_walk(self):
        r = _run({
            "citation_graph": _graph(),
            "target_node_id": "cl_aaa111",
            "include_siblings": False,
        })
        assert r.output["siblings"] == []
        assert r.output["sibling_count"] == 0

    def test_metrics_record_source_and_sibling_counts(self):
        r = _run({
            "citation_graph": _graph(),
            "target_node_id": "cl_aaa111",
        })
        m = r.output["metrics"]
        assert m["source_count"] == 2
        assert m["sibling_count"] == 1
        assert m["max_shared"] == 1

    def test_orphan_claim_has_no_siblings(self):
        r = _run({
            "citation_graph": _graph(),
            "target_node_id": "cl_ccc333",
        })
        # src_444 not shared by any other node
        assert r.output["sibling_count"] == 0

    def test_empty_graph_returns_not_found(self):
        r = _run({
            "citation_graph": {"nodes": [], "sources": []},
            "target_node_id": "cl_anything",
        })
        assert r.output["found"] is False

    def test_deterministic(self):
        a = _run({
            "citation_graph": _graph(),
            "target_node_id": "cl_aaa111",
        })
        b = _run({
            "citation_graph": _graph(),
            "target_node_id": "cl_aaa111",
        })
        assert a.output["source_ids"] == b.output["source_ids"]
        assert [s["node_id"] for s in a.output["siblings"]] == \
               [s["node_id"] for s in b.output["siblings"]]

    def test_sources_filtered_to_those_in_source_ids(self):
        r = _run({
            "citation_graph": _graph(),
            "target_node_id": "cl_aaa111",
        })
        # cl_aaa111 sources are src_111 + src_222 — should NOT include 333 or 444
        sids = {s["source_id"] for s in r.output["sources"]}
        assert sids == {"src_111", "src_222"}

    def test_target_node_id_overrides_text(self):
        r = _run({
            "citation_graph": _graph(),
            "target_node_id": "cl_aaa111",
            "target_claim_text": "totally different claim text",
        })
        # node_id takes precedence; resolved to cl_aaa111
        assert r.output["target_node_id"] == "cl_aaa111"
        assert r.output["found"] is True

    def test_shared_source_ids_listed_per_sibling(self):
        r = _run({
            "citation_graph": _graph(),
            "target_node_id": "cl_aaa111",
        })
        for sib in r.output["siblings"]:
            assert isinstance(sib["shared_source_ids"], list)
            assert sib["shared_count"] == len(sib["shared_source_ids"])

    def test_metadata_records_target(self):
        r = _run({
            "citation_graph": _graph(),
            "target_node_id": "cl_aaa111",
        })
        assert r.metadata["target_node_id"] == "cl_aaa111"
        assert r.metadata["found"] is True
        assert r.metadata["source_count"] == 2
