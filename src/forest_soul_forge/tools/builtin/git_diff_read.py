"""``git_diff_read.v1`` — read structured diff via ``git diff``.

Side effects: read_only. ``git diff`` reads tree state, working
copy, and the index; it does not mutate the repo. Pure inspection
primitive.

Fourth Phase G.1.A programming primitive (after ruff_lint.v1,
pytest_run.v1, git_log_read.v1). Where ``git_log_read`` answers
"what's the history?", ``git_diff_read`` answers "what does this
specific change look like?". SW-track Reviewer (Guardian L3) is the
primary consumer — diffing a feature branch against main is the
canonical entry point of a code review.

Three diff modes:
  - mode="refs"    — diff between two refs (ref_a, ref_b both
                     required). The classic "show me what changed
                     between X and Y" call.
  - mode="staged"  — diff of the index against HEAD
                     (``git diff --cached``). What's about to be
                     committed.
  - mode="working" — diff of the working tree against HEAD
                     (``git diff`` with no refs). Unstaged changes.

Per-agent constitution must populate:
  allowed_paths: [/abs/path/to/repo, ...]

Path discipline mirrors ``git_log_read.v1``:
  - Resolve to absolute symlink-free form before checking allowlist
  - is_relative_to defense against ../ escape
  - paths_filter entries each verified within allowed_paths

Output shape (structured patch + numstat):
  files: [
    {
      old_path / new_path,
      status: modified|added|deleted|renamed|copied,
      is_binary: bool,
      additions / deletions: int  (numstat; -1 for binary),
      hunks: [{old_start, old_count, new_start, new_count, header, body}],
      body_truncated: bool,
    }, ...
  ]

Truncation:
  - max_files (default 100, ceiling 1000) caps file count
  - max_lines_per_file (default 500, ceiling 5000) caps hunk-body
    lines per file (the patch is truncated at the last full line
    boundary that fits)

Future evolution:
  - v2: --find-renames threshold tuning
  - v2: word-diff mode for prose-heavy files
  - v2: --color=always with structured ANSI extraction
"""
from __future__ import annotations

import re
import shutil
import subprocess
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
DEFAULT_MAX_FILES = 100
GIT_DIFF_MAX_FILES_HARD_CAP = 1000
DEFAULT_MAX_LINES_PER_FILE = 500
GIT_DIFF_MAX_LINES_HARD_CAP = 5000
DEFAULT_TIMEOUT_SECONDS = 30
GIT_DIFF_TIMEOUT_HARD_CAP = 120
GIT_DIFF_TIMEOUT_MIN = 1

VALID_MODES = ("refs", "staged", "working")

# Hunk header: @@ -<old_start>[,<old_count>] +<new_start>[,<new_count>] @@ <ctx>
_HUNK_HEADER_RE = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$"
)


class GitDiffReadError(ToolValidationError):
    """Raised by git_diff_read for path-allowlist or invocation failures.
    Subclasses ToolValidationError so the dispatcher routes it through
    the same path as bad_args."""


class GitNotInstalledError(GitDiffReadError):
    """Raised when the ``git`` binary is not on PATH."""


class NotAGitRepoError(GitDiffReadError):
    """Raised when the resolved path is not inside a git repository."""


class GitDiffReadTool:
    """Args:
      path (str, required): absolute or relative path to a directory
        inside a git repo. We invoke ``git -C <path>``.
      mode (str, optional): one of "refs", "staged", "working".
        Default "working" (the most common day-to-day use).
      ref_a (str, optional): required when mode="refs"; the "from"
        ref. Validated against argument-injection.
      ref_b (str, optional): required when mode="refs"; the "to"
        ref. Validated against argument-injection.
      paths_filter (list[str], optional): trailing pathspec args.
        Each must resolve within the agent's allowed_paths.
      max_files (int, optional): cap on files in the output. Default
        100, max 1000.
      max_lines_per_file (int, optional): cap on hunk-body lines per
        file. Default 500, max 5000.
      timeout_seconds (int, optional): subprocess timeout. Default 30,
        max 120.

    Output:
      {
        "path":         str,    # resolved repo path
        "mode":         str,    # one of VALID_MODES
        "ref_a":        str|null,
        "ref_b":        str|null,
        "files_count":  int,    # files actually returned
        "truncated":    bool,   # true when more files matched than returned
        "files": [
          {
            "old_path":       str,
            "new_path":       str,
            "status":         str,
            "is_binary":      bool,
            "additions":      int,
            "deletions":      int,
            "hunks":          list[dict],
            "body_truncated": bool,
          }, ...
        ]
      }

    Constraints (read from ctx.constraints):
      allowed_paths: list[str]   # required, absolute paths
    """

    name = "git_diff_read"
    version = "1"
    side_effects = "read_only"
    # No required_initiative_level — read_only tools pass at any L per
    # ADR-0021-amendment §5. SW-track Reviewer (Guardian L3),
    # Architect (Observer L1+), and Engineer (Actuator L5) all reach.

    def validate(self, args: dict[str, Any]) -> None:
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ToolValidationError(
                "path is required and must be a non-empty string"
            )

        mode = args.get("mode", "working")
        if mode not in VALID_MODES:
            raise ToolValidationError(
                f"mode must be one of {VALID_MODES}; got {mode!r}"
            )

        if mode == "refs":
            for opt in ("ref_a", "ref_b"):
                val = args.get(opt)
                if not isinstance(val, str) or not val.strip():
                    raise ToolValidationError(
                        f"{opt} is required and must be a non-empty "
                        f"string when mode='refs'"
                    )
                _validate_ref_string(val, opt)
        else:
            # ref_a / ref_b should NOT be set in non-refs mode.
            for opt in ("ref_a", "ref_b"):
                if args.get(opt) is not None:
                    raise ToolValidationError(
                        f"{opt} must not be set when mode={mode!r}"
                    )

        max_files = args.get("max_files", DEFAULT_MAX_FILES)
        if (
            not isinstance(max_files, int)
            or max_files < 1
            or max_files > GIT_DIFF_MAX_FILES_HARD_CAP
        ):
            raise ToolValidationError(
                f"max_files must be a positive int <= "
                f"{GIT_DIFF_MAX_FILES_HARD_CAP}; got {max_files!r}"
            )

        max_lines = args.get("max_lines_per_file", DEFAULT_MAX_LINES_PER_FILE)
        if (
            not isinstance(max_lines, int)
            or max_lines < 1
            or max_lines > GIT_DIFF_MAX_LINES_HARD_CAP
        ):
            raise ToolValidationError(
                f"max_lines_per_file must be a positive int <= "
                f"{GIT_DIFF_MAX_LINES_HARD_CAP}; got {max_lines!r}"
            )

        paths_filter = args.get("paths_filter")
        if paths_filter is not None:
            if not isinstance(paths_filter, list) or any(
                not isinstance(p, str) or not p.strip() for p in paths_filter
            ):
                raise ToolValidationError(
                    "paths_filter must be a list of non-empty strings"
                )

        timeout = args.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        if (
            not isinstance(timeout, int)
            or timeout < GIT_DIFF_TIMEOUT_MIN
            or timeout > GIT_DIFF_TIMEOUT_HARD_CAP
        ):
            raise ToolValidationError(
                f"timeout_seconds must be in [{GIT_DIFF_TIMEOUT_MIN}, "
                f"{GIT_DIFF_TIMEOUT_HARD_CAP}]; got {timeout!r}"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        raw_path: str = args["path"]
        mode = args.get("mode", "working")
        ref_a = args.get("ref_a")
        ref_b = args.get("ref_b")
        paths_filter = args.get("paths_filter") or []
        max_files = int(args.get("max_files", DEFAULT_MAX_FILES))
        max_lines = int(
            args.get("max_lines_per_file", DEFAULT_MAX_LINES_PER_FILE)
        )
        timeout_seconds = int(
            args.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        )

        allowed_paths_raw = ctx.constraints.get("allowed_paths") or ()
        if not allowed_paths_raw:
            raise GitDiffReadError(
                "agent has no allowed_paths in its constitution — "
                "git_diff_read.v1 refuses to touch the filesystem"
            )

        allowed_roots = tuple(_resolve_allowlist(allowed_paths_raw))

        try:
            target = Path(raw_path).resolve(strict=True)
        except FileNotFoundError:
            raise GitDiffReadError(f"path does not exist: {raw_path!r}")
        except OSError as e:
            raise GitDiffReadError(f"path resolution failed: {e}") from e

        if not target.is_dir():
            raise GitDiffReadError(
                f"path must be a directory; got {str(target)!r}"
            )

        if not _is_within_any(target, allowed_roots):
            raise GitDiffReadError(
                f"path {str(target)!r} is outside the agent's "
                f"allowed_paths ({[str(p) for p in allowed_roots]})"
            )

        validated_paths: list[str] = []
        for raw in paths_filter:
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = target / candidate
            try:
                resolved = candidate.resolve(strict=False)
            except OSError as e:
                raise GitDiffReadError(
                    f"paths_filter entry {raw!r} could not be resolved: {e}"
                ) from e
            if not _is_within_any(resolved, allowed_roots):
                raise GitDiffReadError(
                    f"paths_filter entry {raw!r} resolves to "
                    f"{str(resolved)!r}, outside allowed_paths"
                )
            validated_paths.append(raw)

        git_bin = _locate_git()
        if git_bin is None:
            raise GitNotInstalledError(
                "git is not installed (not on PATH). Install via your "
                "OS package manager."
            )

        # Build argv. Order:
        #   git -C <repo> diff [--cached] --no-color -M -p [<refs>] [-- <paths>]
        argv: list[str] = [git_bin, "-C", str(target), "diff", "--no-color", "-M"]
        if mode == "staged":
            argv.append("--cached")
        # Always emit the patch; numstat is a separate invocation
        # because mixing --numstat with --patch produces a less
        # cleanly-parseable interleaved format.
        argv.append("-p")
        if mode == "refs":
            argv.append(ref_a)
            argv.append(ref_b)
        if validated_paths:
            argv.append("--")
            argv.extend(validated_paths)

        proc = _run_git(argv, timeout_seconds)
        if proc.returncode != 0:
            stderr_blurb = (proc.stderr or "").strip()
            if "not a git repository" in stderr_blurb.lower():
                raise NotAGitRepoError(
                    f"path {str(target)!r} is not inside a git repository"
                )
            raise GitDiffReadError(
                f"git diff exited with code {proc.returncode}: "
                f"{stderr_blurb[:500]}"
            )

        # Numstat in a second invocation. Same argv minus -p, plus --numstat.
        numstat_argv = list(argv)
        # Replace "-p" with "--numstat"
        for i, a in enumerate(numstat_argv):
            if a == "-p":
                numstat_argv[i] = "--numstat"
                break
        ns_proc = _run_git(numstat_argv, timeout_seconds)
        if ns_proc.returncode != 0:
            raise GitDiffReadError(
                f"git diff --numstat exited with code {ns_proc.returncode}: "
                f"{(ns_proc.stderr or '').strip()[:500]}"
            )
        numstat_map = _parse_numstat(ns_proc.stdout)

        files = _parse_diff_output(proc.stdout, max_lines, numstat_map)

        actual_count = len(files)
        truncated = actual_count > max_files
        kept = files[:max_files]

        return ToolResult(
            output={
                "path":        str(target),
                "mode":        mode,
                "ref_a":       ref_a if mode == "refs" else None,
                "ref_b":       ref_b if mode == "refs" else None,
                "files_count": len(kept),
                "truncated":   truncated,
                "files":       kept,
            },
            metadata={
                "allowed_roots":      [str(p) for p in allowed_roots],
                "actual_count":       actual_count,
                "max_files":          max_files,
                "max_lines_per_file": max_lines,
                "git_bin":            git_bin,
                "paths_filter":       validated_paths,
            },
            side_effect_summary=(
                f"git_diff_read[{mode}]: {len(kept)} files "
                f"(truncated={truncated})"
            ),
        )


# ---------------------------------------------------------------------------
# Helpers — module-level for testability
# ---------------------------------------------------------------------------
def _locate_git() -> str | None:
    """Find a working git binary on PATH."""
    return shutil.which("git")


def _run_git(argv: list[str], timeout: int) -> subprocess.CompletedProcess:
    """Run a git subprocess with a fixed env (no terminal prompts).
    Centralized here so both the patch and numstat invocations share
    the same defensive surface."""
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
                "LC_ALL": "C",  # stable error-message locale for parsing
            },
        )
    except subprocess.TimeoutExpired as e:
        raise GitDiffReadError(
            f"git diff timed out after {timeout}s; narrow with "
            f"paths_filter or scope the diff range"
        ) from e
    except FileNotFoundError as e:
        raise GitNotInstalledError(
            f"git invocation failed at exec time: {e}"
        ) from e


def _validate_ref_string(ref: str, opt_name: str = "ref") -> None:
    """Reject ref strings that look like argument injection attempts.

    Refs never start with ``-`` (which would smuggle a flag into argv).
    Reject anything starting with ``-``; everything else is left to
    git itself to validate.
    """
    if ref.startswith("-"):
        raise ToolValidationError(
            f"{opt_name} must not start with '-' (would be misinterpreted "
            f"as a flag): {ref!r}"
        )


def _parse_numstat(stdout: str) -> dict[str, tuple[int, int]]:
    """Parse ``git diff --numstat`` output into {path -> (additions, deletions)}.

    Format per line: ``<additions>\\t<deletions>\\t<path>``
    Binary files use ``-`` for both counts.
    Renames use ``old => new`` notation in the path column; we key by
    the post-rename path (which matches what we extract from the patch).
    """
    out: dict[str, tuple[int, int]] = {}
    for line in stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        adds_s, dels_s, path = parts
        adds = -1 if adds_s == "-" else _safe_int(adds_s)
        dels = -1 if dels_s == "-" else _safe_int(dels_s)
        # Strip rename arrows: ``old => new`` or ``{a => b}/file``
        if "=>" in path:
            # Take the post-arrow piece for keying. Best-effort.
            path = path.split("=>", 1)[1].strip(" {}")
        out[path] = (adds, dels)
    return out


def _safe_int(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        return 0


def _parse_diff_output(
    stdout: str,
    max_lines_per_file: int,
    numstat_map: dict[str, tuple[int, int]],
) -> list[dict[str, Any]]:
    """Parse ``git diff -p`` unified-patch output into structured files.

    The diff stream has a well-defined structure:
      diff --git a/<old> b/<new>
      [similarity index ...]
      [rename from ...]
      [rename to ...]
      [new file mode ...]
      [deleted file mode ...]
      [Binary files ... differ]
      [index ...]
      --- a/<path>  OR  --- /dev/null
      +++ b/<path>  OR  +++ /dev/null
      @@ ... @@ <ctx>
      <hunk lines>
      ...
    """
    files: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_hunk: dict[str, Any] | None = None
    body_lines_kept = 0  # for the current file's hunk-body cap

    def _push_current():
        nonlocal current, current_hunk
        if current is not None:
            if current_hunk is not None:
                current["hunks"].append(current_hunk)
            files.append(current)
        current = None
        current_hunk = None

    for line in stdout.splitlines():
        if line.startswith("diff --git "):
            _push_current()
            # Format: diff --git a/<old> b/<new>
            # Quotes appear if the path has spaces; we don't try to
            # parse those exhaustively — best-effort split on " b/".
            paths_part = line[len("diff --git "):]
            old_p, _, new_p = paths_part.partition(" b/")
            old_path = old_p[2:] if old_p.startswith("a/") else old_p
            new_path = new_p
            current = {
                "old_path":       old_path,
                "new_path":       new_path,
                "status":         "modified",
                "is_binary":      False,
                "additions":      0,
                "deletions":      0,
                "hunks":          [],
                "body_truncated": False,
            }
            current_hunk = None
            body_lines_kept = 0
            continue

        if current is None:
            # Defensive — output before any diff header. Skip.
            continue

        if line.startswith("new file mode "):
            current["status"] = "added"
            current["old_path"] = ""
            continue
        if line.startswith("deleted file mode "):
            current["status"] = "deleted"
            current["new_path"] = ""
            continue
        if line.startswith("rename from "):
            current["status"] = "renamed"
            continue
        if line.startswith("copy from "):
            current["status"] = "copied"
            continue
        if line.startswith("Binary files "):
            current["is_binary"] = True
            continue
        if line.startswith("---") or line.startswith("+++"):
            # Header — already captured paths from diff --git.
            continue
        if line.startswith("index "):
            continue

        m = _HUNK_HEADER_RE.match(line)
        if m:
            # Push the prior hunk (if any) to the current file.
            if current_hunk is not None:
                current["hunks"].append(current_hunk)
            old_start = int(m.group(1))
            old_count = int(m.group(2)) if m.group(2) else 1
            new_start = int(m.group(3))
            new_count = int(m.group(4)) if m.group(4) else 1
            ctx_text = (m.group(5) or "").rstrip()
            current_hunk = {
                "old_start": old_start,
                "old_count": old_count,
                "new_start": new_start,
                "new_count": new_count,
                "header":    ctx_text,
                "body":      "",
            }
            continue

        # Hunk body line.
        if current_hunk is not None:
            if body_lines_kept < max_lines_per_file:
                if current_hunk["body"]:
                    current_hunk["body"] += "\n"
                current_hunk["body"] += line
                body_lines_kept += 1
            else:
                current["body_truncated"] = True

    _push_current()

    # Apply numstat overlay so additions/deletions are accurate
    # (parsing the patch counts +/- lines but we'd duplicate work).
    for f in files:
        key = f["new_path"] if f["status"] != "deleted" else f["old_path"]
        if key in numstat_map:
            adds, dels = numstat_map[key]
            f["additions"] = adds
            f["deletions"] = dels
            if adds == -1 and dels == -1:
                f["is_binary"] = True

    return files


def _resolve_allowlist(paths: Any) -> tuple[Path, ...]:
    """Resolve every entry in the allowlist to an absolute, symlink-free Path.
    Mirrors the helper in code_read.v1 / ruff_lint.v1 / pytest_run.v1 /
    git_log_read.v1.
    """
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
    one root."""
    for root in roots:
        try:
            if target == root or target.is_relative_to(root):
                return True
        except (ValueError, AttributeError):
            continue
    return False


__all__ = [
    "GitDiffReadTool",
    "GitDiffReadError",
    "GitNotInstalledError",
    "NotAGitRepoError",
    "DEFAULT_MAX_FILES",
    "GIT_DIFF_MAX_FILES_HARD_CAP",
    "DEFAULT_MAX_LINES_PER_FILE",
    "GIT_DIFF_MAX_LINES_HARD_CAP",
    "DEFAULT_TIMEOUT_SECONDS",
    "VALID_MODES",
]
