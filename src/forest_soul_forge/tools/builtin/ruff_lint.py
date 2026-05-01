"""``ruff_lint.v1`` — run the ruff linter against allowed paths.

Side effects: read_only. Lint reports findings; never modifies files.
``ruff check --output-format json`` is the canonical invocation.
``ruff format --check`` is OUT of scope here — formatting is a
separate concern handled by a future ``ruff_format.v1`` tool. We
deliberately don't enable ``--fix`` so the tool stays honest about
its read_only contract.

This is the first of the Phase G.1.A programming-primitive batch
per `docs/roadmap/2026-04-30-v0.2-to-v1.0-roadmap.md` §3 G.1.A.

Per-agent constitution must populate:
  allowed_paths: [/abs/path/to/repo, /another/abs/path, ...]

Path discipline mirrors `code_read.v1`:
  - Resolve to absolute symlink-free form before checking allowlist
  - Defends against ../ escape, symlink escape, case-collision tricks
  - is_relative_to checks lexicographically after resolve()

Ruff invocation strategy:
  - Subprocess `python3 -m ruff check --output-format json <path>`
  - Avoids importing ruff in-process (ruff is a Rust binary; the
    Python wrapper is a thin CLI shim, so subprocess is the
    canonical invocation pattern even for downstream consumers)
  - 30-second timeout (linting a typical project takes 1-3s; a
    pathological case shouldn't hold the dispatch)
  - JSON output is parsed back into a structured list

Output is capped at max_findings (default 500) to prevent pathological
output from breaking the dispatch.

Future evolution:
  - v2: include ruff config file selection (currently inherits the
        repo's pyproject.toml [tool.ruff] section)
  - v2: per-rule selection (--select / --ignore overrides)
  - v2: companion ruff_format.v1 (separate side_effects=filesystem)
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


DEFAULT_MAX_FINDINGS = 500
DEFAULT_TIMEOUT_SECONDS = 30
RUFF_TIMEOUT_HARD_CAP = 120     # 2 minutes; even pathological monorepos shouldn't exceed
RUFF_TIMEOUT_MIN = 1


class RuffLintError(ToolValidationError):
    """Raised by ruff_lint for path-allowlist or invocation failures.
    Subclasses ToolValidationError so the dispatcher routes it through
    the same path as bad_args."""


class RuffNotInstalledError(RuffLintError):
    """Raised when ruff is not on PATH and not invokable as a Python
    module. Operators install via `pip install ruff` (project may
    add as a dev extra). Refusal is graceful — the agent gets a
    clear actionable error rather than a cryptic subprocess failure."""


class RuffLintTool:
    """Args:
      path (str, required): absolute or relative path to lint.
        Can be a file or a directory; ruff handles both.
      max_findings (int, optional): cap on findings returned.
        Default 500. Beyond this the output is truncated and
        ``truncated=True`` flagged.
      timeout_seconds (int, optional): subprocess timeout. Default 30,
        max 120. A timeout produces a refusal (not a partial result),
        per the dispatcher's all-or-nothing contract.

    Output:
      {
        "path":           str,    # the resolved absolute path scanned
        "findings_count": int,    # total findings returned
        "truncated":      bool,   # true when actual count exceeded max_findings
        "exit_code":      int,    # ruff's exit code (0=clean, 1=findings, 2=error)
        "findings": [
          {
            "filename":     str,
            "line":         int,
            "column":       int,
            "rule_code":    str,
            "rule_name":    str,
            "message":      str,
            "severity":     str,   # ruff doesn't expose; we map fix_applicability
            "fixable":      bool,
          },
          ...
        ]
      }

    Constraints (read from ctx.constraints):
      allowed_paths: list[str]   # required, absolute paths
    """

    name = "ruff_lint"
    version = "1"
    side_effects = "read_only"
    # No required_initiative_level — read_only tools pass at any L per
    # ADR-0021-amendment §5. SW-track Reviewer (Guardian-genre L3) and
    # Engineer (Actuator L5) both reach.

    def validate(self, args: dict[str, Any]) -> None:
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ToolValidationError(
                "path is required and must be a non-empty string"
            )
        max_findings = args.get("max_findings", DEFAULT_MAX_FINDINGS)
        if not isinstance(max_findings, int) or max_findings < 1 or max_findings > 100_000:
            raise ToolValidationError(
                f"max_findings must be a positive int <= 100000; got {max_findings!r}"
            )
        timeout = args.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        if not isinstance(timeout, int) or timeout < RUFF_TIMEOUT_MIN or timeout > RUFF_TIMEOUT_HARD_CAP:
            raise ToolValidationError(
                f"timeout_seconds must be in [{RUFF_TIMEOUT_MIN}, "
                f"{RUFF_TIMEOUT_HARD_CAP}]; got {timeout!r}"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        raw_path: str = args["path"]
        max_findings = int(args.get("max_findings", DEFAULT_MAX_FINDINGS))
        timeout_seconds = int(args.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))

        allowed_paths_raw = ctx.constraints.get("allowed_paths") or ()
        if not allowed_paths_raw:
            raise RuffLintError(
                "agent has no allowed_paths in its constitution — "
                "ruff_lint.v1 refuses to touch the filesystem"
            )

        allowed_roots = tuple(_resolve_allowlist(allowed_paths_raw))

        # Resolve target. Linting can target either a file or a directory;
        # ruff handles both. We resolve, check existence, and verify
        # containment in the allowlist.
        try:
            target = Path(raw_path).resolve(strict=True)
        except FileNotFoundError:
            raise RuffLintError(f"path does not exist: {raw_path!r}")
        except OSError as e:
            raise RuffLintError(f"path resolution failed: {e}") from e

        if not _is_within_any(target, allowed_roots):
            raise RuffLintError(
                f"path {str(target)!r} is outside the agent's allowed_paths "
                f"({[str(p) for p in allowed_roots]})"
            )

        # Locate ruff. We prefer `ruff` on PATH (Rust binary direct);
        # fall back to `python3 -m ruff` (Python entry-point shim).
        # Both ultimately invoke the same Rust binary, so the
        # invocation choice is just about reachability.
        ruff_invocation = _locate_ruff()
        if ruff_invocation is None:
            raise RuffNotInstalledError(
                "ruff is not installed (not on PATH and not invokable as "
                "`python3 -m ruff`). Install via `pip install ruff` or add "
                "to the project's dev extras."
            )

        # Build the subprocess argv. --output-format json gets a
        # machine-parseable list of diagnostics. `--no-cache` prevents
        # ruff from writing into the agent's working directory; this
        # tool is read_only and a cache file would violate that.
        argv = list(ruff_invocation) + [
            "check",
            "--output-format", "json",
            "--no-cache",
            str(target),
        ]

        try:
            proc = subprocess.run(
                argv,
                timeout=timeout_seconds,
                capture_output=True,
                text=True,
                check=False,    # ruff exits 1 when findings exist; that's success
            )
        except subprocess.TimeoutExpired as e:
            raise RuffLintError(
                f"ruff timed out after {timeout_seconds}s on {target}; "
                f"increase timeout_seconds or scope the path narrower"
            ) from e
        except FileNotFoundError as e:
            # ruff_invocation pointed somewhere; if the file disappears
            # between locate and exec (rare race) we surface cleanly.
            raise RuffNotInstalledError(
                f"ruff invocation failed at exec time: {e}"
            ) from e

        # Parse JSON output. Ruff emits JSON to stdout even on findings
        # (exit 1). On hard errors (exit 2) it emits diagnostic text to
        # stderr; we surface that as a refusal because the lint result
        # is meaningless when ruff itself failed to run.
        if proc.returncode not in (0, 1):
            raise RuffLintError(
                f"ruff exited with code {proc.returncode}: "
                f"{(proc.stderr or proc.stdout or '').strip()[:500]}"
            )

        try:
            raw_findings = json.loads(proc.stdout) if proc.stdout.strip() else []
        except json.JSONDecodeError as e:
            raise RuffLintError(
                f"ruff produced unparseable JSON output: {e}; first 200 chars: "
                f"{proc.stdout[:200]!r}"
            ) from e
        if not isinstance(raw_findings, list):
            raise RuffLintError(
                f"ruff JSON output is not a list; got {type(raw_findings).__name__}"
            )

        actual_count = len(raw_findings)
        truncated = actual_count > max_findings
        kept = raw_findings[:max_findings]

        findings_out = [_normalize_finding(f) for f in kept]

        return ToolResult(
            output={
                "path":           str(target),
                "findings_count": len(findings_out),
                "truncated":      truncated,
                "exit_code":      proc.returncode,
                "findings":       findings_out,
            },
            metadata={
                "allowed_roots":  [str(p) for p in allowed_roots],
                "actual_count":   actual_count,
                "max_findings":   max_findings,
                "ruff_invocation": list(ruff_invocation),
            },
            side_effect_summary=(
                f"ruff_lint: {len(findings_out)}/{actual_count} findings on "
                f"{target.name} (exit={proc.returncode})"
            ),
        )


# ---------------------------------------------------------------------------
# Helpers — module-level for testability
# ---------------------------------------------------------------------------
def _locate_ruff() -> tuple[str, ...] | None:
    """Find a working ruff invocation. Tries `ruff` on PATH first, then
    `python3 -m ruff`. Returns the argv prefix tuple, or None if neither
    works.

    Why prefer `ruff` on PATH: the Rust binary direct invocation skips
    Python startup (~80ms saved on lint runs that hit the no-findings
    fast path).
    """
    if shutil.which("ruff"):
        return ("ruff",)
    # Fallback: invoke as Python module. This works whenever `pip install
    # ruff` succeeded but the entry-point script didn't end up on PATH
    # (common in venv'd environments where the agent's PATH may not
    # include the venv's bin/).
    try:
        proc = subprocess.run(
            ["python3", "-m", "ruff", "--version"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            return ("python3", "-m", "ruff")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def _normalize_finding(raw: dict[str, Any]) -> dict[str, Any]:
    """Map ruff's JSON finding shape to FSF's stable output schema.

    Ruff's JSON shape (as of ruff 0.x):
      {"code": "E501", "message": "...", "fix": {...}|null,
       "filename": "...", "location": {"row": int, "column": int},
       "end_location": {...}, "url": "..."}
    """
    location = raw.get("location") or {}
    fix = raw.get("fix")
    return {
        "filename":  raw.get("filename") or "",
        "line":      int(location.get("row") or 0),
        "column":    int(location.get("column") or 0),
        "rule_code": raw.get("code") or "",
        "rule_name": raw.get("name") or raw.get("code") or "",
        "message":   raw.get("message") or "",
        # Ruff doesn't carry severity per finding (everything's a
        # "violation"); we map fix-availability to a stable boolean.
        "severity":  "violation",
        "fixable":   bool(fix),
    }


def _resolve_allowlist(paths: Any) -> tuple[Path, ...]:
    """Resolve every entry in the allowlist to an absolute, symlink-free
    Path. Skips entries that don't exist (operator typos shouldn't
    crash the dispatch — they should be visible as 'not allowed' later).

    Mirrors the helper in code_read.v1 verbatim. Will live here until
    a refactor extracts a shared `tools/_path_allowlist.py` module.
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
    one root. Mirrors code_read.v1's helper."""
    for root in roots:
        try:
            if target == root or target.is_relative_to(root):
                return True
        except (ValueError, AttributeError):
            continue
    return False


__all__ = [
    "RuffLintTool",
    "RuffLintError",
    "RuffNotInstalledError",
    "DEFAULT_MAX_FINDINGS",
    "DEFAULT_TIMEOUT_SECONDS",
]
