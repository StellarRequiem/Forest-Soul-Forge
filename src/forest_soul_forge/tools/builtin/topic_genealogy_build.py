"""``topic_genealogy_build.v1`` — ADR-0086 Phase B topic-graph builder.

Walks memory entries tagged ``topic:<slug>`` from the Librarian-D1
catalog (and any synthesizer/prospector entries that carry the
same tag), uses the audit chain to recover provenance + temporal
ordering, and emits a structured topic graph: nodes are claims
(catalog entries) and edges describe relationships between them
("supports" / "refines" / "contradicts" / "confirms") derived
from the catalog block's ``relationship`` field (see
``examples/skills/knowledge_curation.v1.yaml`` step 3) and the
audit chain's strict temporal ordering.

## Why graph, not list

A topic page that's just "every catalog entry tagged topic:X" is
operationally identical to a memory_recall — the synthesizer's
value-add is the RELATIONSHIPS. A graph lets the operator (and
downstream agents — knowledge_verifier, daily_knowledge_delta)
see which claim was the seed, which claim refined it, which claim
challenged it. That's the topic *genealogy*: who said what when,
and how those statements relate.

## Edge derivation

Two paths feed the edge set:

1. **Explicit relationship field.** The librarian's
   ``knowledge_curation`` skill writes a catalog block with a
   ``Relationship: refines:<prior_entry_id>`` (or confirms /
   potential_contradiction) line. This is the primary signal —
   when present, it's authoritative.
2. **Temporal-ordering fallback.** When the relationship line is
   absent (entries written by hand, by earlier librarian versions,
   or by a prospector inbox entry promoted directly without
   running the curation skill), the tool falls back to "most
   recent prior entry supports newest entry" as a weak edge with
   ``edge_kind=temporal_only`` so the operator can see the
   ordering even without explicit relationship metadata.

side_effects=read_only — the tool only reads memory + the audit
chain; never writes. The synthesizer's skill persists the graph
to memory via memory_write at its own attestation step.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_DEFAULT_AUDIT_CHAIN = Path("examples/audit_chain.jsonl")
_MAX_CHAIN_LINES = 100_000
_MAX_ENTRIES = 500
_DEFAULT_WINDOW_DAYS = 365


class TopicGenealogyBuildTool:
    """Build a topic graph from memory entries + audit-chain provenance.

    Args:
      topic_slug (str, required): the topic to build the graph for.
        Convention: lowercase kebab-case. Must match
        ``[a-z0-9-]+`` to forbid path-traversal + tag-injection.
      window_days (int, optional): how far back to walk. Default 365.
        Capped at 730 (active-segment scope per ADR-0073).
      audit_chain_path (str, optional): override default chain path.
        Tests pass a fixture path; production callers omit.
      max_entries (int, optional): cap on the number of catalog
        entries gathered. Default 500.

    Output:
      {
        "topic_slug":      str,
        "window_days":     int,
        "generated_at":    str (ISO),
        "node_count":      int,
        "edge_count":      int,
        "nodes":           [{
            "entry_id":     str,
            "ts":           float,
            "attestor":     str,
            "source_url":   str,
            "content_excerpt": str,
            "tags":         [str, ...],
        }, ...],
        "edges":           [{
            "from_entry_id": str,
            "to_entry_id":   str,
            "edge_kind":     "refines" | "confirms" |
                             "contradicts" | "temporal_only",
            "evidence":      str,
        }, ...],
        "errors":          [str, ...],
      }
    """

    name = "topic_genealogy_build"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        slug = args.get("topic_slug")
        if not isinstance(slug, str) or not slug:
            raise ToolValidationError(
                "topic_slug must be a non-empty string"
            )
        if not re.fullmatch(r"[a-z0-9-]+", slug):
            raise ToolValidationError(
                "topic_slug must be lowercase kebab-case "
                "([a-z0-9-]+); blocks path-traversal + tag-injection"
            )
        wd = args.get("window_days")
        if wd is not None:
            if not isinstance(wd, int) or wd <= 0:
                raise ToolValidationError(
                    "window_days must be a positive integer"
                )
            if wd > 730:
                raise ToolValidationError(
                    "window_days must be <= 730 (segment scope)"
                )
        me = args.get("max_entries")
        if me is not None:
            if not isinstance(me, int) or me <= 0:
                raise ToolValidationError(
                    "max_entries must be a positive integer"
                )
            if me > 5000:
                raise ToolValidationError(
                    "max_entries must be <= 5000"
                )
        if "audit_chain_path" in args and not isinstance(
            args["audit_chain_path"], str,
        ):
            raise ToolValidationError(
                "audit_chain_path must be a string"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        topic_slug: str = args["topic_slug"]
        window_days = int(
            args.get("window_days") or _DEFAULT_WINDOW_DAYS,
        )
        max_entries = int(args.get("max_entries") or _MAX_ENTRIES)
        chain_path = Path(
            args.get("audit_chain_path") or _DEFAULT_AUDIT_CHAIN,
        )

        errors: list[str] = []
        cutoff = time.time() - (window_days * 86400)
        topic_tag = f"topic:{topic_slug}"

        # Walk the chain ONCE, collect every memory_write entry
        # tagged with this topic. Entries are ordered by audit-chain
        # sequence (= temporal order) by virtue of the chain being
        # append-only.
        nodes: list[dict[str, Any]] = []
        seen_entry_ids: set[str] = set()

        if chain_path.exists():
            try:
                with chain_path.open() as f:
                    for i, line in enumerate(f):
                        if i >= _MAX_CHAIN_LINES:
                            break
                        if len(nodes) >= max_entries:
                            break
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        ts = _entry_ts(entry)
                        if ts is None or ts < cutoff:
                            continue
                        tags = _entry_tags(entry)
                        if topic_tag not in tags:
                            continue
                        entry_id = _entry_id(entry)
                        if not entry_id or entry_id in seen_entry_ids:
                            continue
                        seen_entry_ids.add(entry_id)
                        content = _entry_content(entry)
                        nodes.append({
                            "entry_id":         entry_id,
                            "ts":               ts,
                            "attestor":         _attestor_from_tags(tags),
                            "source_url":       _source_url_from_tags(
                                tags,
                            ),
                            "content_excerpt":  content[:300],
                            "_content_full":    content,
                            "tags":             tags,
                        })
            except OSError as e:
                errors.append(f"chain read error: {e}")
        else:
            errors.append(f"audit chain not found: {chain_path}")

        # Edge derivation pass. For each node, parse its content
        # excerpt for an explicit Relationship: line; fall back to
        # temporal-ordering edge.
        edges: list[dict[str, Any]] = []
        nodes_sorted = sorted(nodes, key=lambda n: n["ts"])
        for idx, node in enumerate(nodes_sorted):
            rel = _parse_relationship(node["_content_full"])
            if rel:
                edge_kind, target_id, evidence = rel
                if target_id in seen_entry_ids:
                    edges.append({
                        "from_entry_id": node["entry_id"],
                        "to_entry_id":   target_id,
                        "edge_kind":     edge_kind,
                        "evidence":      evidence,
                    })
                    continue
            # Fallback: weak temporal edge to the immediately
            # prior entry, if any. Only emit when there's no
            # explicit relationship; this avoids burying the
            # operator in redundant edges.
            if idx > 0:
                prior = nodes_sorted[idx - 1]
                edges.append({
                    "from_entry_id": node["entry_id"],
                    "to_entry_id":   prior["entry_id"],
                    "edge_kind":     "temporal_only",
                    "evidence":      "no explicit relationship; "
                                     "weak temporal-ordering edge",
                })

        # Strip the internal _content_full field before emit.
        for n in nodes_sorted:
            n.pop("_content_full", None)

        body = {
            "topic_slug":   topic_slug,
            "window_days":  window_days,
            "generated_at": datetime.now(timezone.utc)
                                    .replace(tzinfo=None)
                                    .isoformat(timespec="seconds")
                                    + "Z",
            "node_count":   len(nodes_sorted),
            "edge_count":   len(edges),
            "nodes":        nodes_sorted,
            "edges":        edges,
            "errors":       errors,
        }
        return ToolResult(
            output=body,
            metadata={
                "node_count": len(nodes_sorted),
                "edge_count": len(edges),
                "edge_kinds": _count_edge_kinds(edges),
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"topic_genealogy {topic_slug}: "
                f"{len(nodes_sorted)} nodes, "
                f"{len(edges)} edges"
            ),
        )


def _entry_ts(entry: dict[str, Any]) -> float | None:
    raw = entry.get("ts") or entry.get("timestamp")
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            s = raw.rstrip("Z")
            return datetime.fromisoformat(s).timestamp()
        except ValueError:
            return None
    return None


def _entry_tags(entry: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for top_key in ("tags", "payload_tags"):
        raw = entry.get(top_key)
        if isinstance(raw, list):
            found.extend(t for t in raw if isinstance(t, str))
    for nest_key in ("payload", "data"):
        nested = entry.get(nest_key)
        if isinstance(nested, dict):
            sub = nested.get("tags")
            if isinstance(sub, list):
                found.extend(t for t in sub if isinstance(t, str))
    return found


def _entry_content(entry: dict[str, Any]) -> str:
    for top_key in ("content", "body"):
        v = entry.get(top_key)
        if isinstance(v, str):
            return v
    for nest_key in ("payload", "data"):
        nested = entry.get(nest_key)
        if isinstance(nested, dict):
            v = nested.get("content")
            if isinstance(v, str):
                return v
    return ""


def _entry_id(entry: dict[str, Any]) -> str | None:
    """Pull the memory entry's id from a memory_write audit event.

    The librarian's catalog write produces a memory_written event
    whose payload carries the entry_id. Different daemons have
    embedded this id in slightly different places over time; check
    the common locations and return the first hit.
    """
    for top_key in ("entry_id", "memory_entry_id"):
        v = entry.get(top_key)
        if isinstance(v, str) and v:
            return v
    for nest_key in ("payload", "data"):
        nested = entry.get(nest_key)
        if isinstance(nested, dict):
            for sub_key in ("entry_id", "memory_entry_id", "id"):
                v = nested.get(sub_key)
                if isinstance(v, str) and v:
                    return v
    # Fallback: use the chain sequence number as a synthetic id,
    # so two entries from different writes don't collide.
    seq = entry.get("seq") or entry.get("sequence")
    if isinstance(seq, (int, str)):
        return f"seq:{seq}"
    return None


def _attestor_from_tags(tags: list[str]) -> str:
    for t in tags:
        if t.startswith("attestor:"):
            return t.split(":", 1)[1]
    return ""


def _source_url_from_tags(tags: list[str]) -> str:
    for t in tags:
        if t.startswith("provenance:"):
            return t.split(":", 1)[1]
    return ""


_REL_RE = re.compile(
    r"Relationship:\s*(refines|confirms|potential_contradiction|"
    r"contradicts|new)\s*(?::\s*([A-Za-z0-9_\-:]+))?",
    re.IGNORECASE,
)


def _parse_relationship(
    content: str,
) -> tuple[str, str, str] | None:
    """Extract (edge_kind, target_entry_id, evidence) from content.

    Returns None when there's no Relationship line or when the
    relationship is "new" (which has no target).
    """
    if not content:
        return None
    m = _REL_RE.search(content)
    if not m:
        return None
    rel = m.group(1).lower()
    target = (m.group(2) or "").strip()
    if rel == "new" or not target:
        return None
    edge_kind = {
        "refines":                  "refines",
        "confirms":                 "confirms",
        "potential_contradiction":  "contradicts",
        "contradicts":              "contradicts",
    }.get(rel, "temporal_only")
    evidence = m.group(0)[:200]
    return edge_kind, target, evidence


def _count_edge_kinds(edges: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in edges:
        k = e.get("edge_kind", "temporal_only")
        counts[k] = counts.get(k, 0) + 1
    return counts
