"""``usb_device_audit.v1`` — snapshot the USB device tree.

ADR-0033 Phase B1. Gatekeeper's USB control surface: a snapshot
of every USB device the host sees, so the operator notices when
something new gets plugged in (an unexpected mass-storage device,
a YubiKey moved to a different port, a serial-cable for some
embedded debug pad).

Backends:

  * **macOS**: ``system_profiler SPUSBDataType -json`` — emits a
    rich nested tree we flatten into a list
  * **Linux**: ``lsusb`` — text output parsed into the same shape

Each device record:
  ``{vendor_id, product_id, manufacturer, product, serial, location,
     speed, source}``

Vendor / product IDs are normalized to ``0xXXXX`` form so a baseline
diff doesn't tag superficial format differences as changes.

side_effects=read_only — both backends are query-only.
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


_BACKENDS = ("system_profiler", "lsusb")
_TIMEOUT_SECONDS = 30
_MAX_DEVICES = 500


class UsbDeviceAuditTool:
    """Snapshot the USB device tree.

    Args:
      backends (list[str], optional): subset of ['system_profiler',
        'lsusb']. Default: try system_profiler (macOS) then lsusb
        (Linux); first one whose binary exists wins.

    Output:
      {
        "devices": [
          {"vendor_id": str, "product_id": str,
           "manufacturer": str|null, "product": str|null,
           "serial": str|null, "location": str|null,
           "speed": str|null, "source": str},
          ...
        ],
        "count":        int,
        "truncated":    bool,
        "backend_used": str | null,
        "skipped":      [{"backend": str, "reason": str}, ...],
        "parse_errors": [str, ...]
      }
    """

    name = "usb_device_audit"
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
        order = args.get("backends") or list(_BACKENDS)
        skipped: list[dict[str, str]] = []
        parse_errors: list[str] = []
        devices: list[dict[str, Any]] = []
        backend_used: str | None = None

        for backend in order:
            binary = shutil.which(backend)
            if binary is None:
                skipped.append({"backend": backend, "reason": "binary_not_on_path"})
                continue
            try:
                if backend == "system_profiler":
                    found, perrs = _run_system_profiler(binary)
                elif backend == "lsusb":
                    found, perrs = _run_lsusb(binary)
                else:
                    continue
            except subprocess.TimeoutExpired:
                skipped.append({"backend": backend, "reason": "timeout"})
                continue
            backend_used = backend
            devices = found
            parse_errors = perrs
            break

        truncated = False
        if len(devices) > _MAX_DEVICES:
            devices = devices[:_MAX_DEVICES]
            truncated = True

        return ToolResult(
            output={
                "devices":      devices,
                "count":        len(devices),
                "truncated":    truncated,
                "backend_used": backend_used,
                "skipped":      skipped,
                "parse_errors": parse_errors,
            },
            metadata={"backends_tried": list(order)},
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"{len(devices)} USB device{'s' if len(devices) != 1 else ''} "
                f"via {backend_used or 'no_backend'}"
            ),
        )


def _run_subprocess(cmd: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd, capture_output=True, timeout=_TIMEOUT_SECONDS, check=False,
    )
    return (
        proc.returncode,
        proc.stdout.decode("utf-8", errors="replace"),
        proc.stderr.decode("utf-8", errors="replace"),
    )


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------
def _run_system_profiler(binary: str) -> tuple[list[dict], list[str]]:
    rc, stdout, stderr = _run_subprocess([binary, "SPUSBDataType", "-json"])
    if rc != 0:
        return [], [f"system_profiler exit={rc}: {stderr.strip()[:120]}"]
    try:
        data = json.loads(stdout) if stdout.strip() else {}
    except json.JSONDecodeError as e:
        return [], [f"json: {e}"]
    devices: list[dict] = []
    parse_errors: list[str] = []
    # The top-level shape is {"SPUSBDataType": [<root1>, <root2>, ...]}
    # where each root has nested _items children.
    for root in data.get("SPUSBDataType", []):
        _flatten_macos(root, devices, parse_errors, parent_name=None)
    return devices, parse_errors


def _flatten_macos(
    node: dict,
    out: list[dict],
    parse_errors: list[str],
    parent_name: str | None,
) -> None:
    """Recurse through SPUSBDataType, emitting one record per
    leaf device. Hubs that have child devices ARE emitted (they
    can be USB hubs in their own right that an attacker might
    swap)."""
    name = node.get("_name") or "unknown"
    vid = _normalize_id(node.get("vendor_id") or node.get("vendorID"))
    pid = _normalize_id(node.get("product_id") or node.get("productID"))
    if vid is not None and pid is not None:
        out.append({
            "vendor_id":    vid,
            "product_id":   pid,
            "manufacturer": node.get("manufacturer") or node.get("vendor_name"),
            "product":      name,
            "serial":       node.get("serial_num"),
            "location":     node.get("location_id"),
            "speed":        node.get("device_speed"),
            "source":       "system_profiler",
        })
    for child in node.get("_items", []) or []:
        _flatten_macos(child, out, parse_errors, parent_name=name)


def _normalize_id(raw: str | None) -> str | None:
    """Coerce vendor/product IDs into 0xXXXX form regardless of
    input shape ('apple_vendor_id', '0x05ac', '05ac', etc.)."""
    if raw is None:
        return None
    s = str(raw).strip()
    # Strip common suffixes Apple's profiler tacks on.
    m = re.search(r"0x([0-9a-fA-F]{4})", s)
    if m:
        return f"0x{m.group(1).lower()}"
    m = re.match(r"^([0-9a-fA-F]{4})$", s)
    if m:
        return f"0x{m.group(1).lower()}"
    # Symbolic vendor IDs ('apple_vendor_id') — keep as-is.
    return s


def _run_lsusb(binary: str) -> tuple[list[dict], list[str]]:
    """``lsusb`` lines: ``Bus 001 Device 003: ID 05ac:8262 Apple...``"""
    rc, stdout, stderr = _run_subprocess([binary])
    if rc != 0:
        return [], [f"lsusb exit={rc}: {stderr.strip()[:120]}"]
    devices: list[dict] = []
    parse_errors: list[str] = []
    line_re = re.compile(
        r"^Bus\s+(\d+)\s+Device\s+(\d+):\s+ID\s+([0-9a-f]{4}):([0-9a-f]{4})(?:\s+(.+))?$",
        re.IGNORECASE,
    )
    for line in stdout.splitlines():
        if not line.strip():
            continue
        m = line_re.match(line.strip())
        if not m:
            parse_errors.append(f"unparsed: {line[:80]}")
            continue
        product = (m.group(5) or "").strip() or None
        devices.append({
            "vendor_id":    f"0x{m.group(3).lower()}",
            "product_id":   f"0x{m.group(4).lower()}",
            "manufacturer": None,
            "product":      product,
            "serial":       None,
            "location":     f"bus={m.group(1)},device={m.group(2)}",
            "speed":        None,
            "source":       "lsusb",
        })
    return devices, parse_errors
