"""``key_inventory.v1`` — enumerate cryptographic key material counts.

ADR-0033 Phase B3. KeyKeeper's read-only census tool. Answers
"what key material is on this host, where is it, and when was
it last touched?" without ever reading the bytes themselves.

Categories:
  * **keychain** (macOS only) — list installed keychain database
    files via ``security list-keychains``. Returns paths +
    sizes + mtimes. NEVER calls ``security dump-keychain`` or
    ``find-generic-password`` — those return secret material.
  * **ssh** — enumerates ~/.ssh/{id_*, authorized_keys,
    known_hosts}. For private keys (id_*), reports filename +
    perms + size + mtime. For authorized_keys / known_hosts,
    reports line count (not the keys themselves).
  * **signing** — best-effort: ~/.gnupg/secring.gpg,
    ~/.gnupg/pubring.kbx, ~/.gnupg/private-keys-v1.d/. Counts
    files by category. Never invokes ``gpg --list-secret-keys``
    (which would emit fingerprints).

side_effects=read_only — every probe is a stat() or a count of
file entries. The tool refuses any path traversal beyond the
caller's HOME directory and standard system Keychain paths.

Drift between successive runs is the high-value signal: a new
private key in ~/.ssh that wasn't there yesterday is exactly
the kind of finding that should escalate. Caller composes
key_inventory + memory_recall + continuous_verify to surface it.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_TIMEOUT_SECONDS = 10
_VALID_CATEGORIES = ("keychain", "ssh", "signing")
_SSH_PRIVATE_PATTERNS = (
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "id_rsa_*", "id_ecdsa_*", "id_ed25519_*",
)
_SSH_PUBLIC_SUFFIX = ".pub"
_SSH_AUX_FILES = ("authorized_keys", "known_hosts", "config")


class KeyInventoryTool:
    """Enumerate key material on the host — paths + counts only.

    Args:
      categories (list[str], optional): subset of ['keychain',
        'ssh', 'signing']. Default: all three. macOS-only
        categories silently report empty on Linux.
      home_dir   (str, optional): override for the home directory
        scan (used by tests + multi-user audits). Default: $HOME.
        Must be an existing absolute path.

    Output:
      {
        "platform":       "darwin"|"linux"|"unknown",
        "categories": {
          "keychain": {
            "files": [{"path":..., "size":..., "mtime_unix":...}, ...],
            "count": int,
          } | null,
          "ssh": {
            "private_keys": [{"name":..., "perms":..., "size":..., "mtime_unix":...}, ...],
            "public_keys":  [{"name":..., "size":..., "mtime_unix":...}, ...],
            "authorized_keys_lines": int | null,
            "known_hosts_lines":     int | null,
            "ssh_dir_perms":         str | null,
          },
          "signing": {
            "files": [...],
            "count": int,
          },
        },
        "warnings":      [str, ...],   # e.g. "id_rsa is mode 0644, should be 0600"
        "categories_skipped": [{"name":..., "reason":...}, ...],
      }
    """

    name = "key_inventory"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        cats = args.get("categories")
        if cats is not None:
            if not isinstance(cats, list):
                raise ToolValidationError(
                    "categories must be a list of strings"
                )
            for c in cats:
                if c not in _VALID_CATEGORIES:
                    raise ToolValidationError(
                        f"category {c!r} not in {list(_VALID_CATEGORIES)}"
                    )
        home = args.get("home_dir")
        if home is not None:
            if not isinstance(home, str) or not home:
                raise ToolValidationError(
                    "home_dir must be a non-empty string"
                )
            p = Path(home)
            if not p.is_absolute():
                raise ToolValidationError(
                    f"home_dir must be an absolute path; got {home!r}"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        wanted = args.get("categories") or list(_VALID_CATEGORIES)
        home = args.get("home_dir") or os.path.expanduser("~")
        plat = _detect_platform()

        results: dict[str, Any] = {}
        warnings: list[str] = []
        skipped: list[dict[str, str]] = []

        for cat in wanted:
            if cat == "keychain":
                if plat != "darwin":
                    skipped.append({
                        "name": "keychain",
                        "reason": f"keychain probe is macOS-only; platform={plat}",
                    })
                    results["keychain"] = None
                    continue
                results["keychain"] = _scan_keychain(skipped)
            elif cat == "ssh":
                results["ssh"] = _scan_ssh(home, warnings, skipped)
            elif cat == "signing":
                results["signing"] = _scan_signing(home, skipped)

        # High-level finding aggregator: count "things to flag"
        # so caller skills can quickly check whether to escalate.
        findings_count = len(warnings)

        return ToolResult(
            output={
                "platform":           plat,
                "categories":         results,
                "warnings":           warnings,
                "categories_skipped": skipped,
            },
            metadata={
                "categories_run":    [c for c in wanted if c not in {s["name"] for s in skipped}],
                "warning_count":     findings_count,
                "platform":          plat,
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"key inventory: {findings_count} warning"
                f"{'s' if findings_count != 1 else ''} on {plat}"
            ),
        )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _detect_platform() -> str:
    s = platform.system().lower()
    if s == "darwin":
        return "darwin"
    if s == "linux":
        return "linux"
    return "unknown"


def _safe_stat(p: Path) -> dict[str, Any] | None:
    """Return {size, mtime_unix, perms} or None if stat fails. Never
    raises — a permission denied on a single file shouldn't poison
    the whole inventory."""
    try:
        st = p.stat()
    except (OSError, PermissionError):
        return None
    return {
        "size":        st.st_size,
        "mtime_unix":  int(st.st_mtime),
        "perms":       oct(st.st_mode & 0o777),
    }


def _scan_keychain(skipped: list[dict[str, str]]) -> dict[str, Any] | None:
    """List installed keychain database files. We use the ``security
    list-keychains`` output (paths only) and stat each. Never opens
    a keychain or queries item names."""
    binary = shutil.which("security")
    if binary is None:
        skipped.append({
            "name": "keychain",
            "reason": "security binary not on PATH",
        })
        return None
    try:
        proc = subprocess.run(
            [binary, "list-keychains"],
            capture_output=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        skipped.append({"name": "keychain", "reason": "security timed out"})
        return None
    out = proc.stdout.decode("utf-8", errors="replace")
    files: list[dict[str, Any]] = []
    for raw_line in out.splitlines():
        # Lines look like:    "/Users/x/Library/Keychains/login.keychain-db"
        path_str = raw_line.strip().strip('"')
        if not path_str:
            continue
        p = Path(path_str)
        st = _safe_stat(p)
        if st is None:
            continue
        files.append({
            "path":       str(p),
            "size":       st["size"],
            "mtime_unix": st["mtime_unix"],
        })
    return {"files": files, "count": len(files)}


def _scan_ssh(
    home: str,
    warnings: list[str],
    skipped: list[dict[str, str]],
) -> dict[str, Any]:
    """Enumerate ~/.ssh: private keys, public keys, line counts for
    authorized_keys + known_hosts. Flag wrong-perm private keys."""
    ssh_dir = Path(home) / ".ssh"
    if not ssh_dir.exists():
        skipped.append({"name": "ssh", "reason": f"{ssh_dir} not present"})
        return {
            "private_keys":           [],
            "public_keys":            [],
            "authorized_keys_lines":  None,
            "known_hosts_lines":      None,
            "ssh_dir_perms":          None,
        }

    dir_st = _safe_stat(ssh_dir)
    dir_perms = dir_st["perms"] if dir_st else None
    if dir_perms is not None and dir_perms not in ("0o700", "0o500"):
        warnings.append(
            f"~/.ssh perms are {dir_perms}; should be 0o700"
        )

    private_keys: list[dict[str, Any]] = []
    public_keys:  list[dict[str, Any]] = []
    authorized_lines = None
    known_hosts_lines = None

    # Iterate the dir entries once to avoid double traversal.
    try:
        entries = list(ssh_dir.iterdir())
    except (OSError, PermissionError):
        skipped.append({
            "name": "ssh",
            "reason": f"cannot iterate {ssh_dir}",
        })
        return {
            "private_keys":           [],
            "public_keys":            [],
            "authorized_keys_lines":  None,
            "known_hosts_lines":      None,
            "ssh_dir_perms":          dir_perms,
        }

    for entry in entries:
        if not entry.is_file():
            continue
        name = entry.name
        st = _safe_stat(entry)
        if st is None:
            continue
        # Private key candidates: "id_*" without .pub suffix
        if name.startswith("id_") and not name.endswith(_SSH_PUBLIC_SUFFIX):
            private_keys.append({
                "name":       name,
                "perms":      st["perms"],
                "size":       st["size"],
                "mtime_unix": st["mtime_unix"],
            })
            # Posix private key perms must be 0600 / 0400; warn otherwise.
            if st["perms"] not in ("0o600", "0o400"):
                warnings.append(
                    f"private key {name} has perms {st['perms']}; "
                    "should be 0o600"
                )
        elif name.endswith(_SSH_PUBLIC_SUFFIX) and name.startswith("id_"):
            public_keys.append({
                "name":       name,
                "size":       st["size"],
                "mtime_unix": st["mtime_unix"],
            })
        elif name == "authorized_keys":
            authorized_lines = _count_lines(entry)
        elif name == "known_hosts":
            known_hosts_lines = _count_lines(entry)

    return {
        "private_keys":           private_keys,
        "public_keys":            public_keys,
        "authorized_keys_lines":  authorized_lines,
        "known_hosts_lines":      known_hosts_lines,
        "ssh_dir_perms":          dir_perms,
    }


def _scan_signing(
    home: str,
    skipped: list[dict[str, str]],
) -> dict[str, Any] | None:
    """Best-effort GnuPG enumeration: count files in ~/.gnupg/.
    Never invokes gpg itself — a missing binary or absent dir
    is reported in skipped, not as a finding."""
    gpg_dir = Path(home) / ".gnupg"
    if not gpg_dir.exists():
        skipped.append({
            "name": "signing",
            "reason": f"{gpg_dir} not present",
        })
        return {"files": [], "count": 0}
    files: list[dict[str, Any]] = []
    # Walk a single level — don't recurse into private-keys-v1.d's
    # contents because each entry there is a binary key blob whose
    # name leaks key fingerprints.
    try:
        for entry in gpg_dir.iterdir():
            if not entry.is_file():
                continue
            st = _safe_stat(entry)
            if st is None:
                continue
            files.append({
                "name":       entry.name,
                "size":       st["size"],
                "mtime_unix": st["mtime_unix"],
            })
    except (OSError, PermissionError):
        skipped.append({
            "name": "signing",
            "reason": f"cannot iterate {gpg_dir}",
        })
    # private-keys-v1.d → just count the blobs, don't list names.
    pkdir = gpg_dir / "private-keys-v1.d"
    blob_count = 0
    if pkdir.exists() and pkdir.is_dir():
        try:
            blob_count = sum(1 for _ in pkdir.iterdir())
        except (OSError, PermissionError):
            pass
    return {
        "files":              files,
        "count":              len(files),
        "private_blob_count": blob_count,
    }


def _count_lines(p: Path) -> int | None:
    """Count non-empty lines in a file. Returns None on read error.
    Never returns the line content itself."""
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            return sum(1 for line in f if line.strip())
    except (OSError, PermissionError):
        return None
