"""``behavioral_baseline.v1`` — summary stats over an event stream.

ADR-0033 Phase B2. AnomalyAce's first surface: feed it a list of
events (rows of dicts) and a list of fields, get back a baseline
dict the operator persists (typically via ``memory_write.v1`` at
scope='lineage' so descendants in the swarm chain can recall it).

The baseline shape per field is decided by the data type the
operator names:

  * **categorical** — frequency table { value: count }, plus the
                       Top-N most frequent (for compact recall)
  * **numeric**     — mean, stddev (sample), min, max, count
  * **timestamp**   — bucketed counts in N equal time windows over
                       the observed range (default 24 buckets =
                       hourly for a one-day window)

This is the SHAPE that ``anomaly_score.v1`` consumes — they're
designed as a pair. A baseline emitted by this tool is directly
parseable by anomaly_score's baseline argument.

side_effects=read_only — the tool reads the events the caller
passes in, computes summary stats in memory, returns them. Nothing
crosses to disk or network. The agent is responsible for
persisting the baseline (via memory_write).
"""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_MAX_EVENTS = 100000
_MAX_FIELDS = 20
_DEFAULT_TOP_N = 20
_MAX_TOP_N = 200
_DEFAULT_TIME_BUCKETS = 24
_MAX_TIME_BUCKETS = 168  # one week of hourly
_FIELD_TYPES = ("categorical", "numeric", "timestamp")


class BehavioralBaselineTool:
    """Compute summary stats over a list of events.

    Args:
      events (list[object], required): list of dicts. Each dict is
        one event. Missing fields are tolerated; absent values
        contribute to a ``__missing__`` count per field.
      fields (object, required): map of ``{field_name: {type, ...}}``
        where type is one of ``categorical``, ``numeric``, ``timestamp``.
        Each may carry per-type knobs:
          - categorical: top_n (int, default 20, max 200)
          - numeric: nothing extra
          - timestamp: buckets (int, default 24, max 168);
                       parses ISO 8601 / unix epoch automatically.

    Output:
      {
        "event_count": int,
        "fields": {
          "<name>": {
            "type": str,
            "missing_count": int,
            ... type-specific stats ...
          }, ...
        }
      }
    """

    name = "behavioral_baseline"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        events = args.get("events")
        if not isinstance(events, list):
            raise ToolValidationError(
                "events must be a list of dicts"
            )
        if len(events) > _MAX_EVENTS:
            raise ToolValidationError(
                f"events must be ≤ {_MAX_EVENTS}; got {len(events)}"
            )
        for i, e in enumerate(events):
            if not isinstance(e, dict):
                raise ToolValidationError(
                    f"events[{i}] must be a dict; got {type(e).__name__}"
                )

        fields = args.get("fields")
        if not isinstance(fields, dict) or not fields:
            raise ToolValidationError(
                "fields must be a non-empty mapping {name: {type, ...}}"
            )
        if len(fields) > _MAX_FIELDS:
            raise ToolValidationError(
                f"fields must be ≤ {_MAX_FIELDS}; got {len(fields)}"
            )
        for name, spec in fields.items():
            if not isinstance(spec, dict):
                raise ToolValidationError(
                    f"fields[{name!r}] must be a mapping with at least 'type'"
                )
            t = spec.get("type")
            if t not in _FIELD_TYPES:
                raise ToolValidationError(
                    f"fields[{name!r}].type must be one of {list(_FIELD_TYPES)}; "
                    f"got {t!r}"
                )
            if t == "categorical":
                tn = spec.get("top_n")
                if tn is not None and (not isinstance(tn, int) or tn < 1 or tn > _MAX_TOP_N):
                    raise ToolValidationError(
                        f"fields[{name!r}].top_n must be 1..{_MAX_TOP_N}; got {tn!r}"
                    )
            elif t == "timestamp":
                bk = spec.get("buckets")
                if bk is not None and (not isinstance(bk, int) or bk < 2 or bk > _MAX_TIME_BUCKETS):
                    raise ToolValidationError(
                        f"fields[{name!r}].buckets must be 2..{_MAX_TIME_BUCKETS}; got {bk!r}"
                    )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        events: list[dict] = args["events"]
        fields_spec: dict[str, dict] = args["fields"]
        out_fields: dict[str, dict] = {}

        for fname, spec in fields_spec.items():
            t = spec["type"]
            values = [e.get(fname) for e in events]
            missing = sum(1 for v in values if v is None)
            present = [v for v in values if v is not None]

            if t == "categorical":
                top_n = int(spec.get("top_n") or _DEFAULT_TOP_N)
                counter: Counter = Counter(_to_str(v) for v in present)
                out_fields[fname] = {
                    "type":          "categorical",
                    "missing_count": missing,
                    "unique_count":  len(counter),
                    "top": [
                        {"value": v, "count": c}
                        for v, c in counter.most_common(top_n)
                    ],
                    # Full frequency table — the consumer (anomaly_score)
                    # uses this for novelty detection. Capped via top_n
                    # only for the visible "top" array; the full table is
                    # always emitted so anomaly_score has the data it
                    # needs.
                    "frequency": dict(counter),
                }

            elif t == "numeric":
                nums = []
                non_numeric = 0
                for v in present:
                    try:
                        nums.append(float(v))
                    except (TypeError, ValueError):
                        non_numeric += 1
                if nums:
                    mean = sum(nums) / len(nums)
                    var = (
                        sum((x - mean) ** 2 for x in nums) / (len(nums) - 1)
                        if len(nums) > 1 else 0.0
                    )
                    stddev = math.sqrt(var)
                    stats = {
                        "type":          "numeric",
                        "missing_count": missing,
                        "non_numeric_count": non_numeric,
                        "count":         len(nums),
                        "mean":          mean,
                        "stddev":        stddev,
                        "min":           min(nums),
                        "max":           max(nums),
                    }
                else:
                    stats = {
                        "type":          "numeric",
                        "missing_count": missing,
                        "non_numeric_count": non_numeric,
                        "count":         0,
                        "mean":          None,
                        "stddev":        None,
                        "min":           None,
                        "max":           None,
                    }
                out_fields[fname] = stats

            elif t == "timestamp":
                buckets = int(spec.get("buckets") or _DEFAULT_TIME_BUCKETS)
                parsed = []
                unparsed = 0
                for v in present:
                    dt = _parse_timestamp(v)
                    if dt is None:
                        unparsed += 1
                    else:
                        parsed.append(dt)
                if not parsed:
                    out_fields[fname] = {
                        "type":          "timestamp",
                        "missing_count": missing,
                        "unparsed_count": unparsed,
                        "count":         0,
                        "earliest":      None,
                        "latest":        None,
                        "buckets":       [],
                    }
                    continue
                earliest = min(parsed)
                latest = max(parsed)
                span_seconds = max((latest - earliest).total_seconds(), 1.0)
                bucket_size = span_seconds / buckets
                bucket_counts = [0] * buckets
                for dt in parsed:
                    offset = (dt - earliest).total_seconds()
                    idx = min(int(offset / bucket_size), buckets - 1)
                    bucket_counts[idx] += 1
                out_fields[fname] = {
                    "type":          "timestamp",
                    "missing_count": missing,
                    "unparsed_count": unparsed,
                    "count":         len(parsed),
                    "earliest":      earliest.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "latest":        latest.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "bucket_size_seconds": bucket_size,
                    "buckets":       bucket_counts,
                }

        return ToolResult(
            output={
                "event_count": len(events),
                "fields":      out_fields,
            },
            metadata={
                "fields_count": len(fields_spec),
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"baseline over {len(events)} events × "
                f"{len(fields_spec)} field{'s' if len(fields_spec) != 1 else ''}"
            ),
        )


def _to_str(v: Any) -> str:
    """Coerce categorical values to a string. Lists/dicts get JSON-
    style repr so the frequency table keys are stable and hashable."""
    if isinstance(v, str):
        return v
    return repr(v)


_ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$"
)


def _parse_timestamp(v: Any) -> datetime | None:
    """Recognize ISO 8601 strings and unix epoch numbers. Other
    shapes return None (counted in unparsed_count)."""
    if isinstance(v, (int, float)):
        try:
            ts = float(v)
            if ts > 1e12:  # millisecond epoch
                ts /= 1000.0
            if ts <= 0 or ts > 4102444800:  # past year 2100 — bail
                return None
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OSError):
            return None
    if isinstance(v, str):
        if not _ISO_RE.match(v):
            return None
        try:
            s = v.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None
