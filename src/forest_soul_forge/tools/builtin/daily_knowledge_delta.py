"""``daily_knowledge_delta.v1`` — ADR-0086 Phase D delta builder.

Walks the audit chain over an operator-named window (default 24h)
and buckets D1-relevant events into a "what changed in your
knowledge today" report. Read-only synthesis — never writes.
The synthesizer's `daily_knowledge_delta.v1` skill wraps this
tool with chain-integrity verification + memory_write of the
final attestation.

## What lands in the delta

Three event families surface in the report:

1. **Catalog writes** — every memory_write tagged
   `knowledge_catalog_entry` (librarian's catalog discipline)
   within the window. Bucketed by topic. Lets the operator see
   what was cataloged.
2. **Prospector pulls** — every memory_write tagged
   `knowledge_prospector_inbox`. Bucketed by topic + source URL.
   Lets the operator see what was sourced.
3. **Contradiction flags** — every contradiction-flag escalation
   from the knowledge_verifier within the window. Bucketed by
   topic. Lets the operator see what needs review.

## Operator framing

The delta is the daily situational-awareness brief: "yesterday
you cataloged 7 entries across 3 topics; the prospector pulled
5 new sources; the verifier flagged 2 candidates on
'diffusion-models' for review." That's the brief; the tool's
output is the data structure the synthesizer's skill condenses
into the brief's prose.

side_effects=read_only. The tool reads memory + the audit chain;
the skill persists the final delta artifact via memory_write.
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
_MAX_CHAIN_LINES = 200_000
_DEFAULT_WINDOW_HOURS = 24
_MAX_ENTRIES_PER_FAMILY = 500


class DailyKnowledgeDeltaTool:
    """Bucket recent D1 chain events into a daily-delta report.

    Args:
      window_hours (int, optional): how far back to walk.
        Default 24. Capped at 720 (30 days; for week-/month-
        catch-up briefs after operator PTO).
      audit_chain_path (str, optional): override default chain path.
      topic_filter (str, optional): if present, restrict the
        delta to one topic slug (lowercase kebab-case). Default
        no filter (all topics).

    Output:
      {
        "window_hours":           int,
        "topic_filter":           str | "",
        "generated_at":           str (ISO),
        "catalog_writes":         {
          "<topic>": [{ts, entry_id, source_url, attestor,
                       content_excerpt}, ...],
          ...
        },
        "prospector_pulls":       {
          "<topic>": [{ts, entry_id, source_url, attestor,
                       content_excerpt}, ...],
          ...
        },
        "contradiction_flags":    {
          "<topic>": [{ts, entry_id, attestor, content_excerpt},
                       ...],
          ...
        },
        "summary": {
          "catalog_write_count":      int,
          "prospector_pull_count":    int,
          "contradiction_flag_count": int,
          "topic_count":              int,
        },
        "errors":                 [str, ...],
      }
    """

    name = "daily_knowledge_delta"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        wh = args.get("window_hours")
        if wh is not None:
            if not isinstance(wh, int) or wh <= 0:
                raise ToolValidationError(
                    "window_hours must be a positive integer"
                )
            if wh > 720:
                raise ToolValidationError(
                    "window_hours must be <= 720 (30 days)"
                )
        if "audit_chain_path" in args and not isinstance(
            args["audit_chain_path"], str,
        ):
            raise ToolValidationError(
                "audit_chain_path must be a string"
            )
        tf = args.get("topic_filter")
        if tf is not None:
            if not isinstance(tf, str):
                raise ToolValidationError(
                    "topic_filter must be a string"
                )
            if tf and not re.fullmatch(r"[a-z0-9-]+", tf):
                raise ToolValidationError(
                    "topic_filter must be lowercase kebab-case "
                    "or empty"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        window_hours = int(
            args.get("window_hours") or _DEFAULT_WINDOW_HOURS,
        )
        chain_path = Path(
            args.get("audit_chain_path") or _DEFAULT_AUDIT_CHAIN,
        )
        topic_filter = (args.get("topic_filter") or "").strip()

        errors: list[str] = []
        cutoff = time.time() - (window_hours * 3600)

        catalog: dict[str, list[dict[str, Any]]] = {}
        inbox: dict[str, list[dict[str, Any]]] = {}
        flags: dict[str, list[dict[str, Any]]] = {}

        if chain_path.exists():
            try:
                with chain_path.open() as f:
                    for i, line in enumerate(f):
                        if i >= _MAX_CHAIN_LINES:
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
                        topic = _topic_from_tags(tags)
                        if not topic:
                            continue
                        if topic_filter and topic != topic_filter:
                            continue
                        envelope = {
                            "ts":              ts,
                            "entry_id":        _entry_id(entry) or "",
                            "source_url":      _source_url_from_tags(tags),
                            "attestor":        _attestor_from_tags(tags),
                            "content_excerpt": _entry_content(entry)[:200],
                        }
                        bucket = _bucket_for(tags)
                        if bucket is None:
                            continue
                        target = {
                            "catalog":  catalog,
                            "inbox":    inbox,
                            "flag":     flags,
                        }[bucket]
                        bucket_list = target.setdefault(topic, [])
                        if len(bucket_list) < _MAX_ENTRIES_PER_FAMILY:
                            bucket_list.append(envelope)
            except OSError as e:
                errors.append(f"chain read error: {e}")
        else:
            errors.append(f"audit chain not found: {chain_path}")

        topics = (
            set(catalog.keys())
            | set(inbox.keys())
            | set(flags.keys())
        )
        summary = {
            "catalog_write_count":      sum(
                len(v) for v in catalog.values()
            ),
            "prospector_pull_count":    sum(
                len(v) for v in inbox.values()
            ),
            "contradiction_flag_count": sum(
                len(v) for v in flags.values()
            ),
            "topic_count":              len(topics),
        }

        body = {
            "window_hours":         window_hours,
            "topic_filter":         topic_filter,
            "generated_at":         datetime.now(timezone.utc)
                                            .replace(tzinfo=None)
                                            .isoformat(timespec="seconds")
                                            + "Z",
            "catalog_writes":       catalog,
            "prospector_pulls":     inbox,
            "contradiction_flags":  flags,
            "summary":              summary,
            "errors":               errors,
        }
        return ToolResult(
            output=body,
            metadata=summary,
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"daily_delta {window_hours}h: "
                f"{summary['catalog_write_count']}C "
                f"{summary['prospector_pull_count']}P "
                f"{summary['contradiction_flag_count']}F "
                f"across {summary['topic_count']} topics"
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
    seq = entry.get("seq") or entry.get("sequence")
    if isinstance(seq, (int, str)):
        return f"seq:{seq}"
    return None


def _topic_from_tags(tags: list[str]) -> str:
    for t in tags:
        if t.startswith("topic:"):
            return t.split(":", 1)[1]
    return ""


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


def _bucket_for(tags: list[str]) -> str | None:
    """Identify which D1 event family an entry belongs to.

    Returns "catalog" / "inbox" / "flag" / None. The mapping is
    by the D1 skill manifests' tag conventions:
      knowledge_catalog_entry        → catalog (librarian)
      knowledge_prospector_inbox     → inbox (prospector)
      contradiction_flag*            → flag (knowledge_verifier)
    """
    tagset = set(tags)
    if "knowledge_catalog_entry" in tagset:
        return "catalog"
    if "knowledge_prospector_inbox" in tagset:
        return "inbox"
    for t in tags:
        if t.startswith("contradiction_flag"):
            return "flag"
        if t == "knowledge_contradiction_flag":
            return "flag"
    return None
