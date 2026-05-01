"""``code_edit.v1`` — write a file to the local filesystem.

Side effects: ``filesystem``. The constitution constraint resolver
treats ``filesystem`` as gate-worthy — when an agent's traits + role
+ genre warrant it, ``requires_human_approval=true`` lands on the
constraint set and every dispatch hits the operator queue first.
For the coding triune, Engineer (actuator genre) gets this; Architect
and Reviewer don't.

Per-agent constitution must populate (under code_edit.v1's constraints
block):
  allowed_paths: [/abs/path/to/repo, ...]   # write-target allowlist

Path-allowlist semantics are identical to code_read.v1 — same
``_is_within_any`` check after resolving to an absolute symlink-free
path. The agent cannot escape into ``/etc`` or ``/Users/<other>`` no
matter what trick they try with relative paths or symlinks.

Atomic write: content lands in a sibling temp file first, then
``rename()`` swaps it into place. If the operator pulls the plug
mid-write, either the old file or the new file is on disk — never a
half-written file.

Modes:
  - "write" (default): replaces the file's content (or creates if
    missing). The most common case for the coding flow.
  - "append": adds content to the end. Useful for log/diary files,
    not for source code (source code edits should be full-file
    replacements so diffs are clean).

Future evolution:
  - v2: line-range patch (insert/delete/replace specific lines)
  - v2: dry-run mode that returns the diff without writing
  - v2: conflict detection (refuse if the file's sha256 changed since
        the agent last read it — optimistic concurrency)
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)
from forest_soul_forge.tools.builtin.code_read import (
    _is_within_any,
    _resolve_allowlist,
)

MAX_CONTENT_BYTES = 5_000_000   # 5 MB ceiling — same as Linux PIPE_BUF * many; sane upper bound
ALLOWED_MODES = ("write", "append")


class CodeEditError(ToolValidationError):
    """Raised by code_edit for path/mode/write failures."""


class CodeEditTool:
    """Args:
      path (str, required): absolute or relative path to write.
        Resolved to an absolute symlink-free form before checking
        the agent's allowed_paths.
      content (str, required): the new file content (utf-8). Must be
        ≤ 5 MB.
      mode (str, optional): "write" (default; replaces content) or
        "append" (adds to end). For source code edits, prefer
        "write" so diffs are clean.

    Output:
      {
        "path":           str,    # the resolved absolute path
        "bytes_written":  int,    # length of the content written this call
        "size_after":     int,    # the file's total size after the operation
        "sha256_after":   str,    # SHA-256 of the file's full contents post-write
        "created":        bool,   # true if the file did not exist before this call
        "mode":           str,    # "write" or "append"
      }

    Constraints (read from ctx.constraints):
      allowed_paths: list[str]        # required, absolute paths
      requires_human_approval: bool   # if true, dispatcher gates upstream
    """

    name = "code_edit"
    version = "1"
    side_effects = "filesystem"
    # ADR-0021-amendment §5 — code_edit writes to allowlisted paths under
    # constitutional policy (reversible-with-policy class). Required
    # initiative L4. SW-track Engineer (Actuator genre, default L5)
    # reaches; Companion / Observer / Researcher do not. The
    # ApprovalGateStep handles per-call approval on top — initiative
    # is the structural floor that says "this agent is allowed to be
    # in the business of editing code at all."
    required_initiative_level = "L4"

    def validate(self, args: dict[str, Any]) -> None:
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ToolValidationError("path is required and must be a non-empty string")
        content = args.get("content")
        if not isinstance(content, str):
            raise ToolValidationError(
                f"content is required and must be a string; got {type(content).__name__}"
            )
        if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
            raise ToolValidationError(
                f"content exceeds {MAX_CONTENT_BYTES} byte ceiling — "
                f"split the write into smaller calls"
            )
        mode = args.get("mode", "write")
        if mode not in ALLOWED_MODES:
            raise ToolValidationError(
                f"mode must be one of {ALLOWED_MODES}; got {mode!r}"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        raw_path: str = args["path"]
        content: str = args["content"]
        mode: str = args.get("mode", "write")

        allowed_paths_raw = ctx.constraints.get("allowed_paths") or ()
        if not allowed_paths_raw:
            raise CodeEditError(
                "agent has no allowed_paths in its constitution — "
                "code_edit.v1 refuses to touch the filesystem"
            )
        allowed_roots = tuple(_resolve_allowlist(allowed_paths_raw))

        # For writes, we don't require the file to already exist (the
        # whole point is creation). But the PARENT directory must exist
        # AND must be inside the allowlist.
        target = Path(raw_path)
        if not target.is_absolute():
            # Resolve against cwd. We'll re-check the resolved form
            # against the allowlist before the actual write.
            target = target.resolve(strict=False)

        parent = target.parent
        try:
            parent_resolved = parent.resolve(strict=True)
        except FileNotFoundError:
            raise CodeEditError(
                f"parent directory does not exist: {parent}. "
                f"code_edit.v1 won't create directories — make the dir "
                f"yourself first via shell_exec.v1 if you have it."
            )
        target_resolved = parent_resolved / target.name

        if not _is_within_any(target_resolved, allowed_roots):
            raise CodeEditError(
                f"path {str(target_resolved)!r} is outside the agent's allowed_paths "
                f"({[str(p) for p in allowed_roots]})"
            )

        existed_before = target_resolved.exists()
        if existed_before and not target_resolved.is_file():
            raise CodeEditError(
                f"target exists but isn't a regular file: {target_resolved}"
            )

        encoded = content.encode("utf-8")

        try:
            if mode == "write":
                _atomic_write(target_resolved, encoded)
                final_bytes = encoded
            else:  # append
                with target_resolved.open("ab") as f:
                    f.write(encoded)
                final_bytes = target_resolved.read_bytes()
        except OSError as e:
            raise CodeEditError(f"write failed: {e}") from e

        sha = hashlib.sha256(final_bytes).hexdigest()
        size_after = len(final_bytes)

        return ToolResult(
            output={
                "path":          str(target_resolved),
                "bytes_written": len(encoded),
                "size_after":    size_after,
                "sha256_after":  sha,
                "created":       not existed_before,
                "mode":          mode,
            },
            metadata={
                "allowed_roots": [str(p) for p in allowed_roots],
            },
            side_effect_summary=(
                f"code_edit: {mode} {len(encoded)}b → {target_resolved.name} "
                f"({'created' if not existed_before else 'replaced'})"
            ),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _atomic_write(target: Path, data: bytes) -> None:
    """Write atomically: temp file in same directory + rename().

    Same-directory is required so the rename is a single inode swap
    (not a cross-filesystem copy). The temp file is removed on any
    exception path so we don't leak partial files into the dir.
    """
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise
