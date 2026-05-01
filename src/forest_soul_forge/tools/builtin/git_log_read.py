"""``git_log_read.v1`` — read commit history via ``git log``.

Side effects: read_only. ``git log`` reads refs and commit objects;
nothing in the repo is mutated. Pure inspection primitive.

This is the third Phase G.1.A programming primitive (after
``ruff_lint.v1`` and ``pytest_run.v1``). The shape of "code work"
that SW-track agents need invariably starts with reading what the
codebase has already lived through. ``git log`` is the canonical
answer to "what changed, by whom, when, and why".

Per-agent constitution must populate:
  allowed_paths: [/abs/path/to/repo, ...]

The ``path`` argument identifies the repo whose history to read.
It must resolve into one of the agent's allowed roots. We invoke
git via ``git -C <path> log ...`` so cwd discipline is explicit
and doesn't depend on the dispatcher's working directory.

Path discipline mirrors ``ruff_lint.v1`` / ``pytest_run.v1``:
  - Resolve to absolute symlink-free form before checking allowlist
  - is_relative_to defense against ../ escape and case-collision
  - All paths must be within at least one allowed_paths root

Git invocation strategy:
  - Subprocess ``git -C <path> log <opts> --pretty=format:<delim-fmt>``
  - Output uses unit-separator (\\x1f) between fields and record-
    separator (\\x1e) between commits — robust against any commit-
    message content (newlines, tabs, quotes, control bytes)
  - 30-second timeout (history reads are normally <1s; pathological
    monorepos with millions of commits and a wide --since shouldn't
    hold the dispatch indefinitely)

Output is capped at max_count (default 50, hard ceiling 500) to
prevent a runaway request from flooding the dispatch envelope.
``truncated`` flags when the underlying repo had more matching
commits than were returned.

Future evolution:
  - v2: include --stat / --shortstat for change-volume context
  - v2: --grep regex over commit messages
  - v2: per-commit file-list (currently only the commit metadata
        + paths_filter for filtering; not enumerating files-changed)
"""
from __future__ import annotations

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
DEFAULT_MAX_COUNT = 50
GIT_LOG_MAX_COUNT_HARD_CAP = 500
DEFAULT_TIMEOUT_SECONDS = 30
GIT_LOG_TIMEOUT_HARD_CAP = 120
GIT_LOG_TIMEOUT_MIN = 1

# Field/record separators for the --pretty=format: output. ASCII US
# (\x1f) and RS (\x1e) are control characters that don't appear in
# normal text — using them as delimiters is safe against any
# commit-message content.
_FS = "\x1f"
_RS = "\x1e"
_PRETTY_FORMAT = (
    f"%H{_FS}%an{_FS}%ae{_FS}%aI{_FS}%cI{_FS}%P{_FS}%s{_FS}%b{_RS}"
)
_FIELD_NAMES = (
    "sha",
    "author_name",
    "author_email",
    "author_date",
    "commit_date",
    "parents",
    "subject",
    "body",
)


class GitLogReadError(ToolValidationError):
    """Raised by git_log_read for path-allowlist or invocation failures.
    Subclasses ToolValidationError so the dispatcher routes it through
    the same path as bad_args."""


class GitNotInstalledError(GitLogReadError):
    """Raised when the ``git`` binary is not on PATH. Operators install
    via their OS package manager (``brew install git``, ``apt-get
    install git``, etc.). Refusal is graceful — the agent gets a clear
    actionable error rather than a cryptic subprocess failure."""


class NotAGitRepoError(GitLogReadError):
    """Raised when the resolved path is not inside a git repository.
    Distinct from path-allowlist refusal because it's a useful signal:
    the path was reachable but the repo just hasn't been initialized
    (or this is a worktree that lost its .git pointer)."""


class GitLogReadTool:
    """Args:
      path (str, required): absolute or relative path to a directory
        inside a git repo. We invoke ``git -C <path>`` so the path
        can be the repo root or any subdirectory.
      max_count (int, optional): cap on commits returned. Default 50,
        max 500. Beyond this the output is truncated and
        ``truncated=True`` flagged.
      ref (str, optional): branch/tag/commit to start the log from.
        Default is whatever ``git log`` defaults to (the current
        HEAD). Validated to reject argument-injection attempts.
      since (str, optional): forwarded as ``--since=<value>``. Git
        accepts approxidate strings ("2 weeks ago", "2026-01-01").
      until (str, optional): forwarded as ``--until=<value>``.
      author (str, optional): forwarded as ``--author=<value>``.
        Git's --author is a regex over the author field; use plain
        text for substring match.
      paths_filter (list[str], optional): trailing pathspec args.
        Each element is appended after ``--`` so git treats them
        as path filters, not refs. All entries must resolve within
        the agent's allowed_paths.
      timeout_seconds (int, optional): subprocess timeout. Default 30,
        max 120.

    Output:
      {
        "path":          str,    # the resolved repo working-dir path
        "ref":           str,    # the ref the log started from
        "commits_count": int,    # commits actually returned
        "truncated":     bool,   # true when more commits matched than returned
        "commits": [
          {
            "sha":          str,    # full 40-char hash
            "author_name":  str,
            "author_email": str,
            "author_date":  str,    # ISO 8601 strict (%aI)
            "commit_date":  str,    # ISO 8601 strict (%cI)
            "parents":      [str],  # parent SHAs (empty for root commit)
            "subject":      str,
            "body":         str,
          }, ...
        ]
      }

    Constraints (read from ctx.constraints):
      allowed_paths: list[str]   # required, absolute paths
    """

    name = "git_log_read"
    version = "1"
    side_effects = "read_only"
    # No required_initiative_level — read_only tools pass at any L per
    # ADR-0021-amendment §5. SW-track Architect (Observer L1+),
    # Engineer (Actuator L5), and Reviewer (Guardian L3) all reach.

    def validate(self, args: dict[str, Any]) -> None:
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ToolValidationError(
                "path is required and must be a non-empty string"
            )

        max_count = args.get("max_count", DEFAULT_MAX_COUNT)
        if (
            not isinstance(max_count, int)
            or max_count < 1
            or max_count > GIT_LOG_MAX_COUNT_HARD_CAP
        ):
            raise ToolValidationError(
                f"max_count must be a positive int <= "
                f"{GIT_LOG_MAX_COUNT_HARD_CAP}; got {max_count!r}"
            )

        ref = args.get("ref")
        if ref is not None:
            if not isinstance(ref, str) or not ref.strip():
                raise ToolValidationError(
                    "ref must be a non-empty string when provided"
                )
            _validate_ref_string(ref)

        for opt_name in ("since", "until", "author"):
            val = args.get(opt_name)
            if val is not None and (not isinstance(val, str) or not val.strip()):
                raise ToolValidationError(
                    f"{opt_name} must be a non-empty string when provided"
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
            or timeout < GIT_LOG_TIMEOUT_MIN
            or timeout > GIT_LOG_TIMEOUT_HARD_CAP
        ):
            raise ToolValidationError(
                f"timeout_seconds must be in [{GIT_LOG_TIMEOUT_MIN}, "
                f"{GIT_LOG_TIMEOUT_HARD_CAP}]; got {timeout!r}"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        raw_path: str = args["path"]
        max_count = int(args.get("max_count", DEFAULT_MAX_COUNT))
        ref = args.get("ref")
        since = args.get("since")
        until = args.get("until")
        author = args.get("author")
        paths_filter = args.get("paths_filter") or []
        timeout_seconds = int(
            args.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        )

        allowed_paths_raw = ctx.constraints.get("allowed_paths") or ()
        if not allowed_paths_raw:
            raise GitLogReadError(
                "agent has no allowed_paths in its constitution — "
                "git_log_read.v1 refuses to touch the filesystem"
            )

        allowed_roots = tuple(_resolve_allowlist(allowed_paths_raw))

        # Resolve the repo path. We accept either the repo root or a
        # subdirectory; ``git -C`` handles both. Existence and allowlist
        # containment are mandatory.
        try:
            target = Path(raw_path).resolve(strict=True)
        except FileNotFoundError:
            raise GitLogReadError(f"path does not exist: {raw_path!r}")
        except OSError as e:
            raise GitLogReadError(f"path resolution failed: {e}") from e

        if not target.is_dir():
            raise GitLogReadError(
                f"path must be a directory; got {str(target)!r}"
            )

        if not _is_within_any(target, allowed_roots):
            raise GitLogReadError(
                f"path {str(target)!r} is outside the agent's "
                f"allowed_paths ({[str(p) for p in allowed_roots]})"
            )

        # Resolve paths_filter entries against the repo root and verify
        # each lives in the agent's allowed roots. Each entry is then
        # passed to git as-is (relative to the repo working dir is fine
        # because we use ``-C target``).
        validated_paths: list[str] = []
        for raw in paths_filter:
            # Allow relative entries (treated relative to ``target``)
            # and absolute entries (must resolve within the allowlist).
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = target / candidate
            try:
                resolved = candidate.resolve(strict=False)
            except OSError as e:
                raise GitLogReadError(
                    f"paths_filter entry {raw!r} could not be resolved: {e}"
                ) from e
            if not _is_within_any(resolved, allowed_roots):
                raise GitLogReadError(
                    f"paths_filter entry {raw!r} resolves to "
                    f"{str(resolved)!r}, outside allowed_paths"
                )
            validated_paths.append(raw)

        # Locate git. Unlike ruff/pytest there's no Python-module
        # fallback; git is a system binary. If it's not on PATH we
        # refuse cleanly.
        git_bin = _locate_git()
        if git_bin is None:
            raise GitNotInstalledError(
                "git is not installed (not on PATH). Install via your "
                "OS package manager (`brew install git`, `apt-get "
                "install git`, etc.)."
            )

        # Build the argv. Order matters:
        #   git -C <repo> log [<flags>] [<ref>] [-- <paths>...]
        # We request one extra commit beyond max_count so we can
        # detect truncation cheaply (no need for a separate count
        # invocation).
        argv: list[str] = [git_bin, "-C", str(target), "log"]
        argv.extend(["--max-count", str(max_count + 1)])
        argv.extend(["--pretty=format:" + _PRETTY_FORMAT])
        if since is not None:
            argv.append(f"--since={since}")
        if until is not None:
            argv.append(f"--until={until}")
        if author is not None:
            argv.append(f"--author={author}")
        if ref is not None:
            argv.append(ref)
        if validated_paths:
            argv.append("--")
            argv.extend(validated_paths)

        try:
            proc = subprocess.run(
                argv,
                timeout=timeout_seconds,
                capture_output=True,
                text=True,
                check=False,
                # Avoid color escapes in the output even if the
                # operator's git config sets color=always.
                env={"GIT_TERMINAL_PROMPT": "0", "PATH": _safe_path_env()},
            )
        except subprocess.TimeoutExpired as e:
            raise GitLogReadError(
                f"git log timed out after {timeout_seconds}s on {target}; "
                f"narrow with --since / --max-count or scope the path"
            ) from e
        except FileNotFoundError as e:
            raise GitNotInstalledError(
                f"git invocation failed at exec time: {e}"
            ) from e

        if proc.returncode != 0:
            stderr_blurb = (proc.stderr or "").strip()
            if "not a git repository" in stderr_blurb.lower():
                raise NotAGitRepoError(
                    f"path {str(target)!r} is not inside a git repository"
                )
            raise GitLogReadError(
                f"git log exited with code {proc.returncode}: "
                f"{stderr_blurb[:500]}"
            )

        commits = _parse_log_output(proc.stdout)

        actual_count = len(commits)
        truncated = actual_count > max_count
        kept = commits[:max_count]

        return ToolResult(
            output={
                "path":          str(target),
                "ref":           ref or "HEAD",
                "commits_count": len(kept),
                "truncated":     truncated,
                "commits":       kept,
            },
            metadata={
                "allowed_roots":   [str(p) for p in allowed_roots],
                "actual_count":    actual_count,
                "max_count":       max_count,
                "git_bin":         git_bin,
                "paths_filter":    validated_paths,
            },
            side_effect_summary=(
                f"git_log_read: {len(kept)} commits from "
                f"{ref or 'HEAD'} on {target.name} "
                f"(truncated={truncated})"
            ),
        )


# ---------------------------------------------------------------------------
# Helpers — module-level for testability
# ---------------------------------------------------------------------------
def _locate_git() -> str | None:
    """Find a working git binary on PATH. Returns the absolute path
    or None if git isn't installed."""
    return shutil.which("git")


def _safe_path_env() -> str:
    """Return a PATH that retains the operator's PATH (so git can find
    its helpers like git-pack-objects). We don't strip anything here;
    the binary itself is located via shutil.which() outside this env."""
    import os
    return os.environ.get("PATH", "")


def _validate_ref_string(ref: str) -> None:
    """Reject ref strings that look like argument injection attempts.

    Git refs have a strict naming grammar (refs/heads/main,
    feature/foo, v1.2.3, abc1234). They never start with ``-`` (which
    would let an attacker smuggle a flag into argv as a positional
    argument). Reject any ref that starts with ``-``; everything else
    we let git itself validate (its own grammar is the canonical check).
    """
    if ref.startswith("-"):
        raise ToolValidationError(
            f"ref must not start with '-' (would be misinterpreted "
            f"as a flag): {ref!r}"
        )


def _parse_log_output(stdout: str) -> list[dict[str, Any]]:
    """Parse the delimited git log output into structured commits.

    Records are separated by ASCII RS (\\x1e); within a record fields
    are separated by ASCII US (\\x1f). Trailing whitespace from git's
    own newline-after-format is trimmed.
    """
    if not stdout.strip():
        return []
    out: list[dict[str, Any]] = []
    # Records may have leading/trailing whitespace from format quirks.
    for raw_record in stdout.split(_RS):
        record = raw_record.strip("\n\r ")
        if not record:
            continue
        fields = record.split(_FS)
        # Pad short records (defensive — git sometimes emits with
        # missing trailing fields if %b is empty for example).
        while len(fields) < len(_FIELD_NAMES):
            fields.append("")
        commit: dict[str, Any] = dict(zip(_FIELD_NAMES, fields[: len(_FIELD_NAMES)]))
        # Body sometimes has trailing newlines from git's formatting.
        commit["body"] = commit["body"].rstrip("\n\r")
        # Parents come back as space-separated SHAs (or empty for root).
        parents_raw = commit["parents"].strip()
        commit["parents"] = parents_raw.split() if parents_raw else []
        out.append(commit)
    return out


def _resolve_allowlist(paths: Any) -> tuple[Path, ...]:
    """Resolve every entry in the allowlist to an absolute, symlink-free
    Path. Skips entries that don't exist (operator typos shouldn't
    crash the dispatch — they should be visible as 'not allowed' later).

    Mirrors the helper in code_read.v1 / ruff_lint.v1 / pytest_run.v1.
    Will live here until a refactor extracts a shared
    ``tools/_path_allowlist.py`` module.
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
    one root. Mirrors code_read.v1 / ruff_lint.v1 / pytest_run.v1."""
    for root in roots:
        try:
            if target == root or target.is_relative_to(root):
                return True
        except (ValueError, AttributeError):
            continue
    return False


__all__ = [
    "GitLogReadTool",
    "GitLogReadError",
    "GitNotInstalledError",
    "NotAGitRepoError",
    "DEFAULT_MAX_COUNT",
    "GIT_LOG_MAX_COUNT_HARD_CAP",
    "DEFAULT_TIMEOUT_SECONDS",
]
