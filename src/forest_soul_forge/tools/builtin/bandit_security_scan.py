"""``bandit_security_scan.v1`` — Python-specific security linter via bandit.

Side effects: read_only. Bandit reports findings; no mutation.

Ninth Phase G.1.A programming primitive. Where semgrep_scan covers
multi-language patterns and arbitrary rulesets, bandit is the
canonical Python-specific security gate — the OWASP-aligned rule
set has been curated for over a decade and catches the common
Python footguns (use of pickle, exec, shell=True, weak hashing,
flask debug=True, hardcoded secrets pattern). SW-track Reviewer
+ security_low kit consumers reach.

Per-agent constitution must populate:
  allowed_paths: [/abs/path/to/repo, ...]

Path discipline mirrors semgrep_scan.v1.

Argument-injection defense:
  - severity_level + confidence_level constrained to enum
    {low, medium, high}
  - skip_tests list (B-codes) validated against pattern ^B[0-9]{3}$
  - timeout enforced as separate kwarg

Bandit invocation:
  - 'bandit' on PATH first, 'python3 -m bandit' fallback
  - Flags: -f json, -q (quiet), -r (recursive — bandit auto-detects
    files vs dirs but -r explicit ensures consistency)
  - Output parsed from bandit's JSON shape
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
BANDIT_MAX_FINDINGS_HARD_CAP = 10_000
DEFAULT_TIMEOUT_SECONDS = 60
BANDIT_TIMEOUT_HARD_CAP = 600
BANDIT_TIMEOUT_MIN = 1

VALID_LEVELS = ("low", "medium", "high")

# Test ID grammar: bandit's IDs are B followed by 3 digits (B101, B102, ...).
import re as _re
_TEST_ID_RE = _re.compile(r"^B[0-9]{3}$")


class BanditScanError(ToolValidationError):
    """Raised by bandit_security_scan for path-allowlist or invocation failures."""


class BanditNotInstalledError(BanditScanError):
    """Raised when bandit is not installed."""


class BanditSecurityScanTool:
    """Args:
      path (str, required): file or directory to scan.
      severity_level (str, optional): minimum severity to report.
        One of {low, medium, high}. Default 'low'.
      confidence_level (str, optional): minimum confidence to report.
        One of {low, medium, high}. Default 'low'.
      skip_tests (list[str], optional): bandit test IDs to skip.
        Each must match ^B[0-9]{3}$.
      max_findings (int, optional): cap on findings. Default 500,
        max 10000.
      timeout_seconds (int, optional): subprocess timeout. Default
        60, max 600.

    Output:
      {
        "path":           str,
        "findings_count": int,
        "truncated":      bool,
        "exit_code":      int,
        "findings": [
          {
            "test_id":       str,    # B101, B102, etc.
            "test_name":     str,    # exec_used, hardcoded_password_string
            "severity":      str,    # LOW | MEDIUM | HIGH
            "confidence":    str,    # LOW | MEDIUM | HIGH
            "filename":      str,
            "line":          int,
            "message":       str,
            "code_snippet":  str,
            "more_info":     str,    # url to bandit docs
          }, ...
        ]
      }
    """

    name = "bandit_security_scan"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ToolValidationError(
                "path is required and must be a non-empty string"
            )

        for opt in ("severity_level", "confidence_level"):
            val = args.get(opt)
            if val is not None and val not in VALID_LEVELS:
                raise ToolValidationError(
                    f"{opt} must be one of {VALID_LEVELS}; got {val!r}"
                )

        skip_tests = args.get("skip_tests")
        if skip_tests is not None:
            if not isinstance(skip_tests, list) or any(
                not isinstance(t, str) or not _TEST_ID_RE.match(t)
                for t in skip_tests
            ):
                raise ToolValidationError(
                    "skip_tests must be a list of bandit test IDs "
                    "matching ^B[0-9]{3}$"
                )

        max_findings = args.get("max_findings", DEFAULT_MAX_FINDINGS)
        if (
            not isinstance(max_findings, int)
            or max_findings < 1
            or max_findings > BANDIT_MAX_FINDINGS_HARD_CAP
        ):
            raise ToolValidationError(
                f"max_findings must be a positive int <= "
                f"{BANDIT_MAX_FINDINGS_HARD_CAP}; got {max_findings!r}"
            )

        timeout = args.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        if (
            not isinstance(timeout, int)
            or timeout < BANDIT_TIMEOUT_MIN
            or timeout > BANDIT_TIMEOUT_HARD_CAP
        ):
            raise ToolValidationError(
                f"timeout_seconds must be in [{BANDIT_TIMEOUT_MIN}, "
                f"{BANDIT_TIMEOUT_HARD_CAP}]; got {timeout!r}"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        raw_path: str = args["path"]
        severity_level = args.get("severity_level", "low")
        confidence_level = args.get("confidence_level", "low")
        skip_tests = args.get("skip_tests") or []
        max_findings = int(args.get("max_findings", DEFAULT_MAX_FINDINGS))
        timeout_seconds = int(args.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))

        allowed_paths_raw = ctx.constraints.get("allowed_paths") or ()
        if not allowed_paths_raw:
            raise BanditScanError(
                "agent has no allowed_paths in its constitution — "
                "bandit_security_scan.v1 refuses to touch the filesystem"
            )

        allowed_roots = tuple(_resolve_allowlist(allowed_paths_raw))

        try:
            target = Path(raw_path).resolve(strict=True)
        except FileNotFoundError:
            raise BanditScanError(f"path does not exist: {raw_path!r}")
        except OSError as e:
            raise BanditScanError(f"path resolution failed: {e}") from e

        if not _is_within_any(target, allowed_roots):
            raise BanditScanError(
                f"path {str(target)!r} is outside the agent's "
                f"allowed_paths ({[str(p) for p in allowed_roots]})"
            )

        invocation = _locate_bandit()
        if invocation is None:
            raise BanditNotInstalledError(
                "bandit is not installed (not on PATH and not invokable as "
                "`python3 -m bandit`). Install via `pip install bandit`."
            )

        argv = list(invocation) + [
            "-f", "json",
            "-q",
            "-r" if target.is_dir() else "",
            # Bandit's severity/confidence threshold flags are -l/-ll/-lll
            # (low/medium/high) and -i/-ii/-iii. _severity_to_flag returns
            # the count-of-l characters; we prefix with a single dash here
            # to form the full flag like '-lll'. Default level 'low' is the
            # bandit default so we omit the flag entirely.
            f"-{_severity_to_flag(severity_level)}" if severity_level != "low" else "",
            f"-{_confidence_to_flag(confidence_level)}" if confidence_level != "low" else "",
        ]
        # Filter empty strings produced by the conditional flags above.
        argv = [a for a in argv if a]

        if skip_tests:
            argv.append("--skip")
            argv.append(",".join(skip_tests))

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
            raise BanditScanError(
                f"bandit timed out after {timeout_seconds}s on {target}; "
                f"increase timeout_seconds or scope the path narrower"
            ) from e
        except FileNotFoundError as e:
            raise BanditNotInstalledError(
                f"bandit invocation failed at exec time: {e}"
            ) from e

        # Bandit exit codes:
        #   0 = no findings (success)
        #   1 = findings found
        #   2 = errors during scan (refusal)
        if proc.returncode not in (0, 1):
            raise BanditScanError(
                f"bandit exited with code {proc.returncode}: "
                f"{(proc.stderr or proc.stdout or '').strip()[:500]}"
            )

        try:
            payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
        except json.JSONDecodeError as e:
            raise BanditScanError(
                f"bandit produced unparseable JSON: {e}; first 200 chars: "
                f"{proc.stdout[:200]!r}"
            ) from e

        raw_findings = payload.get("results", []) if isinstance(payload, dict) else []
        if not isinstance(raw_findings, list):
            raise BanditScanError(
                f"bandit JSON 'results' is not a list; got "
                f"{type(raw_findings).__name__}"
            )

        normalized = [_normalize_finding(f) for f in raw_findings]
        actual_count = len(normalized)
        truncated = actual_count > max_findings
        kept = normalized[:max_findings]

        return ToolResult(
            output={
                "path":           str(target),
                "findings_count": len(kept),
                "truncated":      truncated,
                "exit_code":      proc.returncode,
                "findings":       kept,
            },
            metadata={
                "allowed_roots":     [str(p) for p in allowed_roots],
                "actual_count":      actual_count,
                "max_findings":      max_findings,
                "bandit_invocation": list(invocation),
                "severity_level":    severity_level,
                "confidence_level":  confidence_level,
                "skip_tests":        list(skip_tests),
            },
            side_effect_summary=(
                f"bandit_security_scan: {len(kept)}/{actual_count} findings "
                f"on {target.name} (sev>={severity_level}, "
                f"conf>={confidence_level})"
            ),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _locate_bandit() -> tuple[str, ...] | None:
    """Find a working bandit invocation. PATH first, module fallback."""
    if shutil.which("bandit"):
        return ("bandit",)
    try:
        proc = subprocess.run(
            ["python3", "-m", "bandit", "--version"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            return ("python3", "-m", "bandit")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def _severity_to_flag(level: str) -> str:
    """Map severity level to bandit's -l flag value.
    Bandit's CLI uses -l, -ll, -lll for low/med/high; the count of
    'l's is the threshold. We synthesize the right number of 'l's.
    Wait — that's the legacy interface. Modern bandit uses -l/--level
    and accepts an integer count. Use the safer pattern: pass --severity-level
    flag with the named value if the version supports it.

    For maximum compatibility with bandit 1.7+, we use the count-of-l
    convention which has been stable. low=1, medium=2, high=3.
    """
    return "l" * {"low": 1, "medium": 2, "high": 3}[level]


def _confidence_to_flag(level: str) -> str:
    """Map confidence level to bandit's -i flag value (count-of-i)."""
    return "i" * {"low": 1, "medium": 2, "high": 3}[level]


def _normalize_finding(raw: dict[str, Any]) -> dict[str, Any]:
    """Map bandit's JSON finding shape to FSF's stable output schema."""
    return {
        "test_id":      raw.get("test_id") or "",
        "test_name":    raw.get("test_name") or "",
        "severity":     raw.get("issue_severity") or "LOW",
        "confidence":   raw.get("issue_confidence") or "LOW",
        "filename":     raw.get("filename") or "",
        "line":         int(raw.get("line_number") or 0),
        "message":      raw.get("issue_text") or "",
        "code_snippet": (raw.get("code") or "").strip(),
        "more_info":    raw.get("more_info") or "",
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
    "BanditSecurityScanTool",
    "BanditScanError",
    "BanditNotInstalledError",
    "DEFAULT_MAX_FINDINGS",
    "BANDIT_MAX_FINDINGS_HARD_CAP",
    "DEFAULT_TIMEOUT_SECONDS",
    "VALID_LEVELS",
]
