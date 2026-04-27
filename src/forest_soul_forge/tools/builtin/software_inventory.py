"""``software_inventory.v1`` — installed-app snapshot.

ADR-0033 Phase B1. PatchPatrol uses this alongside file_integrity
to baseline the operator's machine and notice when something
new shows up between sweeps.

Backends probed (each conditional on its binary being on PATH):

  * **macos_apps** — walk ``/Applications`` + ``/System/Applications``;
    read each ``.app/Contents/Info.plist`` for ``CFBundleShortVersionString``
    via ``defaults read`` (no PyObjC dependency)
  * **brew** — ``brew list --versions`` (Homebrew formulae + casks)
  * **dpkg** — ``dpkg-query -W -f='${Package}\t${Version}\n'`` (Debian)
  * **rpm** — ``rpm -qa --qf '%{NAME}\t%{VERSION}-%{RELEASE}\n'`` (RPM)

Each backend's output is parsed into ``{name, version, source}``.
Missing backends are recorded in ``backends_skipped`` rather than
treated as an error — a Linux machine that has dpkg but not brew
is fine.

side_effects=read_only — every backend invocation is query-only.
The macos_apps walker only reads Info.plist; it never executes
the apps it discovers.

Cap: 5000 entries total per call. Past that the snapshot truncates
and reports ``truncated=true``. Operators with legitimate large
inventories chunk by backend or directory.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_BACKENDS = ("macos_apps", "brew", "dpkg", "rpm")
_TIMEOUT_SECONDS = 30
_MAX_ENTRIES = 5000


class SoftwareInventoryTool:
    """Inventory installed apps + packages.

    Args:
      backends (list[str], optional): subset of ['macos_apps', 'brew',
        'dpkg', 'rpm']. Default: probe all four. Only backends whose
        prerequisites exist are run.

    Output:
      {
        "items": [
          {"name": str, "version": str, "source": str}, ...
        ],
        "count":            int,
        "truncated":        bool,
        "backends_run":     [str, ...],
        "backends_skipped": [{"backend": str, "reason": str}, ...],
        "parse_errors":     [{"backend": str, "detail": str}, ...]
      }
    """

    name = "software_inventory"
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
        items: list[dict[str, str]] = []
        backends_run: list[str] = []
        backends_skipped: list[dict[str, str]] = []
        parse_errors: list[dict[str, str]] = []
        truncated = False

        for backend in wanted:
            if len(items) >= _MAX_ENTRIES:
                truncated = True
                backends_skipped.append({
                    "backend": backend, "reason": "max_entries_cap",
                })
                continue
            try:
                if backend == "macos_apps":
                    found, perrs, skip_reason = _run_macos_apps()
                elif backend == "brew":
                    found, perrs, skip_reason = _run_brew_list()
                elif backend == "dpkg":
                    found, perrs, skip_reason = _run_dpkg()
                elif backend == "rpm":
                    found, perrs, skip_reason = _run_rpm()
                else:  # unreachable — validate guards
                    continue
            except subprocess.TimeoutExpired:
                backends_skipped.append({
                    "backend": backend, "reason": "timeout",
                })
                continue

            if skip_reason is not None:
                backends_skipped.append({"backend": backend, "reason": skip_reason})
                continue

            backends_run.append(backend)
            for it in found:
                if len(items) >= _MAX_ENTRIES:
                    truncated = True
                    break
                items.append(it)
            for pe in perrs:
                parse_errors.append({"backend": backend, "detail": pe})

        return ToolResult(
            output={
                "items":            items,
                "count":            len(items),
                "truncated":        truncated,
                "backends_run":     backends_run,
                "backends_skipped": backends_skipped,
                "parse_errors":     parse_errors,
            },
            metadata={"backends_requested": list(wanted)},
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"{len(items)} entries across "
                f"{len(backends_run)} backend{'s' if len(backends_run) != 1 else ''}"
                + (" (truncated)" if truncated else "")
            ),
        )


# ---------------------------------------------------------------------------
# Backends — each returns (items, parse_errors, skip_reason).
# skip_reason None means "ran successfully (possibly empty)".
# ---------------------------------------------------------------------------
def _run_subprocess(cmd: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd, capture_output=True, timeout=_TIMEOUT_SECONDS, check=False,
    )
    return (
        proc.returncode,
        proc.stdout.decode("utf-8", errors="replace"),
        proc.stderr.decode("utf-8", errors="replace"),
    )


def _run_macos_apps() -> tuple[list[dict], list[str], str | None]:
    """Walk /Applications + /System/Applications. For each .app
    bundle, read CFBundleShortVersionString from Info.plist via
    `defaults read` (which ships with macOS, no PyObjC needed)."""
    defaults = shutil.which("defaults")
    if defaults is None:
        return [], [], "defaults_not_on_path"
    items: list[dict] = []
    parse_errors: list[str] = []
    roots = [Path("/Applications"), Path("/System/Applications")]
    if not any(r.exists() for r in roots):
        return items, parse_errors, "no_app_dirs"
    for root in roots:
        if not root.exists():
            continue
        try:
            entries = list(root.iterdir())
        except OSError as e:
            parse_errors.append(f"iterdir {root}: {e}")
            continue
        for entry in entries:
            if not entry.name.endswith(".app"):
                continue
            plist = entry / "Contents" / "Info.plist"
            if not plist.exists():
                parse_errors.append(f"missing Info.plist: {entry}")
                continue
            rc, stdout, _stderr = _run_subprocess([
                defaults, "read", str(plist), "CFBundleShortVersionString",
            ])
            version = stdout.strip() if rc == 0 else "unknown"
            items.append({
                "name":    entry.stem,
                "version": version,
                "source":  f"macos_apps:{root.name}",
            })
    return items, parse_errors, None


def _run_brew_list() -> tuple[list[dict], list[str], str | None]:
    binary = shutil.which("brew")
    if binary is None:
        return [], [], "brew_not_on_path"
    items: list[dict] = []
    parse_errors: list[str] = []
    rc, stdout, stderr = _run_subprocess([binary, "list", "--versions"])
    if rc != 0:
        return [], [], f"exit={rc}: {stderr.strip()[:120]}"
    for line in stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split(None, 1)
        if not parts:
            parse_errors.append(f"unparsed: {line[:80]}")
            continue
        items.append({
            "name":    parts[0],
            "version": parts[1] if len(parts) == 2 else "unknown",
            "source":  "brew",
        })
    return items, parse_errors, None


def _run_dpkg() -> tuple[list[dict], list[str], str | None]:
    binary = shutil.which("dpkg-query")
    if binary is None:
        return [], [], "dpkg-query_not_on_path"
    items: list[dict] = []
    parse_errors: list[str] = []
    rc, stdout, stderr = _run_subprocess([
        binary, "-W", "-f=${Package}\t${Version}\n",
    ])
    if rc != 0:
        return [], [], f"exit={rc}: {stderr.strip()[:120]}"
    for line in stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        if len(parts) != 2:
            parse_errors.append(f"unparsed: {line[:80]}")
            continue
        items.append({
            "name":    parts[0],
            "version": parts[1],
            "source":  "dpkg",
        })
    return items, parse_errors, None


def _run_rpm() -> tuple[list[dict], list[str], str | None]:
    binary = shutil.which("rpm")
    if binary is None:
        return [], [], "rpm_not_on_path"
    items: list[dict] = []
    parse_errors: list[str] = []
    rc, stdout, stderr = _run_subprocess([
        binary, "-qa", "--qf", "%{NAME}\t%{VERSION}-%{RELEASE}\n",
    ])
    if rc != 0:
        return [], [], f"exit={rc}: {stderr.strip()[:120]}"
    for line in stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        if len(parts) != 2:
            parse_errors.append(f"unparsed: {line[:80]}")
            continue
        items.append({
            "name":    parts[0],
            "version": parts[1],
            "source":  "rpm",
        })
    return items, parse_errors, None
