"""Hardware fingerprint — pin an agent to a "home" machine.

ADR-003X K6. Returns a stable, machine-derived 16-char hex fingerprint
that the constitution embeds when ``bind_to_hardware: true`` is set
at /birth. At lifespan the daemon checks each loaded agent's binding
against this machine's fingerprint; mismatched agents go on a
quarantine set and the dispatcher refuses to route tool calls for
them. Operator can deliberately re-bind on a new machine via
``POST /agents/{id}/hardware/unbind``.

Sources, in priority order:

1. macOS: ``ioreg -d2 -c IOPlatformExpertDevice`` → IOPlatformUUID.
   Stable across reboots, changes only on hardware swap.
2. Linux: ``/etc/machine-id`` (systemd) or ``/var/lib/dbus/machine-id``.
   Stable across reboots; regenerated on dd-clone via systemd-firstboot.
3. Fallback: ``platform.node()`` hostname. Weak — hostname can change
   without notifying the user — so this is logged + flagged.

We don't use the raw UUID; we SHA256 it and take the first 16 hex chars
(64 bits of entropy). That's:
  - short enough to read in a chronicle line
  - long enough to not collide across plausible operator fleets
  - preserves the original UUID's privacy (no machine-identifiable
    string ever lands in the audit chain or constitution YAML)

Cached per-process — first call shells out, subsequent calls are
in-memory. Operators changing the machine's identity mid-process is
not a supported flow.
"""
from __future__ import annotations

import hashlib
import platform
import shutil
import subprocess
from dataclasses import dataclass

_FINGERPRINT_CACHE: str | None = None
_SOURCE_CACHE: str | None = None


@dataclass(frozen=True)
class HardwareFingerprint:
    """The machine fingerprint + the source it came from.

    ``source`` is one of:
      ``macos_ioplatform``  — strong, hardware-derived
      ``linux_machine_id``  — strong, OS-derived but stable
      ``hostname_fallback`` — weak; flagged in audit + chronicle

    Operators can decide whether to accept ``hostname_fallback`` for
    binding (``allow_weak_binding=True`` in BirthRequest, default False).
    """

    fingerprint: str   # 16-char hex
    source: str        # see above


def compute_hardware_fingerprint(
    *, force_recompute: bool = False,
) -> HardwareFingerprint:
    """Return the machine fingerprint. Cached per-process unless
    ``force_recompute=True`` (used in tests)."""
    global _FINGERPRINT_CACHE, _SOURCE_CACHE
    if not force_recompute and _FINGERPRINT_CACHE is not None and _SOURCE_CACHE is not None:
        return HardwareFingerprint(_FINGERPRINT_CACHE, _SOURCE_CACHE)

    raw, source = _read_raw_identifier()
    fp = _hash_to_short(raw)
    _FINGERPRINT_CACHE = fp
    _SOURCE_CACHE = source
    return HardwareFingerprint(fp, source)


def reset_cache() -> None:
    """Clear the per-process cache. Tests use this to simulate a
    machine swap without re-importing the module."""
    global _FINGERPRINT_CACHE, _SOURCE_CACHE
    _FINGERPRINT_CACHE = None
    _SOURCE_CACHE = None


# ---------------------------------------------------------------------------
# Platform-specific source readers
# ---------------------------------------------------------------------------
def _read_raw_identifier() -> tuple[str, str]:
    """Try macOS → Linux → hostname fallback. Returns (raw_id, source_name)."""
    sysname = platform.system()
    if sysname == "Darwin":
        macos = _try_macos_ioplatform()
        if macos:
            return macos, "macos_ioplatform"
    if sysname == "Linux":
        linux = _try_linux_machine_id()
        if linux:
            return linux, "linux_machine_id"
    # Last-resort fallback. Not strong; documented as such.
    return platform.node() or "unknown-host", "hostname_fallback"


def _try_macos_ioplatform() -> str | None:
    """Read IOPlatformUUID via ioreg. Returns None on failure (e.g.
    sandboxed environment without /usr/sbin/ioreg)."""
    if shutil.which("ioreg") is None:
        return None
    try:
        # -d2 limits depth so output stays small; -c restricts to the
        # IOPlatformExpertDevice class which carries the UUID.
        out = subprocess.run(
            ["ioreg", "-d2", "-c", "IOPlatformExpertDevice"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if out.returncode != 0:
        return None
    # Match line like: "IOPlatformUUID" = "ABCDEF12-3456-7890-..."
    for raw_line in out.stdout.splitlines():
        if "IOPlatformUUID" not in raw_line:
            continue
        parts = raw_line.split('"')
        # Expected shape: ['  ', 'IOPlatformUUID', ' = ', '<uuid>', '']
        for i, tok in enumerate(parts):
            if tok == "IOPlatformUUID" and i + 2 < len(parts):
                uuid = parts[i + 2].strip()
                if uuid:
                    return uuid
    return None


def _try_linux_machine_id() -> str | None:
    """Read /etc/machine-id or /var/lib/dbus/machine-id. Returns None
    on failure (containers without systemd, missing files, etc.)."""
    from pathlib import Path
    for candidate in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
        try:
            text = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return None


def _hash_to_short(raw: str) -> str:
    """SHA256 → first 16 hex chars (64-bit fingerprint).

    We deliberately don't expose the raw machine identifier; only its
    hash. Same machine always hashes to the same fingerprint, but the
    hash is one-way — a chronicle entry showing the fingerprint can't
    be used to identify the machine externally.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Quarantine helpers
# ---------------------------------------------------------------------------
def fingerprint_matches(constitution_binding: str | None) -> bool:
    """True when the agent's stored binding matches this machine.

    Returns True if the agent has no binding (unbound agents are not
    quarantined). Returns False ONLY when the binding is set AND it
    doesn't match the current machine's fingerprint.
    """
    if not constitution_binding:
        return True
    here = compute_hardware_fingerprint().fingerprint
    return constitution_binding == here
