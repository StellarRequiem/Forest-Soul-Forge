"""``file_integrity.v1`` — sha256 baseline + diff over operator-named paths.

ADR-0033 Phase B1. The low-tier swarm primitive for "did anything
under /etc/ change since I last looked?" The tool walks each path
(file or directory), computes sha256 of every regular file it
reaches, and either:

  * ``mode='snapshot'`` — emits a fresh snapshot dict (path → digest)
  * ``mode='diff'`` — compares against a baseline dict the caller
                       passed in and emits added / removed / changed
                       sets

Symlinks are NOT followed; the tool reports them as ``symlink:<target>``
in the digest column. Doing otherwise lets an attacker plant a
symlink under a watched directory pointing at /etc/passwd and
trick the digest algorithm into hashing the target. PatchPatrol +
LogLurker both reach for this for their daily sweeps.

Side-effects classification: ``read_only``. The tool only reads
filesystem; the snapshot/diff is returned to the caller, never
persisted by the tool. Persisting baselines is the agent's job
(via ``memory_write.v1`` at scope='lineage' so the agent's
descendants in the swarm chain can recall it).
"""
from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


# Cap to keep operator typos from running the tool over a billion
# files. Operators with legitimate large-tree needs can chunk by
# subdirectory; the cap is per-call.
_MAX_FILES = 5000
_MAX_FILE_BYTES = 256 * 1024 * 1024  # 256 MiB cap on a single file
_VALID_MODES = ("snapshot", "diff")


class FileIntegrityTool:
    """sha256 a set of paths; optionally diff against a baseline.

    Args:
      paths (list[str], required): files or directories to walk.
        Directories are walked recursively. Symlinks are NOT
        followed. ≤ 200 path entries per call.
      mode (str, optional): "snapshot" (default) emits a fresh
        digest map. "diff" compares against ``baseline``.
      baseline (object, optional): when mode='diff', a dict of
        {path: digest} from a prior snapshot. Required for diff
        mode; ignored in snapshot mode.

    Output (snapshot mode):
      {
        "mode":       "snapshot",
        "files":      int,
        "digests":    {path: digest, ...},
        "skipped":    [{path, reason}, ...],   # too-large, errors
      }

    Output (diff mode):
      {
        "mode":       "diff",
        "added":      {path: digest, ...},
        "removed":    [path, ...],
        "changed":    [{path, before, after}, ...],
        "unchanged":  int,
        "skipped":    [{path, reason}, ...],
      }
    """

    name = "file_integrity"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        paths = args.get("paths")
        if not isinstance(paths, list) or not paths:
            raise ToolValidationError(
                "paths must be a non-empty list of strings"
            )
        if len(paths) > 200:
            raise ToolValidationError(
                f"paths must be ≤ 200 entries; got {len(paths)}"
            )
        for p in paths:
            if not isinstance(p, str) or not p:
                raise ToolValidationError(
                    "every path must be a non-empty string"
                )
        mode = args.get("mode", "snapshot")
        if mode not in _VALID_MODES:
            raise ToolValidationError(
                f"mode must be one of {list(_VALID_MODES)}; got {mode!r}"
            )
        if mode == "diff":
            base = args.get("baseline")
            if not isinstance(base, dict):
                raise ToolValidationError(
                    "mode='diff' requires baseline object {path: digest, ...}"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        mode = args.get("mode", "snapshot")
        digests: dict[str, str] = {}
        skipped: list[dict[str, str]] = []
        total_files = 0

        for path_str in args["paths"]:
            try:
                p = Path(path_str)
                if not p.exists():
                    skipped.append({"path": path_str, "reason": "not_found"})
                    continue
                # lstat so we don't traverse symlinks at the top level.
                st = p.lstat()
                if stat.S_ISLNK(st.st_mode):
                    digests[str(p)] = f"symlink:{os.readlink(p)}"
                    total_files += 1
                elif p.is_file():
                    if total_files >= _MAX_FILES:
                        skipped.append({"path": path_str, "reason": "max_files_cap"})
                        continue
                    digest, reason = _hash_file(p)
                    if digest is None:
                        skipped.append({"path": str(p), "reason": reason})
                    else:
                        digests[str(p)] = digest
                        total_files += 1
                elif p.is_dir():
                    for sub in _walk(p):
                        if total_files >= _MAX_FILES:
                            skipped.append({"path": str(sub), "reason": "max_files_cap"})
                            continue
                        sub_st = sub.lstat()
                        if stat.S_ISLNK(sub_st.st_mode):
                            digests[str(sub)] = f"symlink:{os.readlink(sub)}"
                            total_files += 1
                            continue
                        if not sub.is_file():
                            continue
                        digest, reason = _hash_file(sub)
                        if digest is None:
                            skipped.append({"path": str(sub), "reason": reason})
                        else:
                            digests[str(sub)] = digest
                            total_files += 1
                else:
                    skipped.append({"path": path_str, "reason": "not_regular_or_dir"})
            except OSError as e:
                skipped.append({"path": path_str, "reason": f"oserror:{e.errno}"})

        if mode == "snapshot":
            return ToolResult(
                output={
                    "mode":    "snapshot",
                    "files":   total_files,
                    "digests": digests,
                    "skipped": skipped,
                },
                metadata={"max_files_capped": total_files >= _MAX_FILES},
                tokens_used=None, cost_usd=None,
                side_effect_summary=f"snapshot: {total_files} files",
            )

        # mode='diff'
        baseline: dict[str, str] = args["baseline"]
        added: dict[str, str] = {}
        removed: list[str] = []
        changed: list[dict[str, str]] = []
        unchanged = 0
        for p, d in digests.items():
            if p not in baseline:
                added[p] = d
            elif baseline[p] != d:
                changed.append({
                    "path":   p,
                    "before": baseline[p],
                    "after":  d,
                })
            else:
                unchanged += 1
        for p in baseline:
            if p not in digests:
                removed.append(p)

        return ToolResult(
            output={
                "mode":      "diff",
                "added":     added,
                "removed":   removed,
                "changed":   changed,
                "unchanged": unchanged,
                "skipped":   skipped,
            },
            metadata={
                "files_after":  total_files,
                "files_before": len(baseline),
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"diff: +{len(added)} -{len(removed)} ~{len(changed)} ={unchanged}"
            ),
        )


def _walk(root: Path):
    """Yield every entry under ``root`` (not following symlinks).
    Caller is responsible for the directory/file/symlink decision."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            yield Path(dirpath) / name


def _hash_file(p: Path) -> tuple[str | None, str | None]:
    """Return (digest, None) on success, (None, reason) on skip."""
    try:
        size = p.stat().st_size
    except OSError as e:
        return None, f"stat:{e.errno}"
    if size > _MAX_FILE_BYTES:
        return None, "too_large"
    h = hashlib.sha256()
    try:
        with p.open("rb") as f:
            while True:
                chunk = f.read(64 * 1024)
                if not chunk:
                    break
                h.update(chunk)
    except OSError as e:
        return None, f"read:{e.errno}"
    return "sha256:" + h.hexdigest(), None
