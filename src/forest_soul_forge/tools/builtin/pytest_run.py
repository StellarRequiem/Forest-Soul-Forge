"""``pytest_run.v1`` — run pytest against the agent's allowed paths.

Side effects: filesystem. Pytest writes a ``.pytest_cache/`` dir +
test fixtures can perform their own filesystem mutation. Required
initiative L4 (reversible-with-policy class per ADR-0021-amendment §5).

This is the second Phase G.1.A programming primitive (Burst 54),
after ``ruff_lint.v1``. Gives SW-track Engineer (Actuator L5/L5)
the ability to run tests on changed code; Reviewer (Guardian L3)
also reaches L4 when birthed at ceiling.

Per-agent constitution must populate:
  allowed_paths: [/abs/path/to/repo, /another/abs/path, ...]

Path discipline mirrors ``code_read.v1`` and ``ruff_lint.v1``:
  - Resolve to absolute symlink-free form before checking allowlist
  - Defends against ../ escape, symlink escape, case-collision tricks

Pytest invocation strategy:
  - Subprocess ``python3 -m pytest <path> --json-report
    --json-report-file=- --no-header -q``
  - Falls back to ``--tb=line`` formatting when pytest-json-report
    isn't installed (every install has ``--tb=line``)
  - 300-second default timeout (test suites take time); operator-
    tunable up to 1800s (30 min)
  - Respects pyproject.toml ``[tool.pytest.ini_options]`` if present
    in the path tree; testpaths, addopts, etc. inherit
  - Test selection via the standard pytest selector syntax (operator-
    supplied as ``selectors`` arg list)

Output strategy:
  - Primary: parse pytest's terminal output for pass/fail/skip
    counts (regex against the summary line)
  - Per-failure: capture the FAILED test ID + last 50 lines of
    its traceback (capped to prevent context blow-up)
  - Optional ``raw_stdout`` / ``raw_stderr`` for forensic value
    when the operator needs the full picture

Future evolution:
  - v2: pytest-json-report integration when installed (richer
        per-test data: durations, parametrize ids, etc.)
  - v2: coverage.py integration (run with --cov, parse output)
  - v2: parallel runs via pytest-xdist when installed
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


DEFAULT_TIMEOUT_SECONDS = 300
PYTEST_TIMEOUT_HARD_CAP = 1800
PYTEST_TIMEOUT_MIN = 1
DEFAULT_MAX_FAILURE_LINES = 50
MAX_FAILURE_LINES_CAP = 500
DEFAULT_MAX_FAILURES_REPORTED = 50


class PytestRunError(ToolValidationError):
    """Raised by pytest_run for path-allowlist or invocation failures.
    Subclasses ToolValidationError so the dispatcher routes it through
    the same path as bad_args."""


class PytestNotInstalledError(PytestRunError):
    """Raised when pytest is not invokable. Operators install via
    `pip install pytest`. Refusal is graceful — actionable error
    rather than cryptic subprocess failure."""


class PytestRunTool:
    """Args:
      path (str, required): absolute or relative path to test (file
        or directory). pytest handles both.
      selectors (list[str], optional): pytest-style selectors
        ("test_module.py::test_name", "-k expression", "-m mark", etc.).
        Each entry passed verbatim as an argv element; no shell parsing.
      timeout_seconds (int, optional): subprocess timeout. Default 300
        (5 min). Max 1800 (30 min).
      max_failures_reported (int, optional): cap on number of failures
        with traceback details. Default 50. Beyond this, count surfaces
        in summary but tracebacks truncated.
      max_failure_lines (int, optional): cap on lines per failure
        traceback. Default 50, max 500.

    Output:
      {
        "path":           str,    # the resolved absolute path scanned
        "passed":         int,
        "failed":         int,
        "skipped":        int,
        "errors":         int,    # collection errors (different from failures)
        "warnings":       int,
        "duration_s":     float,
        "exit_code":      int,    # pytest's exit code
        "summary_line":   str,    # the "X passed, Y failed in Zs" terminal line
        "failures": [
          {
            "test_id":     str,   # e.g. tests/unit/test_foo.py::test_bar
            "traceback":   list[str],   # capped lines
            "truncated":   bool,
          },
          ...
        ],
        "failures_truncated": bool,    # true if more failures than reported
      }

    Constraints (read from ctx.constraints):
      allowed_paths: list[str]   # required, absolute paths
    """

    name = "pytest_run"
    version = "1"
    side_effects = "filesystem"
    # ADR-0021-amendment §5 — pytest_run is reversible-with-policy class
    # (test runs write .pytest_cache, may write coverage data, etc., all
    # under the agent's allowed_paths). Required L4. Engineer (Actuator
    # default L5) reaches; Researcher / Companion don't autonomously
    # reach without ceiling-bumping.
    required_initiative_level = "L4"

    def validate(self, args: dict[str, Any]) -> None:
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ToolValidationError(
                "path is required and must be a non-empty string"
            )
        selectors = args.get("selectors")
        if selectors is not None:
            if not isinstance(selectors, list) or not all(
                isinstance(s, str) for s in selectors
            ):
                raise ToolValidationError(
                    "selectors must be a list of strings when provided"
                )
        timeout = args.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        if (
            not isinstance(timeout, int)
            or timeout < PYTEST_TIMEOUT_MIN
            or timeout > PYTEST_TIMEOUT_HARD_CAP
        ):
            raise ToolValidationError(
                f"timeout_seconds must be in [{PYTEST_TIMEOUT_MIN}, "
                f"{PYTEST_TIMEOUT_HARD_CAP}]; got {timeout!r}"
            )
        max_failures = args.get(
            "max_failures_reported", DEFAULT_MAX_FAILURES_REPORTED
        )
        if not isinstance(max_failures, int) or max_failures < 0 or max_failures > 1000:
            raise ToolValidationError(
                f"max_failures_reported must be in [0, 1000]; got {max_failures!r}"
            )
        max_lines = args.get("max_failure_lines", DEFAULT_MAX_FAILURE_LINES)
        if (
            not isinstance(max_lines, int)
            or max_lines < 1
            or max_lines > MAX_FAILURE_LINES_CAP
        ):
            raise ToolValidationError(
                f"max_failure_lines must be in [1, {MAX_FAILURE_LINES_CAP}]; "
                f"got {max_lines!r}"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        raw_path: str = args["path"]
        selectors = list(args.get("selectors") or [])
        timeout_seconds = int(args.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        max_failures = int(
            args.get("max_failures_reported", DEFAULT_MAX_FAILURES_REPORTED)
        )
        max_lines = int(args.get("max_failure_lines", DEFAULT_MAX_FAILURE_LINES))

        allowed_paths_raw = ctx.constraints.get("allowed_paths") or ()
        if not allowed_paths_raw:
            raise PytestRunError(
                "agent has no allowed_paths in its constitution — "
                "pytest_run.v1 refuses to touch the filesystem"
            )

        allowed_roots = tuple(_resolve_allowlist(allowed_paths_raw))

        # Resolve target. Either file or directory; pytest handles both.
        try:
            target = Path(raw_path).resolve(strict=True)
        except FileNotFoundError:
            raise PytestRunError(f"path does not exist: {raw_path!r}")
        except OSError as e:
            raise PytestRunError(f"path resolution failed: {e}") from e

        if not _is_within_any(target, allowed_roots):
            raise PytestRunError(
                f"path {str(target)!r} is outside the agent's allowed_paths "
                f"({[str(p) for p in allowed_roots]})"
            )

        # Locate pytest. Prefer python3 -m pytest (works in venv'd
        # environments where pytest entry-point may not be on PATH).
        # Direct `pytest` command also works.
        pytest_invocation = _locate_pytest()
        if pytest_invocation is None:
            raise PytestNotInstalledError(
                "pytest is not installed (not on PATH and not invokable as "
                "`python3 -m pytest`). Install via `pip install pytest`."
            )

        # Build argv. -q quiet mode + --tb=line short tracebacks +
        # --no-header to reduce parsing surface. Selectors append after.
        argv = list(pytest_invocation) + [
            str(target),
            "-q",
            "--tb=line",
            "--no-header",
        ] + selectors

        try:
            proc = subprocess.run(
                argv,
                timeout=timeout_seconds,
                capture_output=True,
                text=True,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise PytestRunError(
                f"pytest timed out after {timeout_seconds}s on {target}; "
                f"increase timeout_seconds or scope selectors narrower"
            ) from e
        except FileNotFoundError as e:
            raise PytestNotInstalledError(
                f"pytest invocation failed at exec time: {e}"
            ) from e

        # Pytest exit codes:
        #  0 = all tests passed
        #  1 = some tests failed
        #  2 = test execution interrupted by user
        #  3 = internal error
        #  4 = pytest command line usage error
        #  5 = no tests collected
        # We accept 0, 1, 5 as "ran cleanly"; 2/3/4 are refusals.
        if proc.returncode in (2, 3, 4):
            raise PytestRunError(
                f"pytest exited with code {proc.returncode}: "
                f"{(proc.stderr or proc.stdout or '').strip()[:500]}"
            )

        parsed = _parse_pytest_output(
            proc.stdout, proc.stderr,
            max_failures=max_failures,
            max_lines=max_lines,
        )

        return ToolResult(
            output={
                "path":               str(target),
                "passed":             parsed["passed"],
                "failed":             parsed["failed"],
                "skipped":            parsed["skipped"],
                "errors":             parsed["errors"],
                "warnings":           parsed["warnings"],
                "duration_s":         parsed["duration_s"],
                "exit_code":          proc.returncode,
                "summary_line":       parsed["summary_line"],
                "failures":           parsed["failures"],
                "failures_truncated": parsed["failures_truncated"],
            },
            metadata={
                "allowed_roots":      [str(p) for p in allowed_roots],
                "selectors":          selectors,
                "max_failures":       max_failures,
                "max_lines":          max_lines,
                "pytest_invocation":  list(pytest_invocation),
            },
            side_effect_summary=(
                f"pytest_run: {parsed['passed']}p/{parsed['failed']}f/"
                f"{parsed['skipped']}s/{parsed['errors']}e on {target.name} "
                f"(exit={proc.returncode})"
            ),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _locate_pytest() -> tuple[str, ...] | None:
    """Find a working pytest invocation.

    Prefers `python3 -m pytest` (venv-friendly; same Python that imports
    test modules). Falls back to `pytest` on PATH.
    """
    # Try `python3 -m pytest --version` first — works in any venv that
    # has pytest installed regardless of whether the entry-point script
    # made it to PATH.
    try:
        proc = subprocess.run(
            ["python3", "-m", "pytest", "--version"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            return ("python3", "-m", "pytest")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    # Fall back to PATH lookup.
    if shutil.which("pytest"):
        return ("pytest",)
    return None


# Pytest's terminal summary lines look like:
#   ============= 5 passed in 0.42s =============
#   ============= 1 failed, 4 passed in 0.50s =============
#   ============= 2 failed, 1 passed, 1 skipped in 0.31s =============
#   ============= no tests ran in 0.01s =============
#   ============= 1 error in 0.10s =============
# We use a regex to extract the counts from this line. The line is
# typically the LAST non-empty line of stdout.
_SUMMARY_TOKEN_RE = re.compile(
    r"(\d+)\s+(passed|failed|skipped|error|errors|warning|warnings|deselected|xpassed|xfailed)"
)
_DURATION_RE = re.compile(r"in\s+([\d.]+)s")
# FAILED test_id lines from pytest -q --tb=line format:
#   FAILED tests/unit/test_x.py::test_foo - assert 1 == 2
_FAILED_LINE_RE = re.compile(
    r"^FAILED\s+(?P<test_id>\S+)(?:\s+-\s+(?P<short>.+))?$"
)


def _parse_pytest_output(
    stdout: str, stderr: str, *, max_failures: int, max_lines: int,
) -> dict[str, Any]:
    """Best-effort parse of pytest's terminal output.

    Returns a dict with keys matching the tool's output schema. Any field
    that can't be parsed defaults to 0 / [] / "" — a parse failure
    doesn't break the dispatch, just produces a less-informative result.
    """
    counts = {
        "passed": 0, "failed": 0, "skipped": 0,
        "errors": 0, "warnings": 0,
    }
    duration_s = 0.0
    summary_line = ""
    failures: list[dict[str, Any]] = []

    if stdout:
        # Find the summary line — typically the last non-empty line
        # surrounded by `=` characters.
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if not line:
                continue
            if "passed" in line or "failed" in line or "error" in line or "no tests ran" in line:
                summary_line = line
                break

        if summary_line:
            for match in _SUMMARY_TOKEN_RE.finditer(summary_line):
                count = int(match.group(1))
                kind = match.group(2)
                if kind == "passed":
                    counts["passed"] = count
                elif kind == "failed":
                    counts["failed"] = count
                elif kind == "skipped":
                    counts["skipped"] = count
                elif kind in ("error", "errors"):
                    counts["errors"] = count
                elif kind in ("warning", "warnings"):
                    counts["warnings"] = count
            d = _DURATION_RE.search(summary_line)
            if d:
                try:
                    duration_s = float(d.group(1))
                except ValueError:
                    pass

        # Extract per-failure details. The -q --tb=line format emits
        # one FAILED line per test with the assertion error inline.
        # We collect those + cap.
        for line in stdout.splitlines():
            stripped = line.strip()
            m = _FAILED_LINE_RE.match(stripped)
            if not m:
                continue
            tb_lines = []
            if m.group("short"):
                tb_lines.append(m.group("short"))
            failures.append({
                "test_id":   m.group("test_id"),
                "traceback": tb_lines[:max_lines],
                "truncated": len(tb_lines) > max_lines,
            })

    failures_truncated = len(failures) > max_failures
    failures = failures[:max_failures]

    return {
        "passed":             counts["passed"],
        "failed":             counts["failed"],
        "skipped":            counts["skipped"],
        "errors":             counts["errors"],
        "warnings":           counts["warnings"],
        "duration_s":         duration_s,
        "summary_line":       summary_line,
        "failures":           failures,
        "failures_truncated": failures_truncated,
    }


def _resolve_allowlist(paths: Any) -> tuple[Path, ...]:
    """Mirrors code_read.v1 / ruff_lint.v1 helper. Resolve every entry
    in the allowlist to an absolute, symlink-free Path. Skips entries
    that don't exist (operator typos shouldn't crash the dispatch)."""
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
    """Mirrors code_read.v1 / ruff_lint.v1 helper. True iff target is
    the same as or a descendant of at least one root."""
    for root in roots:
        try:
            if target == root or target.is_relative_to(root):
                return True
        except (ValueError, AttributeError):
            continue
    return False


__all__ = [
    "PytestRunTool",
    "PytestRunError",
    "PytestNotInstalledError",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_MAX_FAILURES_REPORTED",
]
