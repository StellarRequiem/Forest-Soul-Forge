"""Sandbox execution for forged tests — ADR-0030 T3b.

Runs the staged ``test_<name>.py`` in a subprocess, isolated with
``python -I`` (no user site-packages, no PYTHONPATH inheritance, no
PYTHON* env vars), against the staged ``tool.py`` next to it.

This is **subprocess isolation**, not container isolation. Container
sandboxing (Docker, gVisor, microVM) is on the long-term threat
model upgrade (ADR-0025) but is overkill for v1: the operator
already reads the .py before approving, static analysis already
filtered out obvious sandbox escapes, and the tests run on the
operator's own machine.

If a test imports network libraries, the test will reach the
network. If a test does ``open(..., 'w')``, it writes to disk.
That's why the testgen system prompt explicitly forbids those.
The static-analysis pass also flags them in the tool source.
Defense in layers, not in a single container wall.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TestRunResult:
    """Outcome of a sandboxed test run.

    ``passed`` is None when the run couldn't even start (no test file,
    pytest unavailable, etc.). ``summary`` is a short operator-facing
    string suitable for inline display.
    """

    ran: bool
    passed: bool | None
    summary: str
    stdout: str
    stderr: str
    exit_code: int | None
    test_path: Path | None


def run_staged_tests(
    *,
    staged_dir: Path,
    test_path: Path | None,
    timeout_s: float = 30.0,
) -> TestRunResult:
    """Run pytest against the staged test file.

    The subprocess is invoked with::

        python -I -m pytest <test_path> -q --no-header --tb=short

    ``-I`` blocks user site-packages and clears PYTHONPATH/etc. The
    cwd is set to ``staged_dir`` so the test's ``from tool import ...``
    resolves to the sibling ``tool.py``.

    Returns a :class:`TestRunResult` with pass/fail + captured output.
    Caller (the CLI) decides whether to block install on failure.
    """
    if test_path is None or not test_path.exists():
        return TestRunResult(
            ran=False, passed=None,
            summary="no test file generated",
            stdout="", stderr="", exit_code=None,
            test_path=test_path,
        )
    if not _pytest_available():
        return TestRunResult(
            ran=False, passed=None,
            summary=(
                "pytest not available on PATH for the active Python "
                "interpreter — install dev extras with "
                "`pip install -e .[dev]`"
            ),
            stdout="", stderr="", exit_code=None,
            test_path=test_path,
        )

    cmd = [
        sys.executable, "-I", "-m", "pytest",
        str(test_path),
        "-q", "--no-header", "--tb=short",
    ]
    try:
        proc = subprocess.run(  # noqa: S603 — sandbox by design
            cmd,
            cwd=staged_dir,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        return TestRunResult(
            ran=True, passed=False,
            summary=f"timed out after {timeout_s:.0f}s",
            stdout=(e.stdout or "") if isinstance(e.stdout, str) else "",
            stderr=(e.stderr or "") if isinstance(e.stderr, str) else "",
            exit_code=None,
            test_path=test_path,
        )
    passed = proc.returncode == 0
    summary = _short_summary(proc.stdout, proc.returncode)
    return TestRunResult(
        ran=True, passed=passed, summary=summary,
        stdout=proc.stdout, stderr=proc.stderr,
        exit_code=proc.returncode,
        test_path=test_path,
    )


def _pytest_available() -> bool:
    """Check whether ``python -m pytest --version`` works in the
    isolated subprocess. A missing pytest is a setup issue, not a
    forge failure — surface as a non-fatal "tests skipped"."""
    try:
        proc = subprocess.run(  # noqa: S603 — read-only check
            [sys.executable, "-I", "-m", "pytest", "--version"],
            capture_output=True, text=True, timeout=5.0, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return proc.returncode == 0


def _short_summary(stdout: str, exit_code: int) -> str:
    """Pull the last non-empty line of pytest's output as the summary.
    pytest's terminal report ends with `=== N passed in X.XXs ===`
    or `=== N failed, M passed in X.XXs ===` — that's what we surface."""
    last = ""
    for line in stdout.splitlines():
        line = line.strip()
        if line:
            last = line
    if not last:
        return f"exit={exit_code}, no output"
    return last


# ---------------------------------------------------------------------------
# Convenience: copy package source into the staged dir for `from
# forest_soul_forge.tools.base import ...` imports.
# ---------------------------------------------------------------------------
def prepare_test_environment(staged_dir: Path) -> None:
    """Symlink (or copy) the forest_soul_forge package into staged_dir
    so the generated test's imports resolve.

    Tests reference ``forest_soul_forge.tools.base`` for ToolContext
    and ToolValidationError. With ``python -I`` the subprocess can't
    reach the parent process's site-packages or PYTHONPATH, so we
    drop a sys.path shim into the staged dir.

    The shim is a ``conftest.py`` that prepends the project source
    directory to sys.path. Cheap and uncontroversial — pytest picks
    up conftest.py automatically.
    """
    # Find the installed package root by walking up from this file.
    # forge/sandbox.py → forge/ → forest_soul_forge/ → src/
    pkg_root = Path(__file__).resolve().parent.parent  # forest_soul_forge/
    src_root = pkg_root.parent  # src/
    conftest = staged_dir / "conftest.py"
    if conftest.exists():
        return  # already set up
    conftest.write_text(
        "# Generated by Tool Forge sandbox runner — prepend project src/\n"
        "# so tests can import forest_soul_forge.* under `python -I`.\n"
        "import sys\n"
        f"sys.path.insert(0, {str(src_root)!r})\n"
        "sys.path.insert(0, '.')\n",
        encoding="utf-8",
    )
