"""``mypy_typecheck.v1`` — run mypy against allowed paths.

Side effects: read_only. Mypy reports type errors; never modifies
source files. We invoke with ``--no-incremental`` so no
``.mypy_cache`` is written to the agent's filesystem; this keeps
the read_only contract honest.

Sixth Phase G.1.A programming primitive (after ruff_lint.v1,
pytest_run.v1, git_log_read.v1, git_diff_read.v1, git_blame_read.v1).
Where ruff catches style + simple logic mistakes, mypy catches the
class of bugs that come from "I thought this was a string". For
SW-track Engineer + Reviewer, running mypy after a refactor is
a high-yield gate before pytest.

Per-agent constitution must populate:
  allowed_paths: [/abs/path/to/repo, ...]

Path discipline mirrors ``ruff_lint.v1`` / ``pytest_run.v1``:
  - Resolve to absolute symlink-free form before checking allowlist
  - File or directory both supported (mypy handles both)
  - is_relative_to defense against ../ escape

Mypy invocation strategy:
  - Subprocess ``python3 -m mypy <opts> <path>`` (preferred — venv-
    friendly), falling back to ``mypy`` on PATH
  - Output parsed from mypy's stable text format:
      <file>:<line>:<column>: <severity>: <message>  [<code>]
  - 60-second default timeout (mypy is slower than ruff because
    it does whole-program type inference)

Output is capped at max_findings (default 500, ceiling 10000).

Future evolution:
  - v2: mypy's ``--output=json`` once the format stabilizes (newer
        mypy versions support it but parsing differs across versions
        which is why v1 sticks to the text format)
  - v2: per-rule ignore via ``--disable-error-code``
  - v2: ``--follow-imports=silent`` toggle (currently mypy's default
        ``normal`` is used, which can drag in dependencies)
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
DEFAULT_MAX_FINDINGS = 500
MYPY_MAX_FINDINGS_HARD_CAP = 10_000
DEFAULT_TIMEOUT_SECONDS = 60
MYPY_TIMEOUT_HARD_CAP = 300
MYPY_TIMEOUT_MIN = 1

# Mypy's text format. Examples:
#   src/foo.py:42:7: error: Argument 1 to "f" has incompatible type ...  [arg-type]
#   src/foo.py:42: note: Use Optional[X] for ...
# Without column when --show-column-numbers is off; we always pass it
# so the column group should match. Use a tolerant regex that accepts
# missing column.
_MYPY_LINE_RE = re.compile(
    r"^(?P<file>[^:]+):(?P<line>\d+)(?::(?P<column>\d+))?:\s*"
    r"(?P<severity>error|warning|note):\s*"
    r"(?P<message>.*?)"
    r"(?:\s+\[(?P<code>[a-zA-Z0-9_-]+)\])?\s*$"
)


class MypyTypecheckError(ToolValidationError):
    """Raised by mypy_typecheck for path-allowlist or invocation failures."""


class MypyNotInstalledError(MypyTypecheckError):
    """Raised when mypy is not installed (not on PATH and not invokable
    as a Python module)."""


class MypyTypecheckTool:
    """Args:
      path (str, required): absolute or relative path to typecheck.
        File or directory; mypy handles both.
      config_file (str, optional): path to mypy config file
        (``mypy.ini``, ``pyproject.toml``, etc.). Forwarded as
        ``--config-file=<value>``. Must resolve within allowed_paths.
      strict (bool, optional): forward ``--strict``. Default false.
      max_findings (int, optional): cap on findings returned.
        Default 500, max 10000.
      timeout_seconds (int, optional): subprocess timeout. Default 60,
        max 300.

    Output:
      {
        "path":           str,    # the resolved absolute path
        "findings_count": int,
        "truncated":      bool,
        "exit_code":      int,    # 0=clean, 1=errors found, 2=hard error
        "findings": [
          {
            "filename":   str,
            "line":       int,
            "column":     int,    # 0 when mypy didn't emit a column
            "severity":   str,    # error | warning | note
            "code":       str,    # e.g. "arg-type", "" when none
            "message":    str,
          }, ...
        ]
      }

    Constraints (read from ctx.constraints):
      allowed_paths: list[str]   # required, absolute paths
    """

    name = "mypy_typecheck"
    version = "1"
    side_effects = "read_only"
    # No required_initiative_level — read_only tools pass at any L.

    def validate(self, args: dict[str, Any]) -> None:
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ToolValidationError(
                "path is required and must be a non-empty string"
            )

        config_file = args.get("config_file")
        if config_file is not None and (
            not isinstance(config_file, str) or not config_file.strip()
        ):
            raise ToolValidationError(
                "config_file must be a non-empty string when provided"
            )

        strict = args.get("strict")
        if strict is not None and not isinstance(strict, bool):
            raise ToolValidationError(
                f"strict must be a bool when provided; got {type(strict).__name__}"
            )

        max_findings = args.get("max_findings", DEFAULT_MAX_FINDINGS)
        if (
            not isinstance(max_findings, int)
            or max_findings < 1
            or max_findings > MYPY_MAX_FINDINGS_HARD_CAP
        ):
            raise ToolValidationError(
                f"max_findings must be a positive int <= "
                f"{MYPY_MAX_FINDINGS_HARD_CAP}; got {max_findings!r}"
            )

        timeout = args.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        if (
            not isinstance(timeout, int)
            or timeout < MYPY_TIMEOUT_MIN
            or timeout > MYPY_TIMEOUT_HARD_CAP
        ):
            raise ToolValidationError(
                f"timeout_seconds must be in [{MYPY_TIMEOUT_MIN}, "
                f"{MYPY_TIMEOUT_HARD_CAP}]; got {timeout!r}"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        raw_path: str = args["path"]
        config_file = args.get("config_file")
        strict = bool(args.get("strict", False))
        max_findings = int(args.get("max_findings", DEFAULT_MAX_FINDINGS))
        timeout_seconds = int(
            args.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        )

        allowed_paths_raw = ctx.constraints.get("allowed_paths") or ()
        if not allowed_paths_raw:
            raise MypyTypecheckError(
                "agent has no allowed_paths in its constitution — "
                "mypy_typecheck.v1 refuses to touch the filesystem"
            )

        allowed_roots = tuple(_resolve_allowlist(allowed_paths_raw))

        try:
            target = Path(raw_path).resolve(strict=True)
        except FileNotFoundError:
            raise MypyTypecheckError(f"path does not exist: {raw_path!r}")
        except OSError as e:
            raise MypyTypecheckError(f"path resolution failed: {e}") from e

        if not _is_within_any(target, allowed_roots):
            raise MypyTypecheckError(
                f"path {str(target)!r} is outside the agent's "
                f"allowed_paths ({[str(p) for p in allowed_roots]})"
            )

        # Validate config_file path against allowlist if provided.
        resolved_config: str | None = None
        if config_file is not None:
            try:
                cfg = Path(config_file).resolve(strict=True)
            except FileNotFoundError:
                raise MypyTypecheckError(
                    f"config_file does not exist: {config_file!r}"
                )
            except OSError as e:
                raise MypyTypecheckError(
                    f"config_file resolution failed: {e}"
                ) from e
            if not _is_within_any(cfg, allowed_roots):
                raise MypyTypecheckError(
                    f"config_file {str(cfg)!r} is outside allowed_paths"
                )
            if not cfg.is_file():
                raise MypyTypecheckError(
                    f"config_file must be a regular file: {str(cfg)!r}"
                )
            resolved_config = str(cfg)

        invocation = _locate_mypy()
        if invocation is None:
            raise MypyNotInstalledError(
                "mypy is not installed (not on PATH and not invokable as "
                "`python3 -m mypy`). Install via `pip install mypy`."
            )

        argv = list(invocation) + [
            "--no-incremental",
            "--show-column-numbers",
            "--show-error-codes",
            "--no-error-summary",
            "--no-color-output",
        ]
        if strict:
            argv.append("--strict")
        if resolved_config is not None:
            argv.append(f"--config-file={resolved_config}")
        argv.append(str(target))

        try:
            proc = subprocess.run(
                argv,
                timeout=timeout_seconds,
                capture_output=True,
                text=True,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise MypyTypecheckError(
                f"mypy timed out after {timeout_seconds}s on {target}; "
                f"increase timeout_seconds or scope the path narrower"
            ) from e
        except FileNotFoundError as e:
            raise MypyNotInstalledError(
                f"mypy invocation failed at exec time: {e}"
            ) from e

        # Mypy exit codes:
        #   0 = no errors
        #   1 = errors found
        #   2 = command-line / config error
        # We treat 0 and 1 as "ran cleanly"; 2 is a refusal.
        if proc.returncode not in (0, 1):
            raise MypyTypecheckError(
                f"mypy exited with code {proc.returncode}: "
                f"{(proc.stderr or proc.stdout or '').strip()[:500]}"
            )

        all_findings = _parse_mypy_output(proc.stdout)

        actual_count = len(all_findings)
        truncated = actual_count > max_findings
        kept = all_findings[:max_findings]

        return ToolResult(
            output={
                "path":           str(target),
                "findings_count": len(kept),
                "truncated":      truncated,
                "exit_code":      proc.returncode,
                "findings":       kept,
            },
            metadata={
                "allowed_roots":   [str(p) for p in allowed_roots],
                "actual_count":    actual_count,
                "max_findings":    max_findings,
                "mypy_invocation": list(invocation),
                "config_file":     resolved_config,
                "strict":          strict,
            },
            side_effect_summary=(
                f"mypy_typecheck: {len(kept)}/{actual_count} findings on "
                f"{target.name} (exit={proc.returncode})"
            ),
        )


# ---------------------------------------------------------------------------
# Helpers — module-level for testability
# ---------------------------------------------------------------------------
def _locate_mypy() -> tuple[str, ...] | None:
    """Find a working mypy invocation. Tries `python3 -m mypy` first
    (venv-friendly), falls back to `mypy` on PATH.

    Why the order is reversed from ruff: mypy is pure Python and the
    Rust-binary perf argument doesn't apply, but module invocation
    is more reliable when running inside a venv that has mypy
    installed but PATH not yet activated.
    """
    try:
        proc = subprocess.run(
            ["python3", "-m", "mypy", "--version"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            return ("python3", "-m", "mypy")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    if shutil.which("mypy"):
        return ("mypy",)
    return None


def _parse_mypy_output(stdout: str) -> list[dict[str, Any]]:
    """Parse mypy's text output into structured findings.

    Each finding line looks like:
        <file>:<line>:<column>: <severity>: <message>  [<code>]
    The column is optional (when --show-column-numbers isn't honored
    by older mypy versions). The error code in brackets is also
    optional (notes don't have one).
    """
    findings: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        line = line.rstrip()
        if not line:
            continue
        m = _MYPY_LINE_RE.match(line)
        if m is None:
            # Skip non-findings: mypy emits success summaries, blank
            # lines, and the occasional "Found N errors" line that
            # we suppressed with --no-error-summary but might still
            # appear from older versions.
            continue
        col_raw = m.group("column")
        findings.append({
            "filename":  m.group("file"),
            "line":      int(m.group("line")),
            "column":    int(col_raw) if col_raw else 0,
            "severity":  m.group("severity"),
            "code":      m.group("code") or "",
            "message":   (m.group("message") or "").strip(),
        })
    return findings


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
    "MypyTypecheckTool",
    "MypyTypecheckError",
    "MypyNotInstalledError",
    "DEFAULT_MAX_FINDINGS",
    "MYPY_MAX_FINDINGS_HARD_CAP",
    "DEFAULT_TIMEOUT_SECONDS",
]
