"""``git_blame_read.v1`` — per-line blame via ``git blame --porcelain``.

Side effects: read_only. ``git blame`` reads commit objects and
the file at the requested ref; nothing in the repo is mutated.

Fifth Phase G.1.A programming primitive (after ruff_lint.v1,
pytest_run.v1, git_log_read.v1, git_diff_read.v1). Where
``git_log_read`` answers "what's the history?" and
``git_diff_read`` answers "what does this change look like?",
``git_blame_read`` answers "who last touched this specific line,
when, and in what commit?".

The porcelain output format is the canonical machine-readable
shape — line groups are emitted as a stateful header block
followed by tab-prefixed content lines, with subsequent lines
from the same commit having only a short header. We parse that
format into a flat per-line list keyed by current-file line
number.

Per-agent constitution must populate:
  allowed_paths: [/abs/path/to/repo, ...]

Path discipline mirrors git_log_read.v1 / git_diff_read.v1:
  - Resolve to absolute symlink-free form before checking allowlist
  - File must exist; resolved path must be a regular file
  - is_relative_to defense against ../ escape

Argument-injection defense:
  - ref strings rejected if they start with '-' (would smuggle
    flags into argv as positional)
  - line_range bounds validated as positive ints with start <= end

Truncation:
  - max_lines (default 500, ceiling 5000) caps entries returned
  - line_range narrows the scan upstream of max_lines (so a tight
    range gives a precise read of a hot section)

Future evolution:
  - v2: ``-w`` (ignore whitespace), ``-C`` (detect copies)
  - v2: incremental output streaming for very large files
"""
from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_MAX_LINES = 500
GIT_BLAME_MAX_LINES_HARD_CAP = 5000
DEFAULT_TIMEOUT_SECONDS = 30
GIT_BLAME_TIMEOUT_HARD_CAP = 120
GIT_BLAME_TIMEOUT_MIN = 1


class GitBlameReadError(ToolValidationError):
    """Raised by git_blame_read for path-allowlist or invocation failures."""


class GitNotInstalledError(GitBlameReadError):
    """Raised when the ``git`` binary is not on PATH."""


class NotAGitRepoError(GitBlameReadError):
    """Raised when the resolved path is not inside a git repository."""


class GitBlameReadTool:
    """Args:
      path (str, required): absolute or relative path to a file
        inside a git repo. Must be a regular file (not a directory
        — git blame is per-file).
      ref (str, optional): commit/branch/tag to blame at. Default
        HEAD. Validated to reject argument-injection.
      line_range (list[int, int], optional): inclusive [start, end]
        (1-indexed) — forwarded as ``-L start,end``.
      max_lines (int, optional): cap on entries returned. Default
        500, max 5000.
      timeout_seconds (int, optional): subprocess timeout. Default
        30, max 120.

    Output:
      {
        "path":         str,    # resolved absolute file path
        "ref":          str,    # the ref blame was taken at
        "lines_count":  int,
        "truncated":    bool,
        "lines": [
          {
            "line_no":          int,   # 1-indexed in the current file
            "original_line_no": int,   # 1-indexed in the commit's file
            "sha":              str,
            "author_name":      str,
            "author_email":     str,
            "author_date":      str,   # ISO 8601 UTC
            "summary":          str,
            "content":          str,
          }, ...
        ]
      }

    Constraints (read from ctx.constraints):
      allowed_paths: list[str]   # required, absolute paths
    """

    name = "git_blame_read"
    version = "1"
    side_effects = "read_only"
    # No required_initiative_level — read_only tools pass at any L
    # per ADR-0021-amendment §5.

    def validate(self, args: dict[str, Any]) -> None:
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ToolValidationError(
                "path is required and must be a non-empty string"
            )

        ref = args.get("ref")
        if ref is not None:
            if not isinstance(ref, str) or not ref.strip():
                raise ToolValidationError(
                    "ref must be a non-empty string when provided"
                )
            _validate_ref_string(ref)

        line_range = args.get("line_range")
        if line_range is not None:
            if (
                not isinstance(line_range, list)
                or len(line_range) != 2
                or not all(isinstance(x, int) for x in line_range)
            ):
                raise ToolValidationError(
                    "line_range must be a [start, end] list of two ints"
                )
            start, end = line_range
            if start < 1 or end < 1 or start > end:
                raise ToolValidationError(
                    f"line_range must satisfy 1 <= start <= end; "
                    f"got [{start}, {end}]"
                )

        max_lines = args.get("max_lines", DEFAULT_MAX_LINES)
        if (
            not isinstance(max_lines, int)
            or max_lines < 1
            or max_lines > GIT_BLAME_MAX_LINES_HARD_CAP
        ):
            raise ToolValidationError(
                f"max_lines must be a positive int <= "
                f"{GIT_BLAME_MAX_LINES_HARD_CAP}; got {max_lines!r}"
            )

        timeout = args.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        if (
            not isinstance(timeout, int)
            or timeout < GIT_BLAME_TIMEOUT_MIN
            or timeout > GIT_BLAME_TIMEOUT_HARD_CAP
        ):
            raise ToolValidationError(
                f"timeout_seconds must be in [{GIT_BLAME_TIMEOUT_MIN}, "
                f"{GIT_BLAME_TIMEOUT_HARD_CAP}]; got {timeout!r}"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        raw_path: str = args["path"]
        ref = args.get("ref") or "HEAD"
        line_range = args.get("line_range")
        max_lines = int(args.get("max_lines", DEFAULT_MAX_LINES))
        timeout_seconds = int(
            args.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        )

        allowed_paths_raw = ctx.constraints.get("allowed_paths") or ()
        if not allowed_paths_raw:
            raise GitBlameReadError(
                "agent has no allowed_paths in its constitution — "
                "git_blame_read.v1 refuses to touch the filesystem"
            )

        allowed_roots = tuple(_resolve_allowlist(allowed_paths_raw))

        try:
            target = Path(raw_path).resolve(strict=True)
        except FileNotFoundError:
            raise GitBlameReadError(f"path does not exist: {raw_path!r}")
        except OSError as e:
            raise GitBlameReadError(f"path resolution failed: {e}") from e

        if not target.is_file():
            raise GitBlameReadError(
                f"path must be a regular file (not a directory); "
                f"got {str(target)!r}"
            )

        if not _is_within_any(target, allowed_roots):
            raise GitBlameReadError(
                f"path {str(target)!r} is outside the agent's "
                f"allowed_paths ({[str(p) for p in allowed_roots]})"
            )

        git_bin = _locate_git()
        if git_bin is None:
            raise GitNotInstalledError(
                "git is not installed (not on PATH). Install via your "
                "OS package manager."
            )

        # Build argv. We invoke from the file's parent dir and pass
        # the basename — keeps the cwd inside the agent's allowed
        # tree even if the file lives in a deep subdirectory.
        argv: list[str] = [
            git_bin, "-C", str(target.parent),
            "blame", "--porcelain",
        ]
        if line_range is not None:
            argv.append(f"-L{line_range[0]},{line_range[1]}")
        argv.append(ref)
        argv.append("--")
        argv.append(target.name)

        proc = _run_git(argv, timeout_seconds)
        if proc.returncode != 0:
            stderr_blurb = (proc.stderr or "").strip()
            if "not a git repository" in stderr_blurb.lower():
                raise NotAGitRepoError(
                    f"path {str(target)!r} is not inside a git repository"
                )
            raise GitBlameReadError(
                f"git blame exited with code {proc.returncode}: "
                f"{stderr_blurb[:500]}"
            )

        lines = _parse_porcelain(proc.stdout)

        actual_count = len(lines)
        truncated = actual_count > max_lines
        kept = lines[:max_lines]

        return ToolResult(
            output={
                "path":        str(target),
                "ref":         ref,
                "lines_count": len(kept),
                "truncated":   truncated,
                "lines":       kept,
            },
            metadata={
                "allowed_roots": [str(p) for p in allowed_roots],
                "actual_count":  actual_count,
                "max_lines":     max_lines,
                "git_bin":       git_bin,
                "line_range":    line_range,
            },
            side_effect_summary=(
                f"git_blame_read: {len(kept)} lines on "
                f"{target.name}@{ref} (truncated={truncated})"
            ),
        )


# ---------------------------------------------------------------------------
# Helpers — module-level for testability
# ---------------------------------------------------------------------------
def _locate_git() -> str | None:
    return shutil.which("git")


def _run_git(argv: list[str], timeout: int) -> subprocess.CompletedProcess:
    import os
    try:
        return subprocess.run(
            argv,
            timeout=timeout,
            capture_output=True,
            text=True,
            check=False,
            env={
                "GIT_TERMINAL_PROMPT": "0",
                "PATH": os.environ.get("PATH", ""),
                "LC_ALL": "C",
            },
        )
    except subprocess.TimeoutExpired as e:
        raise GitBlameReadError(
            f"git blame timed out after {timeout}s; narrow with "
            f"line_range or scope the file"
        ) from e
    except FileNotFoundError as e:
        raise GitNotInstalledError(
            f"git invocation failed at exec time: {e}"
        ) from e


def _validate_ref_string(ref: str) -> None:
    """Reject ref strings that look like argument injection attempts."""
    if ref.startswith("-"):
        raise ToolValidationError(
            f"ref must not start with '-' (would be misinterpreted "
            f"as a flag): {ref!r}"
        )


def _parse_porcelain(stdout: str) -> list[dict[str, Any]]:
    """Parse ``git blame --porcelain`` output into a per-line list.

    Porcelain stream shape (per blame group):
      <sha> <orig_line> <final_line> [<num_lines_in_group>]
      author <name>
      author-mail <<email>>
      author-time <unix_ts>
      author-tz <+HHMM>
      committer <name>
      committer-mail <<email>>
      committer-time <unix_ts>
      committer-tz <+HHMM>
      summary <commit subject>
      [boundary]            (only on root commit)
      [previous <sha> <path>]
      filename <repo-relative path>
      \\t<content line>

    Subsequent lines from the same commit get only:
      <sha> <orig_line> <final_line>
      \\t<content line>

    We maintain a per-sha metadata cache so repeated commits don't
    need their headers re-emitted (which is what porcelain does on
    the wire).
    """
    out: list[dict[str, Any]] = []
    sha_meta: dict[str, dict[str, Any]] = {}
    cur_sha: str | None = None
    cur_orig: int = 0
    cur_final: int = 0
    in_meta_block = False

    lines_iter = iter(stdout.splitlines())
    for raw in lines_iter:
        if raw.startswith("\t"):
            # Content line — emit one entry using the current commit's
            # cached metadata.
            content = raw[1:]
            meta = sha_meta.get(cur_sha or "", {})
            out.append({
                "line_no":          cur_final,
                "original_line_no": cur_orig,
                "sha":              cur_sha or "",
                "author_name":      meta.get("author_name", ""),
                "author_email":     meta.get("author_email", ""),
                "author_date":      meta.get("author_date", ""),
                "summary":          meta.get("summary", ""),
                "content":          content,
            })
            in_meta_block = False
            continue

        # Header line. Either a blame-group header (sha ...) or a
        # metadata key/value within a header block.
        parts = raw.split(" ", 1)
        if not parts:
            continue
        head = parts[0]
        rest = parts[1] if len(parts) > 1 else ""

        if len(head) == 40 and all(c in "0123456789abcdef" for c in head):
            # Blame-group header line.
            tokens = raw.split()
            cur_sha = tokens[0]
            cur_orig = int(tokens[1])
            cur_final = int(tokens[2])
            sha_meta.setdefault(cur_sha, {})
            in_meta_block = True
            continue

        if not in_meta_block or cur_sha is None:
            continue

        meta = sha_meta[cur_sha]
        if head == "author":
            meta["author_name"] = rest
        elif head == "author-mail":
            meta["author_email"] = rest.strip("<>")
        elif head == "author-time":
            try:
                ts = int(rest)
                meta["_author_time"] = ts
            except ValueError:
                pass
        elif head == "author-tz":
            meta["_author_tz"] = rest
            # Combine time + tz into ISO if both present
            if "_author_time" in meta:
                meta["author_date"] = _format_unix_with_tz(
                    meta["_author_time"], rest,
                )
        elif head == "summary":
            meta["summary"] = rest
        # committer-* and other fields are ignored at v1; can be added
        # in v2 if a consumer asks.

    return out


def _format_unix_with_tz(ts: int, tz: str) -> str:
    """Format a unix timestamp with a git-style ``+HHMM`` offset into
    an ISO 8601 string. Best-effort — falls back to UTC ISO if the
    offset doesn't parse."""
    try:
        sign = 1 if tz.startswith("+") else -1
        hh = int(tz[1:3])
        mm = int(tz[3:5])
        offset_minutes = sign * (hh * 60 + mm)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        # Convert by computing offset; we don't bother building a
        # tzinfo object since timezone.utc + a fixed offset is enough
        # for a stable string representation.
        from datetime import timedelta
        local_dt = dt + timedelta(minutes=offset_minutes)
        return local_dt.strftime(f"%Y-%m-%dT%H:%M:%S{tz[:3]}:{tz[3:5]}")
    except Exception:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _resolve_allowlist(paths: Any) -> tuple[Path, ...]:
    out: list[Path] = []
    for raw in paths:
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            out.append(Path(raw).resolve(strict=False))
        except OSError:
            continue
    return tuple(out)


def _is_within_any(target: Path, roots: tuple[Path, ...]) -> bool:
    for root in roots:
        try:
            if target == root or target.is_relative_to(root):
                return True
        except (ValueError, AttributeError):
            continue
    return False


__all__ = [
    "GitBlameReadTool",
    "GitBlameReadError",
    "GitNotInstalledError",
    "NotAGitRepoError",
    "DEFAULT_MAX_LINES",
    "GIT_BLAME_MAX_LINES_HARD_CAP",
    "DEFAULT_TIMEOUT_SECONDS",
]
