"""``log_correlate.v1`` — cross-source join over normalized log streams.

ADR-0033 Phase B2. AnomalyAce's working primitive: given multiple
log streams (typically the output of ``log_aggregate.v1``) and a
key field (IP address, username, PID, request ID), group events
by that key and emit per-group statistics + the matched events.

Use case: LogLurker emits a unified stream from /var/log/auth.log
+ /var/log/system.log + the daemon's audit chain → AnomalyAce
correlates by remote_ip → per-IP timeline of every event from
every source, ready to feed anomaly_score's window.

The tool extracts the join key from each event using one of:

  * **field** — key is the value of a single dict field
  * **regex** — key is the first capturing group of a regex
                applied to a designated text field

Each group's output includes the matched events (capped) plus
per-group rollups: count, span (earliest..latest timestamp if
present), distinct sources.

side_effects=read_only — pure function over the input streams.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_MAX_EVENTS = 100000
_DEFAULT_MAX_GROUPS = 500
_DEFAULT_MAX_PER_GROUP = 50


class LogCorrelateTool:
    """Group events by an extracted key.

    Args:
      events (list[dict], required): events to group. Typically the
        ``lines`` output of log_aggregate.v1 (each entry has
        path / lineno / timestamp / text), but any list of dicts
        with the join key works.

      key (object, required): extraction spec. One of:
        * ``{"field": "<name>"}`` — use the dict field's value as key
        * ``{"regex": "<pat>", "field": "<text-field>"}`` — apply
          regex to the named text field; first capturing group is
          the key. Events without a match are dropped (counted in
          unmatched_count).

      max_groups (int, optional): cap on returned groups. Default 500;
                                   groups with the highest event
                                   counts win.
      max_per_group (int, optional): cap on events per group's
                                      ``events`` array. Default 50.

    Output:
      {
        "event_count":     int,
        "unmatched_count": int,
        "group_count":     int,
        "truncated":       bool,
        "groups": [
          {
            "key":             str,
            "count":           int,
            "earliest":        str | null,
            "latest":          str | null,
            "distinct_sources": [str, ...],   # distinct path values, capped
            "events":          [dict, ...]    # capped
          }, ...
        ]
      }
    """

    name = "log_correlate"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        events = args.get("events")
        if not isinstance(events, list):
            raise ToolValidationError("events must be a list of dicts")
        if len(events) > _MAX_EVENTS:
            raise ToolValidationError(
                f"events must be ≤ {_MAX_EVENTS}; got {len(events)}"
            )
        for i, e in enumerate(events):
            if not isinstance(e, dict):
                raise ToolValidationError(
                    f"events[{i}] must be a dict; got {type(e).__name__}"
                )

        key = args.get("key")
        if not isinstance(key, dict):
            raise ToolValidationError(
                "key must be a mapping with 'field' or 'regex'"
            )
        if "regex" in key:
            if not isinstance(key.get("regex"), str) or not key["regex"]:
                raise ToolValidationError(
                    "key.regex must be a non-empty string"
                )
            try:
                rx = re.compile(key["regex"])
            except re.error as exc:
                raise ToolValidationError(
                    f"key.regex compile failed: {exc}"
                ) from exc
            if rx.groups < 1:
                raise ToolValidationError(
                    "key.regex must contain at least one capturing group"
                )
            if not isinstance(key.get("field"), str) or not key["field"]:
                raise ToolValidationError(
                    "key.regex requires key.field naming the text field to match against"
                )
        elif "field" in key:
            if not isinstance(key["field"], str) or not key["field"]:
                raise ToolValidationError(
                    "key.field must be a non-empty string"
                )
        else:
            raise ToolValidationError(
                "key must specify either 'field' or 'regex'+'field'"
            )

        for arg, default in [("max_groups", _DEFAULT_MAX_GROUPS),
                             ("max_per_group", _DEFAULT_MAX_PER_GROUP)]:
            v = args.get(arg)
            if v is not None and (not isinstance(v, int) or v < 1 or v > 10000):
                raise ToolValidationError(
                    f"{arg} must be 1..10000; got {v!r}"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        events: list[dict] = args["events"]
        key_spec: dict = args["key"]
        max_groups = int(args.get("max_groups") or _DEFAULT_MAX_GROUPS)
        max_per_group = int(args.get("max_per_group") or _DEFAULT_MAX_PER_GROUP)

        rx = re.compile(key_spec["regex"]) if "regex" in key_spec else None
        text_field = key_spec.get("field")

        groups: dict[str, list[dict]] = defaultdict(list)
        unmatched = 0
        for e in events:
            if rx is not None:
                text = e.get(text_field)
                if not isinstance(text, str):
                    unmatched += 1
                    continue
                m = rx.search(text)
                if not m:
                    unmatched += 1
                    continue
                key_val = m.group(1)
            else:
                key_val = e.get(text_field)
                if key_val is None:
                    unmatched += 1
                    continue
                if not isinstance(key_val, str):
                    key_val = repr(key_val)
            groups[key_val].append(e)

        # Sort groups by count descending and truncate.
        sorted_groups = sorted(groups.items(), key=lambda kv: -len(kv[1]))
        truncated = len(sorted_groups) > max_groups
        sorted_groups = sorted_groups[:max_groups]

        out_groups = []
        for k, evs in sorted_groups:
            timestamps = [e.get("timestamp") for e in evs if e.get("timestamp")]
            sources = []
            seen_sources = set()
            for e in evs:
                p = e.get("path") or e.get("source")
                if p and p not in seen_sources:
                    seen_sources.add(p)
                    sources.append(p)
                    if len(sources) >= 20:
                        break
            out_groups.append({
                "key":              k,
                "count":            len(evs),
                "earliest":         min(timestamps) if timestamps else None,
                "latest":           max(timestamps) if timestamps else None,
                "distinct_sources": sources,
                "events":           evs[:max_per_group],
            })

        return ToolResult(
            output={
                "event_count":     len(events),
                "unmatched_count": unmatched,
                "group_count":     len(groups),
                "truncated":       truncated,
                "groups":          out_groups,
            },
            metadata={
                "max_groups":    max_groups,
                "max_per_group": max_per_group,
                "key_mode":      "regex" if rx else "field",
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"correlated {len(events)} events into {len(groups)} group"
                f"{'s' if len(groups) != 1 else ''}"
                + (" (truncated)" if truncated else "")
            ),
        )
