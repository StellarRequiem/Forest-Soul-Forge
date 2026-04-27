"""``log_scan.v1`` — regex/pattern scan over a file or directory.

ADR-0033 Phase B1. LogLurker's primary surface — given a list of
log files (or a directory tree of them) and a regex pattern, walk
them line-by-line and emit every match with context (line number,
filename, surrounding lines).

Patterns are validated before scanning: catastrophic-backtracking
patterns get rejected (via a length cap + a complexity heuristic),
and the regex is compiled once before the scan starts so per-file
errors don't leak through.

Side-effects classification: ``read_only``. The tool reads files
the agent has read access to; it never writes. Operators chain
this into a skill: log_scan → memory_write (lineage) → delegate
to AnomalyAce (mid tier).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


# Catastrophic-backtracking guards. Operators with legitimate complex
# patterns can override via the catalog's per-tool constraint policy
# at install time; the defaults here are sized for daily-sweep use.
_MAX_PATTERN_LEN = 256
_MAX_PATHS = 200
_MAX_MATCHES = 500
_MAX_FILE_BYTES = 64 * 1024 * 1024  # 64 MiB per file
_MAX_LINE_LEN = 8 * 1024            # treat lines longer than this as truncated


class LogScanTool:
    """Walk paths line-by-line; emit every regex match with context.

    Args:
      paths   (list[str], required): files or directories to scan.
                                      Directories walked recursively.
      pattern (str, required): regex pattern. Compiled with re.MULTILINE.
                                ≤ 256 chars; obvious catastrophic-
                                backtracking shapes refused.
      flags   (list[str], optional): regex flag names. Subset of
                                      ['IGNORECASE', 'DOTALL',
                                       'MULTILINE', 'VERBOSE'].
                                      MULTILINE is always on.
      context_lines (int, optional): lines of surrounding context to
                                      include with each match. Default 0.
                                      Capped at 5 each side.
      max_matches (int, optional): cap on returned match count.
                                    Default 500.

    Output:
      {
        "pattern_compiled": bool,
        "files_scanned":    int,
        "match_count":      int,
        "truncated":        bool,    # match_count == max_matches?
        "matches": [
          {
            "path":       str,
            "lineno":     int,       # 1-indexed
            "line":       str,       # may be truncated
            "before":     [str, ...],  # context_lines lines before
            "after":      [str, ...],  # context_lines lines after
          }, ...
        ],
        "skipped": [{path, reason}, ...]
      }
    """

    name = "log_scan"
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

        pattern = args.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            raise ToolValidationError(
                "pattern must be a non-empty string"
            )
        if len(pattern) > _MAX_PATTERN_LEN:
            raise ToolValidationError(
                f"pattern must be ≤ {_MAX_PATTERN_LEN} chars; got {len(pattern)}"
            )
        # Cheap catastrophic-backtracking heuristic: nested
        # quantifiers like (a+)+ or (a*)* are the classic trigger.
        # Refusing those at validation time is cheap and gives the
        # operator a fast error rather than a hung scan.
        if re.search(r"\([^)]*[+*]\)\s*[+*]", pattern):
            raise ToolValidationError(
                "pattern contains nested quantifiers that risk catastrophic "
                "backtracking — rewrite without (X+)+ / (X*)* shapes"
            )
        try:
            re.compile(pattern)
        except re.error as e:
            raise ToolValidationError(
                f"pattern compile failed: {e}"
            ) from e

        flags = args.get("flags")
        if flags is not None:
            if not isinstance(flags, list):
                raise ToolValidationError(
                    "flags must be a list of strings when provided"
                )
            allowed = {"IGNORECASE", "DOTALL", "MULTILINE", "VERBOSE"}
            for f in flags:
                if f not in allowed:
                    raise ToolValidationError(
                        f"unknown flag {f!r}; allowed: {sorted(allowed)}"
                    )

        ctx_lines = args.get("context_lines")
        if ctx_lines is not None:
            if not isinstance(ctx_lines, int) or ctx_lines < 0 or ctx_lines > 5:
                raise ToolValidationError(
                    f"context_lines must be 0..5; got {ctx_lines!r}"
                )

        cap = args.get("max_matches")
        if cap is not None:
            if not isinstance(cap, int) or cap < 1 or cap > 5000:
                raise ToolValidationError(
                    f"max_matches must be 1..5000; got {cap!r}"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        flag_names = args.get("flags") or []
        flag_value = re.MULTILINE
        for fn in flag_names:
            flag_value |= getattr(re, fn)
        rx = re.compile(args["pattern"], flag_value)
        ctx_lines = int(args.get("context_lines") or 0)
        max_matches = int(args.get("max_matches") or _MAX_MATCHES)

        files_to_scan: list[Path] = []
        skipped: list[dict[str, str]] = []
        for path_str in args["paths"]:
            p = Path(path_str)
            if not p.exists():
                skipped.append({"path": path_str, "reason": "not_found"})
                continue
            if p.is_file():
                files_to_scan.append(p)
            elif p.is_dir():
                for dirpath, _dirnames, filenames in os.walk(p, followlinks=False):
                    for fn in filenames:
                        files_to_scan.append(Path(dirpath) / fn)
            else:
                skipped.append({"path": path_str, "reason": "not_regular_or_dir"})

        matches: list[dict[str, Any]] = []
        files_scanned = 0
        truncated = False
        for fp in files_to_scan:
            if len(matches) >= max_matches:
                truncated = True
                break
            try:
                size = fp.stat().st_size
            except OSError as e:
                skipped.append({"path": str(fp), "reason": f"stat:{e.errno}"})
                continue
            if size > _MAX_FILE_BYTES:
                skipped.append({"path": str(fp), "reason": "too_large"})
                continue
            try:
                with fp.open("r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except OSError as e:
                skipped.append({"path": str(fp), "reason": f"read:{e.errno}"})
                continue
            files_scanned += 1
            for i, line in enumerate(lines):
                if rx.search(line):
                    if len(line) > _MAX_LINE_LEN:
                        line = line[:_MAX_LINE_LEN] + "…[truncated]"
                    before = (
                        [_clip(lines[j]) for j in range(max(0, i - ctx_lines), i)]
                        if ctx_lines else []
                    )
                    after = (
                        [_clip(lines[j]) for j in range(i + 1, min(len(lines), i + 1 + ctx_lines))]
                        if ctx_lines else []
                    )
                    matches.append({
                        "path":   str(fp),
                        "lineno": i + 1,
                        "line":   line.rstrip("\n"),
                        "before": [b.rstrip("\n") for b in before],
                        "after":  [a.rstrip("\n") for a in after],
                    })
                    if len(matches) >= max_matches:
                        truncated = True
                        break
            if truncated:
                break

        return ToolResult(
            output={
                "pattern_compiled": True,
                "files_scanned":    files_scanned,
                "match_count":      len(matches),
                "truncated":        truncated,
                "matches":          matches,
                "skipped":          skipped,
            },
            metadata={
                "pattern_length": len(args["pattern"]),
                "flags":          flag_names,
                "max_matches":    max_matches,
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"scanned {files_scanned} files, "
                f"{len(matches)} match{'es' if len(matches) != 1 else ''}"
                + (" (truncated)" if truncated else "")
            ),
        )


def _clip(line: str) -> str:
    return line if len(line) <= _MAX_LINE_LEN else line[:_MAX_LINE_LEN] + "…[truncated]"
