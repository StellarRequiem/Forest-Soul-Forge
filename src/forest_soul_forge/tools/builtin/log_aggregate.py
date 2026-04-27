"""``log_aggregate.v1`` — multi-file roll-up with timestamp normalization.

ADR-0033 Phase B1. Combines lines from multiple log files into a
single timestamp-sorted view, normalizing common timestamp formats
to ISO 8601 UTC. LogLurker uses this to produce a unified view
across /var/log/system.log + /var/log/auth.log + the daemon's own
audit chain so AnomalyAce gets one stream to correlate against.

Recognized timestamp formats (heuristic — log lines vary wildly):

  * ISO 8601 (``2026-04-27T10:32:01Z``, with or without millis)
  * syslog default (``Apr 27 10:32:01``) — assumes current year
  * apache common (``[27/Apr/2026:10:32:01 +0000]``)
  * unix epoch (``1714209121.123``)
  * unix epoch ms (``1714209121123``)

Lines that don't match any pattern are passed through with
``timestamp=null`` and sort to the head of the output (so they're
visible, not silently dropped).

Side-effects classification: ``read_only``. The tool reads files,
emits a single combined sequence; it does not modify any source.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_MAX_PATHS = 50
_MAX_FILE_BYTES = 64 * 1024 * 1024  # 64 MiB per file
_MAX_LINES_OUT = 10000


# Pre-compiled patterns. Order matters: the first match wins.
# Each entry is (regex, parser_callable). Parsers return an
# aware UTC datetime or None on failure.
_PATTERNS: list[tuple[re.Pattern, Any]] = []


def _parse_iso(m: re.Match) -> datetime | None:
    s = m.group(0)
    # Handle Z suffix vs explicit offset.
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


_PATTERNS.append((
    re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"),
    _parse_iso,
))


def _parse_syslog(m: re.Match) -> datetime | None:
    # "Apr 27 10:32:01" — no year, assume current UTC year.
    s = m.group(0)
    year = datetime.now(timezone.utc).year
    try:
        dt = datetime.strptime(f"{year} {s}", "%Y %b %d %H:%M:%S")
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc)


_PATTERNS.append((
    re.compile(r"^[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}"),
    _parse_syslog,
))


def _parse_apache(m: re.Match) -> datetime | None:
    s = m.group(0).strip("[]")
    try:
        dt = datetime.strptime(s, "%d/%b/%Y:%H:%M:%S %z")
    except ValueError:
        return None
    return dt.astimezone(timezone.utc)


_PATTERNS.append((
    re.compile(r"\[\d{1,2}/[A-Z][a-z]{2}/\d{4}:\d{2}:\d{2}:\d{2}\s+[+-]\d{4}\]"),
    _parse_apache,
))


def _parse_epoch(m: re.Match) -> datetime | None:
    s = m.group(0)
    try:
        if "." in s or len(s) <= 11:
            ts = float(s)
        else:
            ts = float(s) / 1000.0
    except ValueError:
        return None
    if ts <= 0 or ts > 4102444800:  # > year 2100 = almost certainly not a timestamp
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc)


# Epoch is matched LAST because plain digits collide with too many things.
_PATTERNS.append((re.compile(r"^\d{10}(?:\.\d+)?$|^\d{13}$"), _parse_epoch))


def _extract_timestamp(line: str) -> tuple[str | None, str]:
    """Return (iso_utc, line_without_timestamp). When no pattern
    matches, returns (None, original_line)."""
    for pattern, parser in _PATTERNS:
        m = pattern.search(line)
        if m:
            dt = parser(m)
            if dt is not None:
                iso = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                # Don't strip the original timestamp from the line —
                # operators want to see the source format alongside
                # the normalized one. The normalization is additive.
                return iso, line.rstrip("\n")
    return None, line.rstrip("\n")


class LogAggregateTool:
    """Combine multiple log files into a timestamp-sorted view.

    Args:
      paths (list[str], required): files to combine. ≤ 50 entries.
                                    Directories are NOT walked
                                    (use log_scan for that — this
                                    tool's contract is "specific
                                    files I want in one stream").
      max_lines_out (int, optional): cap on combined output. Default
                                      10000; max 50000.

    Output:
      {
        "files_read":    int,
        "lines_in":      int,
        "lines_out":     int,
        "untimestamped": int,    # how many lines couldn't parse
        "truncated":     bool,
        "lines": [
          {"path": str, "lineno": int, "timestamp": iso|null, "text": str},
          ...
        ],
        "skipped": [{path, reason}, ...]
      }
    """

    name = "log_aggregate"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        paths = args.get("paths")
        if not isinstance(paths, list) or not paths:
            raise ToolValidationError(
                "paths must be a non-empty list of strings"
            )
        if len(paths) > _MAX_PATHS:
            raise ToolValidationError(
                f"paths must be ≤ {_MAX_PATHS}; got {len(paths)}"
            )
        for p in paths:
            if not isinstance(p, str) or not p:
                raise ToolValidationError(
                    "every path must be a non-empty string"
                )
        cap = args.get("max_lines_out")
        if cap is not None:
            if not isinstance(cap, int) or cap < 1 or cap > 50000:
                raise ToolValidationError(
                    f"max_lines_out must be 1..50000; got {cap!r}"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        cap = int(args.get("max_lines_out") or _MAX_LINES_OUT)
        skipped: list[dict[str, str]] = []
        # (timestamp_or_None, path, lineno, text). Sort key is
        # (timestamp is None, timestamp_or_empty) so untimestamped
        # lines float to the head of the output rather than the tail.
        records: list[tuple[str | None, str, int, str]] = []
        files_read = 0
        lines_in = 0

        for path_str in args["paths"]:
            p = Path(path_str)
            if not p.exists():
                skipped.append({"path": path_str, "reason": "not_found"})
                continue
            if not p.is_file():
                skipped.append({"path": path_str, "reason": "not_a_file"})
                continue
            try:
                size = p.stat().st_size
            except OSError as e:
                skipped.append({"path": path_str, "reason": f"stat:{e.errno}"})
                continue
            if size > _MAX_FILE_BYTES:
                skipped.append({"path": path_str, "reason": "too_large"})
                continue
            try:
                with p.open("r", encoding="utf-8", errors="replace") as f:
                    file_lines = f.readlines()
            except OSError as e:
                skipped.append({"path": path_str, "reason": f"read:{e.errno}"})
                continue
            files_read += 1
            for i, raw in enumerate(file_lines, start=1):
                lines_in += 1
                ts, text = _extract_timestamp(raw)
                records.append((ts, str(p), i, text))

        # Sort: untimestamped first, then by ISO ascending. The
        # "" sort key on second position is just a tie-breaker for
        # mass-untimestamped streams.
        records.sort(key=lambda r: (r[0] is not None, r[0] or "", r[1], r[2]))

        truncated = False
        if len(records) > cap:
            records = records[:cap]
            truncated = True

        untimestamped = sum(1 for r in records if r[0] is None)

        return ToolResult(
            output={
                "files_read":    files_read,
                "lines_in":      lines_in,
                "lines_out":     len(records),
                "untimestamped": untimestamped,
                "truncated":     truncated,
                "lines": [
                    {
                        "path":      path,
                        "lineno":    lineno,
                        "timestamp": ts,
                        "text":      text,
                    }
                    for ts, path, lineno, text in records
                ],
                "skipped": skipped,
            },
            metadata={
                "max_lines_out": cap,
                "files_skipped": len(skipped),
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"aggregated {files_read} files → {len(records)} lines"
                + (f" (truncated; {lines_in} read)" if truncated else "")
            ),
        )
