"""``semgrep_scan.v1`` — security-focused static analysis via semgrep.

Side effects: read_only. Semgrep reports findings; never modifies
source files. We invoke with ``--no-rewrite-rule-ids --quiet`` and
parse JSON output directly.

Seventh Phase G.1.A programming primitive. Where ruff_lint catches
style + simple logic mistakes and mypy catches type errors,
semgrep catches the class of bugs that come from "this looks like
it could be exploited" — SQL injection patterns, unsafe deserialization,
hard-coded secrets, taint-propagation issues. SW-track Reviewer
(Guardian-genre L3) is the primary consumer.

Per-agent constitution must populate:
  allowed_paths: [/abs/path/to/repo, ...]

Path discipline mirrors ruff_lint.v1 / mypy_typecheck.v1:
  - Resolve to absolute symlink-free form before checking allowlist
  - File or directory both supported (semgrep handles both)
  - is_relative_to defense against ../ escape
  - config (ruleset) is validated against argument-injection but
    is NOT path-allowlisted because rulesets are typically registry
    refs ("p/python", "auto") rather than local paths. When the
    ruleset IS a local path, it must resolve within allowed_paths.

Argument-injection defense:
  - config string rejected if it starts with '-' (would smuggle
    flags into argv as positional)
  - severity_filter values constrained to {ERROR, WARNING, INFO}
    so callers can't pass shell-meaningful tokens
  - timeout enforced as separate kwarg, not embedded in argv

Truncation:
  - max_findings (default 500, ceiling 10000) caps entries returned
  - severity_filter narrows upstream of max_findings so a tight
    filter gives a precise read of high-severity issues
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
SEMGREP_MAX_FINDINGS_HARD_CAP = 10_000
DEFAULT_TIMEOUT_SECONDS = 60
SEMGREP_TIMEOUT_HARD_CAP = 600
SEMGREP_TIMEOUT_MIN = 1

VALID_SEVERITIES = ("ERROR", "WARNING", "INFO")


class SemgrepScanError(ToolValidationError):
    """Raised by semgrep_scan for path-allowlist or invocation failures."""


class SemgrepNotInstalledError(SemgrepScanError):
    """Raised when semgrep is not on PATH and not invokable as a Python module."""


class SemgrepScanTool:
    """Args:
      path (str, required): file or directory to scan.
      config (str, required): semgrep ruleset. Either a registry ref
        like "auto", "p/python", "p/security-audit", or a path to a
        yaml ruleset (must resolve within allowed_paths).
      max_findings (int, optional): cap on findings. Default 500,
        max 10000.
      severity_filter (list[str], optional): subset of
        {ERROR, WARNING, INFO}. Default: all severities.
      timeout_seconds (int, optional): subprocess timeout. Default
        60, max 600.

    Output:
      {
        "path":           str,
        "config":         str,
        "findings_count": int,
        "truncated":      bool,
        "exit_code":      int,    # 0=clean or findings, nonzero=hard error
        "findings": [
          {
            "rule_id":       str,
            "severity":      str,    # ERROR | WARNING | INFO
            "message":       str,
            "filename":      str,
            "start_line":    int,
            "end_line":      int,
            "start_column":  int,
            "end_column":    int,
            "code_snippet":  str,
          }, ...
        ]
      }

    Constraints (read from ctx.constraints):
      allowed_paths: list[str]   # required, absolute paths
    """

    name = "semgrep_scan"
    version = "1"
    side_effects = "read_only"
    # No required_initiative_level — read_only passes at any L.

    def validate(self, args: dict[str, Any]) -> None:
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ToolValidationError(
                "path is required and must be a non-empty string"
            )

        config = args.get("config")
        if not isinstance(config, str) or not config.strip():
            raise ToolValidationError(
                "config is required and must be a non-empty string "
                "(registry ref like 'auto'/'p/python' or a yaml file path)"
            )
        if config.startswith("-"):
            raise ToolValidationError(
                f"config must not start with '-' (would be misinterpreted "
                f"as a flag): {config!r}"
            )

        max_findings = args.get("max_findings", DEFAULT_MAX_FINDINGS)
        if (
            not isinstance(max_findings, int)
            or max_findings < 1
            or max_findings > SEMGREP_MAX_FINDINGS_HARD_CAP
        ):
            raise ToolValidationError(
                f"max_findings must be a positive int <= "
                f"{SEMGREP_MAX_FINDINGS_HARD_CAP}; got {max_findings!r}"
            )

        severity_filter = args.get("severity_filter")
        if severity_filter is not None:
            if not isinstance(severity_filter, list) or any(
                s not in VALID_SEVERITIES for s in severity_filter
            ):
                raise ToolValidationError(
                    f"severity_filter must be a list of "
                    f"{VALID_SEVERITIES} values; got {severity_filter!r}"
                )

        timeout = args.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        if (
            not isinstance(timeout, int)
            or timeout < SEMGREP_TIMEOUT_MIN
            or timeout > SEMGREP_TIMEOUT_HARD_CAP
        ):
            raise ToolValidationError(
                f"timeout_seconds must be in [{SEMGREP_TIMEOUT_MIN}, "
                f"{SEMGREP_TIMEOUT_HARD_CAP}]; got {timeout!r}"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        raw_path: str = args["path"]
        config: str = args["config"]
        max_findings = int(args.get("max_findings", DEFAULT_MAX_FINDINGS))
        severity_filter = args.get("severity_filter")
        timeout_seconds = int(
            args.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        )

        allowed_paths_raw = ctx.constraints.get("allowed_paths") or ()
        if not allowed_paths_raw:
            raise SemgrepScanError(
                "agent has no allowed_paths in its constitution — "
                "semgrep_scan.v1 refuses to touch the filesystem"
            )

        allowed_roots = tuple(_resolve_allowlist(allowed_paths_raw))

        try:
            target = Path(raw_path).resolve(strict=True)
        except FileNotFoundError:
            raise SemgrepScanError(f"path does not exist: {raw_path!r}")
        except OSError as e:
            raise SemgrepScanError(f"path resolution failed: {e}") from e

        if not _is_within_any(target, allowed_roots):
            raise SemgrepScanError(
                f"path {str(target)!r} is outside the agent's "
                f"allowed_paths ({[str(p) for p in allowed_roots]})"
            )

        # If config looks like a local path (starts with / or ./ or ../
        # or contains a path separator pointing to a yaml/yml file),
        # resolve and gate it. Registry refs like "p/python" use a
        # forward slash but don't end in .yaml/.yml, so we use the
        # extension as the disambiguator.
        resolved_config = config
        if config.endswith((".yaml", ".yml")):
            try:
                cfg = Path(config).resolve(strict=True)
            except FileNotFoundError:
                raise SemgrepScanError(
                    f"config file does not exist: {config!r}"
                )
            except OSError as e:
                raise SemgrepScanError(
                    f"config file resolution failed: {e}"
                ) from e
            if not _is_within_any(cfg, allowed_roots):
                raise SemgrepScanError(
                    f"config file {str(cfg)!r} is outside allowed_paths"
                )
            if not cfg.is_file():
                raise SemgrepScanError(
                    f"config must be a regular file when given as "
                    f"a yaml path: {str(cfg)!r}"
                )
            resolved_config = str(cfg)

        invocation = _locate_semgrep()
        if invocation is None:
            raise SemgrepNotInstalledError(
                "semgrep is not installed (not on PATH and not invokable as "
                "`python3 -m semgrep`). Install via `pip install semgrep`."
            )

        argv = list(invocation) + [
            "scan",
            "--json",
            "--quiet",
            "--no-rewrite-rule-ids",
            "--disable-version-check",
            "--metrics=off",
            f"--config={resolved_config}",
            str(target),
        ]

        try:
            proc = subprocess.run(
                argv,
                timeout=timeout_seconds,
                capture_output=True,
                text=True,
                check=False,
                # Suppress semgrep's interactive prompts and version
                # check telemetry. SEMGREP_USER_AGENT_APPEND is a
                # courtesy so the operator can identify Forest in
                # their semgrep app server logs if they have one.
                env={
                    "SEMGREP_SEND_METRICS": "off",
                    "SEMGREP_ENABLE_VERSION_CHECK": "0",
                    "SEMGREP_USER_AGENT_APPEND": "forest-soul-forge",
                    "PATH": __import__("os").environ.get("PATH", ""),
                },
            )
        except subprocess.TimeoutExpired as e:
            raise SemgrepScanError(
                f"semgrep timed out after {timeout_seconds}s on {target}; "
                f"increase timeout_seconds or scope the path narrower"
            ) from e
        except FileNotFoundError as e:
            raise SemgrepNotInstalledError(
                f"semgrep invocation failed at exec time: {e}"
            ) from e

        # Semgrep exit codes:
        #   0  = clean (no findings)
        #   1  = findings found (normal — not a refusal)
        #   2+ = configuration / parse error (refusal)
        # Some semgrep versions return 0 with findings in JSON; the
        # canonical signal is the JSON body, so we parse first and
        # use exit_code as supplemental info.
        if proc.returncode not in (0, 1):
            raise SemgrepScanError(
                f"semgrep exited with code {proc.returncode}: "
                f"{(proc.stderr or proc.stdout or '').strip()[:500]}"
            )

        try:
            payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
        except json.JSONDecodeError as e:
            raise SemgrepScanError(
                f"semgrep produced unparseable JSON: {e}; first 200 chars: "
                f"{proc.stdout[:200]!r}"
            ) from e

        raw_findings = payload.get("results", []) if isinstance(payload, dict) else []
        if not isinstance(raw_findings, list):
            raise SemgrepScanError(
                f"semgrep JSON 'results' is not a list; got "
                f"{type(raw_findings).__name__}"
            )

        normalized = [_normalize_finding(f) for f in raw_findings]

        if severity_filter:
            allowed_sev = set(severity_filter)
            normalized = [f for f in normalized if f["severity"] in allowed_sev]

        actual_count = len(normalized)
        truncated = actual_count > max_findings
        kept = normalized[:max_findings]

        return ToolResult(
            output={
                "path":           str(target),
                "config":         resolved_config,
                "findings_count": len(kept),
                "truncated":      truncated,
                "exit_code":      proc.returncode,
                "findings":       kept,
            },
            metadata={
                "allowed_roots":      [str(p) for p in allowed_roots],
                "actual_count":       actual_count,
                "max_findings":       max_findings,
                "semgrep_invocation": list(invocation),
                "severity_filter":    severity_filter,
            },
            side_effect_summary=(
                f"semgrep_scan: {len(kept)}/{actual_count} findings on "
                f"{target.name} (config={resolved_config})"
            ),
        )


# ---------------------------------------------------------------------------
# Helpers — module-level for testability
# ---------------------------------------------------------------------------
def _locate_semgrep() -> tuple[str, ...] | None:
    """Find a working semgrep invocation. Tries `semgrep` on PATH first
    (faster startup since semgrep ships a binary entrypoint), falls
    back to `python3 -m semgrep`."""
    if shutil.which("semgrep"):
        return ("semgrep",)
    try:
        proc = subprocess.run(
            ["python3", "-m", "semgrep", "--version"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            return ("python3", "-m", "semgrep")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def _normalize_finding(raw: dict[str, Any]) -> dict[str, Any]:
    """Map semgrep's JSON finding shape to FSF's stable output schema.

    Semgrep JSON shape (as of semgrep 1.x):
      {
        "check_id": "rules.foo",
        "path": "src/x.py",
        "start": {"line": int, "col": int},
        "end":   {"line": int, "col": int},
        "extra": {
          "severity": "ERROR" | "WARNING" | "INFO",
          "message": "...",
          "lines": "code snippet"
        }
      }
    """
    extra = raw.get("extra") or {}
    start = raw.get("start") or {}
    end = raw.get("end") or {}
    return {
        "rule_id":      raw.get("check_id") or "",
        "severity":     extra.get("severity") or "INFO",
        "message":      extra.get("message") or "",
        "filename":     raw.get("path") or "",
        "start_line":   int(start.get("line") or 0),
        "end_line":     int(end.get("line") or 0),
        "start_column": int(start.get("col") or 0),
        "end_column":   int(end.get("col") or 0),
        "code_snippet": (extra.get("lines") or "").strip(),
    }


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
    "SemgrepScanTool",
    "SemgrepScanError",
    "SemgrepNotInstalledError",
    "DEFAULT_MAX_FINDINGS",
    "SEMGREP_MAX_FINDINGS_HARD_CAP",
    "DEFAULT_TIMEOUT_SECONDS",
    "VALID_SEVERITIES",
]
