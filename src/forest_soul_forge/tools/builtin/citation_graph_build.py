"""``citation_graph_build.v1`` — ADR-0090 Phase B citation grapher.

Builds a directed citation graph from a list of (claim, sources)
pairs. Nodes are claims (one per unique normalized claim text);
edges are claim→source edges (one per (claim, source) pair).

Deterministic: node IDs are the SHA-256 of the normalized claim
text (first 12 hex chars, ``cl_`` prefixed); source IDs are
SHA-256 of the source URL or catalog_entry_id (``src_`` prefixed).
Two calls with the same inputs produce the same graph.

Read-only. The lab_synthesizer (D10 Phase B) is the primary
consumer — every synthesis report includes the citation graph
as a deliverable so the operator can audit which sources support
which conclusions.

## Inputs

  claim_records (list[dict], required): per-claim records. Each
    record:
      - claim (str, required): the claim text. Normalized
        (lowercase, whitespace-collapsed) for node-ID hashing;
        the original text is preserved on the node.
      - sources (list[dict], required): one or more source
        references. Each:
          - source_id (str, optional): pre-computed source ID;
            if absent, derived from source_url or
            catalog_entry_id.
          - source_url (str, optional): URL of the source.
          - catalog_entry_id (str, optional): D1 catalog
            entry ID.
          - source_type (str, optional): one of
            {web / catalog / memory / verify_claim}. Default
            inferred from which of {source_url,
            catalog_entry_id} is present.
          - excerpt (str, optional): excerpt supporting the
            claim (≤500 chars).
      - claim_kind (str, optional): one of {primary, sub_claim,
        counter}. Default ``primary``.
      - verdict (str, optional): verify_claim verdict for the
        claim (CONFIRMED / REFUTED / INCONCLUSIVE / UNKNOWN).
        Recorded on the node; not used for graph topology.

  topic_slug (str, optional): topic this graph addresses.
    Recorded on the output for operator audit.

## Output

  {
    "topic_slug":      str,
    "built_at":        str (ISO Z),
    "node_count":      int,
    "edge_count":      int,
    "source_count":    int,
    "nodes":           [{"node_id", "claim_text", "claim_kind",
                         "verdict", "source_ids"}, ...],
    "edges":           [{"from_node", "to_source", "excerpt"}, ...],
    "sources":         [{"source_id", "source_type", "source_url",
                         "catalog_entry_id"}, ...],
    "metrics":         {
      "claims_with_sources":      int,
      "claims_without_sources":   int,
      "avg_sources_per_claim":    float,
      "verdict_counts":           {verdict: int},
      "kind_counts":              {kind: int},
    }
  }

side_effects=read_only.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_MAX_CLAIMS = 200
_MAX_SOURCES_PER_CLAIM = 25
_MAX_CLAIM_LEN = 2000
_MAX_EXCERPT_LEN = 500
_MAX_SOURCE_REF_LEN = 1000
_VALID_KINDS = {"primary", "sub_claim", "counter"}
_VALID_VERDICTS = {"CONFIRMED", "REFUTED", "INCONCLUSIVE", "UNKNOWN"}
_VALID_SOURCE_TYPES = {"web", "catalog", "memory", "verify_claim"}
_WS_RE = re.compile(r"\s+")


class CitationGraphBuildTool:
    """Build a directed claim → source citation graph."""

    name = "citation_graph_build"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        recs = args.get("claim_records")
        if not isinstance(recs, list) or not recs:
            raise ToolValidationError(
                "claim_records must be a non-empty list"
            )
        if len(recs) > _MAX_CLAIMS:
            raise ToolValidationError(
                f"claim_records must have <= {_MAX_CLAIMS} entries"
            )
        for i, r in enumerate(recs):
            if not isinstance(r, dict):
                raise ToolValidationError(
                    f"claim_records[{i}] must be an object"
                )
            claim = r.get("claim")
            if not isinstance(claim, str) or not claim.strip():
                raise ToolValidationError(
                    f"claim_records[{i}].claim is required"
                )
            if len(claim) > _MAX_CLAIM_LEN:
                raise ToolValidationError(
                    f"claim_records[{i}].claim must be <= "
                    f"{_MAX_CLAIM_LEN} chars"
                )
            kind = r.get("claim_kind")
            if kind is not None and kind not in _VALID_KINDS:
                raise ToolValidationError(
                    f"claim_records[{i}].claim_kind must be one of "
                    f"{sorted(_VALID_KINDS)}"
                )
            verdict = r.get("verdict")
            if verdict is not None and verdict not in _VALID_VERDICTS:
                raise ToolValidationError(
                    f"claim_records[{i}].verdict must be one of "
                    f"{sorted(_VALID_VERDICTS)}"
                )
            sources = r.get("sources")
            if not isinstance(sources, list):
                raise ToolValidationError(
                    f"claim_records[{i}].sources must be a list"
                )
            if len(sources) > _MAX_SOURCES_PER_CLAIM:
                raise ToolValidationError(
                    f"claim_records[{i}].sources must have <= "
                    f"{_MAX_SOURCES_PER_CLAIM} entries"
                )
            for j, s in enumerate(sources):
                if not isinstance(s, dict):
                    raise ToolValidationError(
                        f"claim_records[{i}].sources[{j}] "
                        f"must be an object"
                    )
                st = s.get("source_type")
                if st is not None and st not in _VALID_SOURCE_TYPES:
                    raise ToolValidationError(
                        f"claim_records[{i}].sources[{j}].source_type "
                        f"must be one of {sorted(_VALID_SOURCE_TYPES)}"
                    )
                for k in ("source_id", "source_url",
                          "catalog_entry_id", "excerpt"):
                    v = s.get(k)
                    if v is not None and not isinstance(v, str):
                        raise ToolValidationError(
                            f"claim_records[{i}].sources[{j}].{k} "
                            f"must be a string"
                        )
                    if isinstance(v, str) and k == "excerpt":
                        if len(v) > _MAX_EXCERPT_LEN:
                            raise ToolValidationError(
                                f"claim_records[{i}].sources[{j}].excerpt "
                                f"must be <= {_MAX_EXCERPT_LEN} chars"
                            )
                    elif isinstance(v, str) and len(v) > _MAX_SOURCE_REF_LEN:
                        raise ToolValidationError(
                            f"claim_records[{i}].sources[{j}].{k} "
                            f"must be <= {_MAX_SOURCE_REF_LEN} chars"
                        )

        slug = args.get("topic_slug")
        if slug is not None and not isinstance(slug, str):
            raise ToolValidationError("topic_slug must be a string")

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        recs = list(args["claim_records"])
        slug = args.get("topic_slug") or ""

        seen_nodes: dict[str, dict[str, Any]] = {}
        seen_sources: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        claims_with_sources = 0
        claims_without_sources = 0
        verdict_counts: dict[str, int] = {}
        kind_counts: dict[str, int] = {}

        for r in recs:
            claim_text = r["claim"]
            kind = r.get("claim_kind") or "primary"
            verdict = r.get("verdict") or "UNKNOWN"

            node_id = _derive_claim_id(claim_text)
            if node_id not in seen_nodes:
                seen_nodes[node_id] = {
                    "node_id":    node_id,
                    "claim_text": claim_text,
                    "claim_kind": kind,
                    "verdict":    verdict,
                    "source_ids": [],
                }
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
            verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

            sources = r["sources"]
            if not sources:
                claims_without_sources += 1
                continue
            claims_with_sources += 1

            for s in sources:
                source_id = _resolve_source_id(s)
                source_type = (
                    s.get("source_type") or _infer_source_type(s)
                )
                if source_id not in seen_sources:
                    seen_sources[source_id] = {
                        "source_id":        source_id,
                        "source_type":      source_type,
                        "source_url":       s.get("source_url") or "",
                        "catalog_entry_id": s.get("catalog_entry_id") or "",
                    }
                if source_id not in seen_nodes[node_id]["source_ids"]:
                    seen_nodes[node_id]["source_ids"].append(source_id)
                edges.append({
                    "from_node": node_id,
                    "to_source": source_id,
                    "excerpt":   s.get("excerpt") or "",
                })

        n_nodes = len(seen_nodes)
        n_edges = len(edges)
        n_sources = len(seen_sources)
        total_claims = claims_with_sources + claims_without_sources
        avg_sources = (
            (n_edges / total_claims) if total_claims else 0.0
        )

        body = {
            "topic_slug":    slug,
            "built_at":      datetime.now(timezone.utc)
                                       .replace(tzinfo=None)
                                       .isoformat(timespec="seconds")
                                       + "Z",
            "node_count":    n_nodes,
            "edge_count":    n_edges,
            "source_count":  n_sources,
            "nodes":         sorted(seen_nodes.values(),
                                    key=lambda n: n["node_id"]),
            "edges":         edges,
            "sources":       sorted(seen_sources.values(),
                                    key=lambda s: s["source_id"]),
            "metrics":       {
                "claims_with_sources":     claims_with_sources,
                "claims_without_sources":  claims_without_sources,
                "avg_sources_per_claim":   round(avg_sources, 4),
                "verdict_counts":          verdict_counts,
                "kind_counts":             kind_counts,
            },
        }
        return ToolResult(
            output=body,
            metadata={
                "topic_slug":   slug,
                "node_count":   n_nodes,
                "edge_count":   n_edges,
                "source_count": n_sources,
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"built citation graph for {slug!r}: "
                f"{n_nodes} claims / {n_edges} edges / "
                f"{n_sources} sources"
            ),
        )


def _normalize_claim(text: str) -> str:
    return _WS_RE.sub(" ", text.strip().lower())


def _derive_claim_id(claim_text: str) -> str:
    normalized = _normalize_claim(claim_text)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"cl_{digest[:12]}"


def _resolve_source_id(s: dict[str, Any]) -> str:
    sid = s.get("source_id")
    if isinstance(sid, str) and sid.strip():
        return sid.strip()
    url = s.get("source_url") or ""
    cat = s.get("catalog_entry_id") or ""
    ref = url or cat
    if not ref:
        # all-empty source = bucket into a stable "unsourced" id
        return "src_unsourced"
    digest = hashlib.sha256(ref.encode("utf-8")).hexdigest()
    return f"src_{digest[:12]}"


def _infer_source_type(s: dict[str, Any]) -> str:
    if s.get("source_url"):
        return "web"
    if s.get("catalog_entry_id"):
        return "catalog"
    return "memory"
