"""``claim_provenance.v1`` — ADR-0090 Phase C provenance walker.

Walks a citation graph (output of ``citation_graph_build.v1``)
backward from a target claim to its root sources. Surfaces:

  - The target claim's node + its direct source set.
  - Sibling claims that share at least one source with the
    target (co-cited claims — useful for the debate_moderator to
    detect when speakers reiterate an already-grounded point).
  - The root source set with each source's metadata.
  - A flat provenance summary: claim_text → list of (source_id,
    source_url, source_type).

Deterministic. Read-only. Two calls with the same inputs produce
the same trace.

The debate_moderator (D10 Phase C) is the primary consumer:
given a transcript claim, the moderator dispatches this tool to
trace which sources back the claim + which other claims in the
graph already cite those sources. The analyst can also dispatch
it as a follow-up to a synthesis.

## Inputs

  citation_graph (dict, required): the output of
    ``citation_graph_build.v1`` — must contain ``nodes`` (list
    of {node_id, claim_text, source_ids, ...}) and ``sources``
    (list of {source_id, source_type, source_url, ...}).
  target_node_id (str, optional): node_id of the claim to walk.
    If omitted, ``target_claim_text`` MUST be provided; the tool
    derives the node ID by normalizing + hashing the text (same
    derivation as citation_graph_build).
  target_claim_text (str, optional): claim text to walk. Resolves
    to a node_id via the same SHA-256 normalization as
    citation_graph_build.
  include_siblings (bool, optional): when true (default), list
    sibling claims that share at least one source with the target.

## Output

  {
    "walked_at":          str (ISO Z),
    "found":              bool,
    "target_node_id":     str,
    "target_claim_text":  str,
    "target_claim_kind":  str,
    "target_verdict":     str,
    "source_ids":         [str, ...],
    "sources":            [{source_id, source_type, source_url,
                            catalog_entry_id}, ...],
    "sibling_count":      int,
    "siblings":           [{node_id, claim_text, shared_source_ids,
                            shared_count}, ...],
    "metrics":            {
      "source_count":    int,
      "sibling_count":   int,
      "max_shared":      int,
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


_MAX_NODES = 5000
_MAX_SOURCES = 5000
_WS_RE = re.compile(r"\s+")


class ClaimProvenanceTool:
    """Trace a claim's sources + co-cited siblings in a citation graph."""

    name = "claim_provenance"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        graph = args.get("citation_graph")
        if not isinstance(graph, dict):
            raise ToolValidationError(
                "citation_graph must be an object"
            )
        nodes = graph.get("nodes")
        sources = graph.get("sources")
        if not isinstance(nodes, list):
            raise ToolValidationError(
                "citation_graph.nodes must be a list"
            )
        if not isinstance(sources, list):
            raise ToolValidationError(
                "citation_graph.sources must be a list"
            )
        if len(nodes) > _MAX_NODES:
            raise ToolValidationError(
                f"citation_graph.nodes must have <= {_MAX_NODES} entries"
            )
        if len(sources) > _MAX_SOURCES:
            raise ToolValidationError(
                f"citation_graph.sources must have <= {_MAX_SOURCES} entries"
            )

        tnid = args.get("target_node_id")
        tct = args.get("target_claim_text")
        if not tnid and not tct:
            raise ToolValidationError(
                "either target_node_id or target_claim_text is required"
            )
        if tnid is not None and not isinstance(tnid, str):
            raise ToolValidationError("target_node_id must be a string")
        if tct is not None and not isinstance(tct, str):
            raise ToolValidationError("target_claim_text must be a string")

        inc = args.get("include_siblings", True)
        if not isinstance(inc, bool):
            raise ToolValidationError("include_siblings must be a boolean")

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        graph = args["citation_graph"]
        nodes = graph.get("nodes", [])
        sources = graph.get("sources", [])
        include_siblings = args.get("include_siblings", True)

        target_node_id = args.get("target_node_id")
        if not target_node_id:
            text = args.get("target_claim_text", "")
            normalized = _WS_RE.sub(" ", text.strip().lower())
            digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            target_node_id = f"cl_{digest[:12]}"

        nodes_by_id = {n.get("node_id"): n for n in nodes
                       if isinstance(n, dict)}
        sources_by_id = {s.get("source_id"): s for s in sources
                         if isinstance(s, dict)}

        target = nodes_by_id.get(target_node_id)
        found = target is not None

        body: dict[str, Any] = {
            "walked_at": datetime.now(timezone.utc)
                                  .replace(tzinfo=None)
                                  .isoformat(timespec="seconds")
                                  + "Z",
            "found":              found,
            "target_node_id":     target_node_id,
            "target_claim_text":  "",
            "target_claim_kind":  "",
            "target_verdict":     "",
            "source_ids":         [],
            "sources":            [],
            "sibling_count":      0,
            "siblings":           [],
            "metrics": {
                "source_count":  0,
                "sibling_count": 0,
                "max_shared":    0,
            },
        }

        if not found:
            return ToolResult(
                output=body,
                metadata={
                    "target_node_id": target_node_id,
                    "found":          False,
                },
                tokens_used=None, cost_usd=None,
                side_effect_summary=(
                    f"provenance walk: target {target_node_id} not in graph"
                ),
            )

        source_ids = list(target.get("source_ids") or [])
        body["target_claim_text"] = target.get("claim_text", "")
        body["target_claim_kind"] = target.get("claim_kind", "")
        body["target_verdict"] = target.get("verdict", "")
        body["source_ids"] = source_ids
        body["sources"] = [
            sources_by_id[sid] for sid in source_ids
            if sid in sources_by_id
        ]

        max_shared = 0
        if include_siblings and source_ids:
            tgt_src_set = set(source_ids)
            siblings: list[dict[str, Any]] = []
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                nid = node.get("node_id")
                if nid == target_node_id:
                    continue
                node_srcs = set(node.get("source_ids") or [])
                shared = tgt_src_set & node_srcs
                if not shared:
                    continue
                shared_ids = sorted(shared)
                siblings.append({
                    "node_id":           nid,
                    "claim_text":        node.get("claim_text", ""),
                    "shared_source_ids": shared_ids,
                    "shared_count":      len(shared_ids),
                })
                if len(shared_ids) > max_shared:
                    max_shared = len(shared_ids)
            siblings.sort(
                key=lambda s: (-s["shared_count"], s["node_id"])
            )
            body["siblings"] = siblings
            body["sibling_count"] = len(siblings)

        body["metrics"] = {
            "source_count":  len(source_ids),
            "sibling_count": body["sibling_count"],
            "max_shared":    max_shared,
        }

        return ToolResult(
            output=body,
            metadata={
                "target_node_id": target_node_id,
                "found":          True,
                "source_count":   len(source_ids),
                "sibling_count":  body["sibling_count"],
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"provenance walk for {target_node_id}: "
                f"{len(source_ids)} sources, "
                f"{body['sibling_count']} co-cited siblings"
            ),
        )
