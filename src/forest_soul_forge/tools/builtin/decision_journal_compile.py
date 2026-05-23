"""``decision_journal_compile.v1`` — ADR-0087 Phase D reflection.

Walks the audit chain over an operator-named window (default 24h)
and compiles a decision-journal digest. Surfaces operator-decision
events that landed in the window — what was decided, what was
deferred, what surfaced as a pattern.

Read-only. The Reflector-D2 agent's ``daily_reflection.v1``
skill wraps this tool with chain-integrity verification +
memory_write of the final attestation.

## What lands in the digest

Three event families surface:

1. **Operator decisions** — chain entries whose event_type
   carries an ``operator_`` prefix or ``decision`` substring, OR
   memory_write entries tagged with ``decision`` /
   ``operator_decision``. Bucketed by the dominant topic tag if
   present.
2. **Deferrals / open items** — memory_write entries tagged
   ``deferred`` / ``pending`` / ``open_item`` /
   ``carry_forward``. Surfaced so the digest can flag what
   piled up.
3. **Pattern signals** — recurring topic tags across the window
   (>= 3 entries on the same topic). Lets the operator see what
   they kept circling back on.

side_effects=read_only.
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
_PATTERN_THRESHOLD = 3

_DECISION_TAGS = {
    "decision", "operator_decision", "decided", "chose",
}
_DEFERRAL_TAGS = {
    "deferred", "pending", "open_item", "carry_forward",
    "blocked",
}


class DecisionJournalCompileTool:
    """Walk the chain + bucket decisions / deferrals / patterns.

    Args:
      window_hours (int, optional): default 24; capped at 720
        (30 days).
      audit_chain_path (str, optional): override default chain path.

    Output:
      {
        "window_hours":     int,
        "generated_at":     str (ISO),
        "decisions":        [{
          "ts":           float,
          "entry_id":     str,
          "topic":        str,
          "attestor":     str,
          "content_excerpt": str,
        }, ...],
        "deferrals":        [...],  # same envelope shape
        "patterns": {
          "<topic>": int,           # entry count for that topic
        },
        "summary": {
          "decision_count": int,
          "deferral_count": int,
          "pattern_count":  int,    # topics with >= 3 entries
        },
        "errors":           [str, ...],
      }
    """

    name = "decision_journal_compile"
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

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        window_hours = int(
            args.get("window_hours") or _DEFAULT_WINDOW_HOURS,
        )
        chain_path = Path(
            args.get("audit_chain_path") or _DEFAULT_AUDIT_CHAIN,
        )

        errors: list[str] = []
        cutoff = time.time() - (window_hours * 3600)

        decisions: list[dict[str, Any]] = []
        deferrals: list[dict[str, Any]] = []
        topic_counts: dict[str, int] = {}

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
                        if topic:
                            topic_counts[topic] = (
                                topic_counts.get(topic, 0) + 1
                            )

                        envelope = {
                            "ts":              ts,
                            "entry_id":        _entry_id(entry) or "",
                            "topic":           topic,
                            "attestor":        _attestor_from_tags(tags),
                            "content_excerpt": _entry_content(entry)[:200],
                        }

                        if _is_decision(entry, tags):
                            if len(decisions) < _MAX_ENTRIES_PER_FAMILY:
                                decisions.append(envelope)
                        if _is_deferral(tags):
                            if len(deferrals) < _MAX_ENTRIES_PER_FAMILY:
                                deferrals.append(envelope)
            except OSError as e:
                errors.append(f"chain read error: {e}")
        else:
            errors.append(f"audit chain not found: {chain_path}")

        patterns = {
            t: c for t, c in topic_counts.items()
            if c >= _PATTERN_THRESHOLD
        }

        summary = {
            "decision_count": len(decisions),
            "deferral_count": len(deferrals),
            "pattern_count":  len(patterns),
        }
        body = {
            "window_hours":  window_hours,
            "generated_at":  datetime.now(timezone.utc)
                                      .replace(tzinfo=None)
                                      .isoformat(timespec="seconds")
                                      + "Z",
            "decisions":     decisions,
            "deferrals":     deferrals,
            "patterns":      patterns,
            "summary":       summary,
            "errors":        errors,
        }
        return ToolResult(
            output=body,
            metadata=summary,
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"decision_journal {window_hours}h: "
                f"{summary['decision_count']}D "
                f"{summary['deferral_count']}P "
                f"{summary['pattern_count']}pat"
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


def _is_decision(entry: dict[str, Any], tags: list[str]) -> bool:
    """Identify operator-decision events.

    Two signals:
    1. An event_type that contains 'operator_' or 'decision'.
    2. A tag that's in _DECISION_TAGS or starts with 'decision:'.
    """
    et = entry.get("event_type") or ""
    if isinstance(et, str):
        lower = et.lower()
        if "decision" in lower:
            return True
        if lower.startswith("operator_"):
            return True
    for t in tags:
        if t in _DECISION_TAGS:
            return True
        if t.startswith("decision:"):
            return True
    return False


def _is_deferral(tags: list[str]) -> bool:
    for t in tags:
        if t in _DEFERRAL_TAGS:
            return True
    return False
