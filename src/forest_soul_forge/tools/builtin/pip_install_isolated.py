"""``pip_install_isolated.v1`` — install packages into an isolated venv.

Side effects: filesystem (pip writes packages into the venv's
site-packages, plus pip's own cache). Distinct from the other
G.1.A primitives because this is the only one that materially
changes the agent's environment — it's the gateway tool for any
agent-driven dependency change.

Tenth Phase G.1.A programming primitive. Where the rest of the
G.1.A batch are read_only inspection tools, pip_install_isolated
is the actuator that completes a change loop: agent reads code
(code_read), proposes a change (code_edit), runs tests
(pytest_run), and — if the test failure points to a missing
dependency — pulls it in via this tool.

Required-initiative L4 (reversible-with-policy per ADR-0021-am
§5). Reasoning: a pip install is reversible (pip uninstall) but
not trivially so (a botched install can leave broken metadata
in site-packages). The L4 floor means SW-track Engineer (Actuator
default L5) reaches autonomously; SW-track Reviewer (Guardian
default L3) does NOT — Reviewer must be deliberately birthed at
ceiling L4 to use this tool. Companion (L1) is refused at the
dispatcher.

Per-agent constitution must populate:
  allowed_paths: [/abs/path/to/venv_parent, ...]

Refusal cases:
  - venv_path outside allowed_paths
  - venv doesn't exist (we DON'T create venvs — that's a separate
    primitive yet to be filed; this tool just installs into
    existing venvs)
  - venv structure invalid (no bin/python or Scripts/python.exe)
  - any package name fails the PEP-503-ish safe pattern
  - timeout (5 min default, 30 min max)

Output: {venv_path, packages_requested, installed, skipped, stdout
(capped), stderr (capped), exit_code, pip_version}
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
PIP_INSTALL_TIMEOUT_HARD_CAP = 1800
PIP_INSTALL_TIMEOUT_MIN = 1
DEFAULT_MAX_LOG_LINES = 200
PIP_INSTALL_MAX_LOG_LINES_HARD_CAP = 2000

# PEP-503-ish safe pattern for a single requirement spec.
# Allows: name, name==1.2.3, name>=1.2,<2.0, name[extra], name~=1.2,
#   name @ git+https://...  (the @ form is editable VCS install)
# Rejects: shell metacharacters, embedded `--upgrade`, paths starting
# with - that could be flag injection.
_PKG_NAME_RE = re.compile(
    r"^(?!-)"
    r"[A-Za-z0-9_.\-]+"            # name
    r"(?:\[[A-Za-z0-9_.,\-]+\])?"  # optional extras
    r"(?:[ ]*[<>=!~]=?[ ]*[A-Za-z0-9_.*+\-]+(?:[ ]*,[ ]*[<>=!~]=?[ ]*[A-Za-z0-9_.*+\-]+)*)?$"
)
# VCS-form requirement: name @ git+https://... — checked separately so
# the URL portion can use a wider character set without weakening the
# main name regex.
_VCS_PKG_RE = re.compile(
    r"^(?!-)"
    r"[A-Za-z0-9_.\-]+"
    r"[ ]*@[ ]*"
    r"git\+(?:https|ssh)://[A-Za-z0-9._/:@\-]+"
    r"(?:#egg=[A-Za-z0-9_.\-]+)?"
    r"(?:@[A-Za-z0-9._\-]+)?$"
)


class PipInstallError(ToolValidationError):
    """Raised by pip_install_isolated for path-allowlist or invocation failures."""


class PipNotFoundError(PipInstallError):
    """Raised when pip is not available in the venv (rare — venv created
    without ensurepip, or corrupt venv)."""


class VenvInvalidError(PipInstallError):
    """Raised when the supplied venv_path doesn't look like a valid venv
    (no bin/python or Scripts/python.exe)."""


class PipInstallIsolatedTool:
    """Args:
      venv_path (str, required): path to an existing venv (its top-
        level dir, not the bin subdir). Must resolve within
        allowed_paths. We DON'T create venvs.
      packages (list[str], required): packages to install. Each entry
        must match the PEP-503-ish safe pattern OR the VCS form
        (name @ git+...). Empty list rejected.
      upgrade (bool, optional): forward --upgrade. Default false.
      no_deps (bool, optional): forward --no-deps. Default false.
      timeout_seconds (int, optional): subprocess timeout. Default
        300 (5 min), max 1800 (30 min).
      max_log_lines (int, optional): cap on stdout/stderr lines
        returned. Default 200, max 2000.

    Output:
      {
        "venv_path":          str,    # resolved absolute venv path
        "packages_requested": list[str],
        "installed":          list[str],   # parsed from pip's output
        "skipped":            list[str],   # already-satisfied
        "exit_code":          int,
        "pip_version":        str,
        "stdout":             str,    # capped at max_log_lines
        "stderr":             str,    # capped at max_log_lines
        "stdout_truncated":   bool,
        "stderr_truncated":   bool,
      }

    Constraints (read from ctx.constraints):
      allowed_paths: list[str]   # required, absolute paths
    """

    name = "pip_install_isolated"
    version = "1"
    side_effects = "filesystem"
    required_initiative_level = "L4"   # per ADR-0021-am §5

    def validate(self, args: dict[str, Any]) -> None:
        venv_path = args.get("venv_path")
        if not isinstance(venv_path, str) or not venv_path.strip():
            raise ToolValidationError(
                "venv_path is required and must be a non-empty string"
            )

        packages = args.get("packages")
        if (
            not isinstance(packages, list)
            or not packages
            or any(not isinstance(p, str) or not p.strip() for p in packages)
        ):
            raise ToolValidationError(
                "packages is required and must be a non-empty list of "
                "non-empty strings"
            )
        for pkg in packages:
            if not _is_valid_pkg_spec(pkg):
                raise ToolValidationError(
                    f"package spec rejected by safe-pattern check: {pkg!r}. "
                    f"Must match PEP-503 name (with optional extras and "
                    f"version specifier) OR VCS form 'name @ git+url'."
                )

        upgrade = args.get("upgrade")
        if upgrade is not None and not isinstance(upgrade, bool):
            raise ToolValidationError(
                f"upgrade must be a bool when provided; got {type(upgrade).__name__}"
            )

        no_deps = args.get("no_deps")
        if no_deps is not None and not isinstance(no_deps, bool):
            raise ToolValidationError(
                f"no_deps must be a bool when provided; got {type(no_deps).__name__}"
            )

        timeout = args.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        if (
            not isinstance(timeout, int)
            or timeout < PIP_INSTALL_TIMEOUT_MIN
            or timeout > PIP_INSTALL_TIMEOUT_HARD_CAP
        ):
            raise ToolValidationError(
                f"timeout_seconds must be in [{PIP_INSTALL_TIMEOUT_MIN}, "
                f"{PIP_INSTALL_TIMEOUT_HARD_CAP}]; got {timeout!r}"
            )

        max_log_lines = args.get("max_log_lines", DEFAULT_MAX_LOG_LINES)
        if (
            not isinstance(max_log_lines, int)
            or max_log_lines < 1
            or max_log_lines > PIP_INSTALL_MAX_LOG_LINES_HARD_CAP
        ):
            raise ToolValidationError(
                f"max_log_lines must be a positive int <= "
                f"{PIP_INSTALL_MAX_LOG_LINES_HARD_CAP}; got {max_log_lines!r}"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        raw_venv: str = args["venv_path"]
        packages: list[str] = list(args["packages"])
        upgrade = bool(args.get("upgrade", False))
        no_deps = bool(args.get("no_deps", False))
        timeout_seconds = int(args.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        max_log_lines = int(args.get("max_log_lines", DEFAULT_MAX_LOG_LINES))

        allowed_paths_raw = ctx.constraints.get("allowed_paths") or ()
        if not allowed_paths_raw:
            raise PipInstallError(
                "agent has no allowed_paths in its constitution — "
                "pip_install_isolated.v1 refuses to touch the filesystem"
            )

        allowed_roots = tuple(_resolve_allowlist(allowed_paths_raw))

        try:
            venv = Path(raw_venv).resolve(strict=True)
        except FileNotFoundError:
            raise PipInstallError(f"venv_path does not exist: {raw_venv!r}")
        except OSError as e:
            raise PipInstallError(f"venv_path resolution failed: {e}") from e

        if not venv.is_dir():
            raise PipInstallError(
                f"venv_path must be a directory; got {str(venv)!r}"
            )

        if not _is_within_any(venv, allowed_roots):
            raise PipInstallError(
                f"venv_path {str(venv)!r} is outside the agent's "
                f"allowed_paths ({[str(p) for p in allowed_roots]})"
            )

        venv_python = _locate_venv_python(venv)
        if venv_python is None:
            raise VenvInvalidError(
                f"venv_path {str(venv)!r} doesn't look like a valid venv "
                f"(no bin/python or Scripts/python.exe). Create a venv "
                f"first via `python3 -m venv <path>`."
            )

        argv = [str(venv_python), "-m", "pip", "install"]
        if upgrade:
            argv.append("--upgrade")
        if no_deps:
            argv.append("--no-deps")
        # `--disable-pip-version-check` quiets pip's startup nag and
        # `--no-input` ensures we never block waiting for prompts.
        argv.extend([
            "--disable-pip-version-check",
            "--no-input",
            "--",
        ])
        argv.extend(packages)

        try:
            proc = subprocess.run(
                argv,
                timeout=timeout_seconds,
                capture_output=True,
                text=True,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise PipInstallError(
                f"pip install timed out after {timeout_seconds}s in "
                f"{venv}; partial state may exist in the venv. Investigate "
                f"with `<venv>/bin/pip list` before retrying."
            ) from e
        except FileNotFoundError as e:
            raise PipNotFoundError(
                f"pip invocation failed at exec time: {e}"
            ) from e

        # Pip exit codes:
        #   0 = success
        #   1 = errors (network, package not found, version conflict)
        #   2 = command-line / config error
        #   3 = errors caught and reported with --report or similar
        # Anything non-zero is a refusal — the agent's allowed_paths
        # are state we want to keep clean. Surface the captured stderr
        # so the caller can read what went wrong.
        installed, skipped = _parse_pip_output(proc.stdout)
        stdout_capped, stdout_trunc = _cap_log(proc.stdout, max_log_lines)
        stderr_capped, stderr_trunc = _cap_log(proc.stderr, max_log_lines)

        # We DO return ToolResult on non-zero exit so the caller can
        # see what pip said; but we mark the failure in metadata and
        # leave installed/skipped potentially empty. The dispatch
        # layer's audit chain records the exit_code so a subsequent
        # agent decision can react to it.
        pip_version = _detect_pip_version(venv_python)

        return ToolResult(
            output={
                "venv_path":          str(venv),
                "packages_requested": list(packages),
                "installed":          installed,
                "skipped":            skipped,
                "exit_code":          proc.returncode,
                "pip_version":        pip_version,
                "stdout":             stdout_capped,
                "stderr":             stderr_capped,
                "stdout_truncated":   stdout_trunc,
                "stderr_truncated":   stderr_trunc,
            },
            metadata={
                "allowed_roots":   [str(p) for p in allowed_roots],
                "venv_python":     str(venv_python),
                "argv":            argv,
                "upgrade":         upgrade,
                "no_deps":         no_deps,
            },
            side_effect_summary=(
                f"pip_install_isolated[{venv.name}]: "
                f"{len(installed)} installed, {len(skipped)} skipped, "
                f"exit={proc.returncode}"
            ),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _is_valid_pkg_spec(pkg: str) -> bool:
    """Validate a package spec against the safe pattern. Accepts the
    standard PEP-503-ish form OR the VCS form."""
    pkg = pkg.strip()
    if not pkg:
        return False
    if pkg.startswith("-"):
        return False
    if "@" in pkg:
        return bool(_VCS_PKG_RE.match(pkg))
    return bool(_PKG_NAME_RE.match(pkg))


def _locate_venv_python(venv: Path) -> Path | None:
    """Find the python interpreter inside a venv. Returns None if it
    doesn't look like a valid venv."""
    # POSIX layout
    candidate = venv / "bin" / "python"
    if candidate.exists():
        return candidate
    candidate = venv / "bin" / "python3"
    if candidate.exists():
        return candidate
    # Windows layout
    candidate = venv / "Scripts" / "python.exe"
    if candidate.exists():
        return candidate
    return None


def _detect_pip_version(venv_python: Path) -> str:
    """Best-effort pip version detection. Returns empty string on failure
    rather than raising — pip_version is metadata, not load-bearing."""
    try:
        proc = subprocess.run(
            [str(venv_python), "-m", "pip", "--version"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if proc.returncode == 0:
            # Output format: "pip 24.0 from /path/to/pip (python 3.11)"
            first = proc.stdout.strip().split("\n")[0]
            tokens = first.split()
            if len(tokens) >= 2:
                return tokens[1]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return ""


# Patterns to extract install state from pip's stdout. These are
# stable across pip 23-25.x.
_INSTALLED_RE = re.compile(r"^Successfully installed (.+)$", re.MULTILINE)
_SKIPPED_RE = re.compile(
    r"^Requirement already satisfied: ([A-Za-z0-9_.\-]+)", re.MULTILINE,
)


def _parse_pip_output(stdout: str) -> tuple[list[str], list[str]]:
    """Extract (installed, skipped) from pip's stdout.

    The 'Successfully installed' line lists all installed packages
    space-separated, like 'Successfully installed pkg-1.0 dep-2.3'.
    The 'Requirement already satisfied' lines fire once per package
    that was already at the right version.
    """
    installed: list[str] = []
    skipped: list[str] = []
    m = _INSTALLED_RE.search(stdout)
    if m:
        installed = [tok for tok in m.group(1).split() if tok]
    for m2 in _SKIPPED_RE.finditer(stdout):
        skipped.append(m2.group(1))
    return installed, skipped


def _cap_log(text: str, max_lines: int) -> tuple[str, bool]:
    """Cap a multi-line string at max_lines. Returns (capped_text, truncated)."""
    if not text:
        return "", False
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text, False
    return "\n".join(lines[:max_lines]), True


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
    "PipInstallIsolatedTool",
    "PipInstallError",
    "PipNotFoundError",
    "VenvInvalidError",
    "DEFAULT_TIMEOUT_SECONDS",
    "PIP_INSTALL_TIMEOUT_HARD_CAP",
    "DEFAULT_MAX_LOG_LINES",
]
