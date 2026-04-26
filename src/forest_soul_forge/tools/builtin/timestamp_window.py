"""``timestamp_window.v1`` — relative-time → absolute-window helper.

Reference implementation for ADR-0019 T1. Pure function, no I/O. Used
across multiple agent kits (network_watcher, log_analyst,
anomaly_investigator) as the start/end-pair generator for time-bounded
queries.

Catalog entry: see ``config/tool_catalog.yaml`` line ``timestamp_window.v1``.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


# Each pattern matches a relative time expression and yields a timedelta.
# Order matters — "minutes" before "minute" so the longer phrasing wins
# the regex match. Plural / singular both accepted.
_RELATIVE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^last\s+(\d+)\s+seconds?$", re.IGNORECASE), "seconds"),
    (re.compile(r"^last\s+(\d+)\s+minutes?$", re.IGNORECASE), "minutes"),
    (re.compile(r"^last\s+(\d+)\s+hours?$", re.IGNORECASE), "hours"),
    (re.compile(r"^last\s+(\d+)\s+days?$", re.IGNORECASE), "days"),
    (re.compile(r"^past\s+(\d+)\s*s$", re.IGNORECASE), "seconds"),
    (re.compile(r"^past\s+(\d+)\s*m$", re.IGNORECASE), "minutes"),
    (re.compile(r"^past\s+(\d+)\s*h$", re.IGNORECASE), "hours"),
    (re.compile(r"^past\s+(\d+)\s*d$", re.IGNORECASE), "days"),
)


def _parse_relative(expr: str) -> timedelta:
    """Convert a relative expression like 'last 15 minutes' to timedelta.

    Raises ToolValidationError on unrecognized expressions — the runtime
    catches it and refuses the call with a clear message.
    """
    expr = expr.strip()
    for pattern, unit in _RELATIVE_PATTERNS:
        m = pattern.match(expr)
        if m:
            value = int(m.group(1))
            return timedelta(**{unit: value})
    raise ToolValidationError(
        f"unrecognized relative expression: {expr!r}. "
        "Expected forms: 'last N {seconds|minutes|hours|days}' or "
        "'past Nm'/'past Nh'/'past Nd' (case-insensitive)."
    )


def _parse_anchor(anchor: str | None) -> datetime:
    """Parse the anchor or default to current UTC. ISO-8601 only."""
    if anchor is None:
        return datetime.now(timezone.utc)
    try:
        # fromisoformat handles "2026-04-26T00:00:00Z" via Python 3.11+
        # but we normalize "Z" → "+00:00" for older fallbacks.
        normalized = anchor.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    except (TypeError, ValueError) as e:
        raise ToolValidationError(
            f"anchor not parseable as ISO-8601: {anchor!r} ({e})"
        ) from e
    if dt.tzinfo is None:
        # Tolerate naive datetimes — interpret as UTC. Mirrors the
        # rest of the codebase's UTC-by-default posture.
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class TimestampWindowTool:
    """Reference implementation. Trivial by design.

    args:
      expression: required, relative time string ("last 15 minutes")
      anchor:     optional, ISO-8601 reference point. Defaults to now-UTC.

    output:
      { start: ISO-8601, end: ISO-8601, span_seconds: int }
    """

    name = "timestamp_window"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        if "expression" not in args:
            raise ToolValidationError("missing required arg 'expression'")
        if not isinstance(args["expression"], str) or not args["expression"].strip():
            raise ToolValidationError("'expression' must be a non-empty string")
        anchor = args.get("anchor")
        if anchor is not None and not isinstance(anchor, str):
            raise ToolValidationError("'anchor' must be a string when provided")

    async def execute(
        self,
        args: dict[str, Any],
        ctx: ToolContext,
    ) -> ToolResult:
        # Validate again at execute time as a defensive layer — the
        # runtime calls validate() first, but tools should never
        # assume their inputs reached them through a sanitized path.
        self.validate(args)
        delta = _parse_relative(args["expression"])
        end = _parse_anchor(args.get("anchor"))
        start = end - delta
        return ToolResult(
            output={
                "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "span_seconds": int(delta.total_seconds()),
            },
            metadata={
                "expression": args["expression"],
                "anchor_supplied": "anchor" in args,
            },
            # Pure function — no provider call, no real-world side effect.
            tokens_used=None,
            cost_usd=None,
            side_effect_summary=None,
        )
