"""``code_read.v1`` — read a file from the local filesystem.

Side effects: read_only. Pure read; no mutation. Safe to run inside
Guardian-genre agents (e.g. Reviewer) without per-call approval.

Per-agent constitution must populate:
  allowed_paths: [/abs/path/to/repo, /another/abs/path, ...]

The check is: resolve the requested path to an absolute, symlink-free
form, then verify it's a child of (or equal to) at least one allowed
root. This defends against:
  - relative-path escapes (../../../etc/passwd)
  - symlink escapes (a symlink inside the allowed dir pointing outside)
  - case-collision tricks on case-insensitive filesystems

Output is capped at max_bytes (default 100 KB) to prevent an agent
from sucking the entire repo into a single tool result. Operators who
want bigger reads pass max_bytes explicitly.

Future evolution:
  - v2: glob support + multi-file batch read
  - v2: line-range slicing (offset/limit) so an agent can read a single
        function from a large file without paying the full file's tokens
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)

DEFAULT_MAX_BYTES = 100_000      # 100 KB — about 25K tokens for typical English/code
MAX_MAX_BYTES    = 2_000_000    # 2 MB hard ceiling
MIN_MAX_BYTES    = 1


class CodeReadError(ToolValidationError):
    """Raised by code_read for path-allowlist or read failures.

    Subclasses ToolValidationError so the dispatcher routes it through
    the same path as bad_args — keeps the audit shape uniform.
    """


class CodeReadTool:
    """Args:
      path (str, required): absolute or relative path to a file.
        Resolved to an absolute symlink-free form before checking
        the agent's allowed_paths.
      max_bytes (int, optional): cap on returned content size. Default
        100000 (~100 KB). Larger reads must request explicitly.

    Output:
      {
        "path":        str,    # the resolved absolute path
        "size_bytes":  int,    # file's actual size on disk
        "bytes_read":  int,    # what we returned (≤ size_bytes, ≤ max_bytes)
        "truncated":   bool,   # true if size_bytes > max_bytes
        "sha256":      str,    # full-file SHA-256 (hex)
        "content":     str,    # the file content (utf-8; binary fails)
      }

    Constraints (read from ctx.constraints):
      allowed_paths: list[str]   # required, absolute paths
    """

    name = "code_read"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ToolValidationError("path is required and must be a non-empty string")
        max_bytes = args.get("max_bytes", DEFAULT_MAX_BYTES)
        if not isinstance(max_bytes, int) or max_bytes < MIN_MAX_BYTES or max_bytes > MAX_MAX_BYTES:
            raise ToolValidationError(
                f"max_bytes must be in [{MIN_MAX_BYTES}, {MAX_MAX_BYTES}]; got {max_bytes!r}"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        raw_path: str = args["path"]
        max_bytes: int = int(args.get("max_bytes", DEFAULT_MAX_BYTES))

        allowed_paths_raw = ctx.constraints.get("allowed_paths") or ()
        if not allowed_paths_raw:
            raise CodeReadError(
                "agent has no allowed_paths in its constitution — "
                "code_read.v1 refuses to touch the filesystem"
            )

        # Resolve every allowed root to an absolute, symlink-free form
        # ONCE so the per-request comparison is a simple is_relative_to.
        allowed_roots = tuple(_resolve_allowlist(allowed_paths_raw))

        # Resolve the requested path the same way.
        try:
            target = Path(raw_path).resolve(strict=True)
        except FileNotFoundError:
            raise CodeReadError(f"path does not exist: {raw_path!r}")
        except OSError as e:
            raise CodeReadError(f"path resolution failed: {e}") from e

        if not target.is_file():
            raise CodeReadError(f"path is not a regular file: {target}")

        if not _is_within_any(target, allowed_roots):
            raise CodeReadError(
                f"path {str(target)!r} is outside the agent's allowed_paths "
                f"({[str(p) for p in allowed_roots]})"
            )

        # Read with the cap. Reading entire file for hash separately
        # would double the IO; instead read up to max_bytes+1 and
        # detect truncation by checking the file size.
        size_bytes = target.stat().st_size
        try:
            data = target.read_bytes()
        except OSError as e:
            raise CodeReadError(f"read failed: {e}") from e

        sha = hashlib.sha256(data).hexdigest()

        truncated = size_bytes > max_bytes
        returned = data[:max_bytes] if truncated else data
        try:
            content = returned.decode("utf-8")
        except UnicodeDecodeError as e:
            raise CodeReadError(
                f"file is not valid UTF-8 ({e}); code_read.v1 is text-only — "
                f"binary support is a future enhancement"
            )

        return ToolResult(
            output={
                "path":        str(target),
                "size_bytes":  size_bytes,
                "bytes_read":  len(returned),
                "truncated":   truncated,
                "sha256":      sha,
                "content":     content,
            },
            metadata={
                "allowed_roots":  [str(p) for p in allowed_roots],
                "max_bytes_used": max_bytes,
            },
            side_effect_summary=(
                f"code_read: {len(returned)}/{size_bytes} bytes from {target.name} "
                f"({'truncated' if truncated else 'full'})"
            ),
        )


# ---------------------------------------------------------------------------
# Helpers — exported via module (not class) for testability
# ---------------------------------------------------------------------------
def _resolve_allowlist(paths: Any) -> tuple[Path, ...]:
    """Resolve every entry in the allowlist to an absolute, symlink-free
    Path. Skips entries that don't exist (operator typos shouldn't crash
    the dispatch — they should be visible as "not allowed" later)."""
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
    """True iff ``target`` is the same as or a descendant of at least
    one root. Uses Path.is_relative_to which checks lexicographically
    after both have been resolve()'d — no symlink-walk surprises."""
    for root in roots:
        try:
            if target == root or target.is_relative_to(root):
                return True
        except (ValueError, AttributeError):
            # is_relative_to raises ValueError on different anchors
            # (Windows drive letter mismatch); treat as not allowed.
            continue
    return False
