"""``patch_check.v1`` — query OS + package-manager updaters.

ADR-0033 Phase B1. PatchPatrol's reason to exist: a daily sweep
that asks the system "what's pending?" and emits a structured list
the operator can prioritize.

Backends probed in order (only those whose binaries exist on PATH
are run; missing binaries are reported in ``backends_skipped``):

  * **brew** — ``brew outdated --json=v2`` (macOS Homebrew)
  * **softwareupdate** — ``softwareupdate -l`` (macOS system updates)
  * **apt** — ``apt list --upgradable`` (Debian/Ubuntu)
  * **dnf** — ``dnf check-update`` (Fedora/RHEL)

Each backend's output is parsed into a uniform ``{name,
current_version, available_version, source}`` shape. Parsing
failures are recorded in ``parse_errors`` rather than dropping
the result, so an operator inspecting an empty list knows whether
"nothing to patch" is real or a parser regression.

side_effects=read_only — every backend is invoked with a query-
only flag (no install, no autoremove). The tool refuses to run
backends with mutating subcommands.

Timeout: 30 seconds per backend. softwareupdate -l is the slowest
on macOS; the cap accommodates a normal network-backed query but
fails closed if the update server hangs.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_BACKENDS = ("brew", "softwareupdate", "apt", "dnf")
_TIMEOUT_SECONDS = 30


class PatchCheckTool:
    """Query installed package managers for pending updates.

    Args:
      backends (list[str], optional): subset of ['brew',
        'softwareupdate', 'apt', 'dnf']. Default: probe all four.
        Only backends whose binary exists on PATH are actually run;
        unavailable ones are reported in ``backends_skipped``.

    Output:
      {
        "updates": [
          {"name": str, "current_version": str | null,
           "available_version": str, "source": str}, ...
        ],
        "backends_run":     [str, ...],
        "backends_skipped": [{"backend": str, "reason": str}, ...],
        "parse_errors":     [{"backend": str, "detail": str}, ...]
      }
    """

    name = "patch_check"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        backends = args.get("backends")
        if backends is not None:
            if not isinstance(backends, list):
                raise ToolValidationError(
                    "backends must be a list of strings"
                )
            for b in backends:
                if b not in _BACKENDS:
                    raise ToolValidationError(
                        f"backend {b!r} not in {list(_BACKENDS)}"
                    )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        wanted = args.get("backends") or list(_BACKENDS)
        updates: list[dict[str, Any]] = []
        backends_run: list[str] = []
        backends_skipped: list[dict[str, str]] = []
        parse_errors: list[dict[str, str]] = []

        for backend in wanted:
            binary = shutil.which(backend)
            if binary is None:
                backends_skipped.append({
                    "backend": backend, "reason": "binary_not_on_path",
                })
                continue
            try:
                if backend == "brew":
                    items, perrs = _run_brew(binary)
                elif backend == "softwareupdate":
                    items, perrs = _run_softwareupdate(binary)
                elif backend == "apt":
                    items, perrs = _run_apt(binary)
                elif backend == "dnf":
                    items, perrs = _run_dnf(binary)
                else:  # unreachable — validate guards
                    continue
            except subprocess.TimeoutExpired:
                backends_skipped.append({
                    "backend": backend, "reason": "timeout",
                })
                continue
            except subprocess.CalledProcessError as e:
                backends_skipped.append({
                    "backend": backend,
                    "reason": f"exit={e.returncode}: {(e.stderr or b'').decode(errors='replace').strip()[:120]}",
                })
                continue

            backends_run.append(backend)
            updates.extend(items)
            for pe in perrs:
                parse_errors.append({"backend": backend, "detail": pe})

        return ToolResult(
            output={
                "updates":          updates,
                "backends_run":     backends_run,
                "backends_skipped": backends_skipped,
                "parse_errors":     parse_errors,
            },
            metadata={
                "update_count": len(updates),
                "backends_requested": list(wanted),
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"{len(updates)} pending updates across "
                f"{len(backends_run)} backend{'s' if len(backends_run) != 1 else ''}"
            ),
        )


# ---------------------------------------------------------------------------
# Backend runners — each returns (items, parse_errors)
# ---------------------------------------------------------------------------
def _run_subprocess(cmd: list[str]) -> tuple[int, str, str]:
    """Stdlib subprocess invocation with a fixed timeout. Returns
    (returncode, stdout, stderr) decoded as UTF-8 with replace
    errors. CalledProcessError raised by check=True is caught at
    the caller; we use check=False here because some backends
    (apt list, dnf check-update) exit non-zero on "updates
    available" which isn't actually an error."""
    proc = subprocess.run(
        cmd, capture_output=True, timeout=_TIMEOUT_SECONDS, check=False,
    )
    return (
        proc.returncode,
        proc.stdout.decode("utf-8", errors="replace"),
        proc.stderr.decode("utf-8", errors="replace"),
    )


def _run_brew(binary: str) -> tuple[list[dict], list[str]]:
    """brew outdated --json=v2 → list of casks/formulae with
    current/available versions."""
    rc, stdout, stderr = _run_subprocess([binary, "outdated", "--json=v2"])
    if rc != 0:
        raise subprocess.CalledProcessError(rc, "brew", b"", stderr.encode())
    items: list[dict] = []
    parse_errors: list[str] = []
    try:
        data = json.loads(stdout) if stdout.strip() else {}
    except json.JSONDecodeError as e:
        parse_errors.append(f"json: {e}")
        return items, parse_errors
    for kind in ("formulae", "casks"):
        for entry in data.get(kind, []):
            try:
                installed = entry.get("installed_versions") or []
                items.append({
                    "name":              entry["name"],
                    "current_version":   installed[0] if installed else None,
                    "available_version": entry.get("current_version", ""),
                    # Preserve the brew kind verbatim. Earlier code used
                    # ``kind[:-1]`` to singularize but that produced "formula"
                    # for "formulae" — wrong, because English plural rules
                    # don't strip a single trailing letter consistently.
                    # Keeping the plural form is also more honest: the JSON
                    # field IS named "formulae", and the source tag is just
                    # a passthrough label.
                    "source":            f"brew:{kind}",
                })
            except (KeyError, IndexError, TypeError) as e:
                parse_errors.append(f"{kind} entry: {e}")
    return items, parse_errors


def _run_softwareupdate(binary: str) -> tuple[list[dict], list[str]]:
    """softwareupdate -l output is human-readable; we parse the
    standard "* Label" / "Title:..., Version:..., Size: ..." form.
    No flag for JSON exists on macOS."""
    rc, stdout, stderr = _run_subprocess([binary, "-l"])
    # Exit code 0 + "No new software available" → no updates.
    # Exit code 0 + the listing → updates present.
    # Anything else is an error.
    if rc != 0:
        raise subprocess.CalledProcessError(rc, "softwareupdate", b"", stderr.encode())
    items: list[dict] = []
    parse_errors: list[str] = []
    if "no new software" in (stdout + stderr).lower():
        return items, parse_errors
    # Parse pairs of lines:
    #   * Label: <label>
    #          Title: <name>, Version: <ver>, Size: <bytes>...
    label_re = re.compile(r"^\s*\*\s*Label:\s*(.+)$")
    info_re = re.compile(r"Title:\s*(.+?),\s*Version:\s*([^,]+)")
    lines = stdout.splitlines()
    i = 0
    while i < len(lines):
        m = label_re.match(lines[i])
        if m and i + 1 < len(lines):
            info = info_re.search(lines[i + 1])
            if info:
                items.append({
                    "name":              info.group(1).strip(),
                    "current_version":   None,
                    "available_version": info.group(2).strip(),
                    "source":            "softwareupdate",
                })
            else:
                parse_errors.append(f"info line at {i+1}: {lines[i+1][:80]}")
            i += 2
        else:
            i += 1
    return items, parse_errors


def _run_apt(binary: str) -> tuple[list[dict], list[str]]:
    """apt list --upgradable lines:
        package/branch new-version [upgradable from: old-version]
    Stderr carries "WARNING: apt does not have a stable CLI" which
    we ignore."""
    rc, stdout, _stderr = _run_subprocess([binary, "list", "--upgradable"])
    # apt list returns 0 even when there are upgradables.
    if rc != 0:
        raise subprocess.CalledProcessError(rc, "apt", b"", _stderr.encode())
    items: list[dict] = []
    parse_errors: list[str] = []
    line_re = re.compile(
        r"^([^/\s]+)/[^\s]+\s+(\S+)\s+\S+\s+\[upgradable from:\s+([^\]]+)\]"
    )
    for line in stdout.splitlines():
        if line.startswith("Listing") or not line.strip():
            continue
        m = line_re.match(line)
        if m:
            items.append({
                "name":              m.group(1),
                "current_version":   m.group(3).strip(),
                "available_version": m.group(2).strip(),
                "source":            "apt",
            })
        else:
            parse_errors.append(f"unparsed: {line[:80]}")
    return items, parse_errors


def _run_dnf(binary: str) -> tuple[list[dict], list[str]]:
    """dnf check-update lines: ``name.arch  ver-rel  repo``.
    Exit 100 = updates available; 0 = none; anything else is an
    error."""
    rc, stdout, stderr = _run_subprocess([binary, "check-update"])
    if rc not in (0, 100):
        raise subprocess.CalledProcessError(rc, "dnf", b"", stderr.encode())
    items: list[dict] = []
    parse_errors: list[str] = []
    if rc == 0:
        return items, parse_errors
    line_re = re.compile(r"^(\S+)\.\S+\s+(\S+)\s+(\S+)$")
    for line in stdout.splitlines():
        if not line.strip() or line.startswith(("Last metadata", "Obsoleting")):
            continue
        m = line_re.match(line)
        if m:
            items.append({
                "name":              m.group(1),
                "current_version":   None,
                "available_version": m.group(2),
                "source":            f"dnf:{m.group(3)}",
            })
    return items, parse_errors
