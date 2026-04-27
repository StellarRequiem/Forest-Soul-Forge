"""``port_policy_audit.v1`` — read-only enumeration of listening ports.

ADR-0033 Phase B1. Gatekeeper's primary surface: a snapshot of every
process on the machine that's accepting inbound connections, so the
operator (or an upstream skill) can spot a process that shouldn't
be there.

Backends:

  * macOS / BSD: ``lsof -i -P -n -sTCP:LISTEN`` and an analogous
    UDP probe via ``-iUDP``
  * Linux: ``ss -tlnp -unlp`` (preferred — modern netstat replacement)
            with a fallback to ``netstat -tlnp -ulnp`` if ss is absent

Each line is parsed into ``{proto, address, port, pid, command,
user}``. Lines whose shape doesn't match get reported in
parse_errors rather than silently dropped.

side_effects=read_only — every backend invocation is read-only.
``lsof`` doesn't need root for non-privileged sockets; processes
owned by other users may show up as ``user='?'`` and ``command='?'``
when the caller can't read /proc/<pid>. The tool reports what it
can see and notes what it couldn't.

Cap: 1000 listeners per call. A reasonable workstation has ≤ 50;
servers might hit the cap and need to chunk.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_BACKENDS = ("lsof", "ss", "netstat")
_TIMEOUT_SECONDS = 30
_MAX_LISTENERS = 1000


class PortPolicyAuditTool:
    """Enumerate listening TCP + UDP ports.

    Args:
      backends (list[str], optional): subset of ['lsof', 'ss',
        'netstat']. Default: try lsof, then ss, then netstat —
        first one whose binary exists wins. Setting this explicitly
        is useful for tests.

    Output:
      {
        "listeners": [
          {"proto": "tcp"|"udp", "address": str, "port": int,
           "pid": int|null, "command": str|null, "user": str|null}, ...
        ],
        "count":        int,
        "truncated":    bool,
        "backend_used": str | null,
        "skipped":      [{"backend": str, "reason": str}, ...],
        "parse_errors": [str, ...]
      }
    """

    name = "port_policy_audit"
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
        listeners: list[dict[str, Any]] = []
        backend_used: str | None = None

        for backend in order:
            binary = shutil.which(backend)
            if binary is None:
                skipped.append({"backend": backend, "reason": "binary_not_on_path"})
                continue
            try:
                if backend == "lsof":
                    found, perrs = _run_lsof(binary)
                elif backend == "ss":
                    found, perrs = _run_ss(binary)
                elif backend == "netstat":
                    found, perrs = _run_netstat(binary)
                else:
                    continue
            except subprocess.TimeoutExpired:
                skipped.append({"backend": backend, "reason": "timeout"})
                continue
            backend_used = backend
            listeners = found
            parse_errors = perrs
            break  # first successful backend wins

        truncated = False
        if len(listeners) > _MAX_LISTENERS:
            listeners = listeners[:_MAX_LISTENERS]
            truncated = True

        return ToolResult(
            output={
                "listeners":    listeners,
                "count":        len(listeners),
                "truncated":    truncated,
                "backend_used": backend_used,
                "skipped":      skipped,
                "parse_errors": parse_errors,
            },
            metadata={"backends_tried": list(order)},
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"{len(listeners)} listener{'s' if len(listeners) != 1 else ''} "
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
def _run_lsof(binary: str) -> tuple[list[dict], list[str]]:
    """``lsof -i -P -n`` lists all internet sockets. We filter for
    LISTEN (TCP) and (UDP-like) state suffix, matching the
    standard COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME
    columns. The NAME column carries ``host:port (LISTEN)`` for
    TCP listeners and bare ``host:port`` for UDP."""
    listeners: list[dict] = []
    parse_errors: list[str] = []
    # TCP listeners only — UDP via lsof is best-effort and noisy.
    rc, stdout, stderr = _run_subprocess([binary, "-i", "-P", "-n", "-sTCP:LISTEN"])
    if rc != 0 and not stdout.strip():
        # Some lsof builds exit 1 when nothing matches — treat
        # empty stdout as "no listeners," and only flag a hard
        # error when stderr looks substantive.
        if stderr.strip():
            parse_errors.append(f"lsof TCP exit={rc}: {stderr.strip()[:120]}")
            return listeners, parse_errors
    for line in stdout.splitlines()[1:]:  # skip header
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) < 4:
            parse_errors.append(f"short row: {line[:80]}")
            continue
        # parts[0]=COMMAND, parts[1]=PID, parts[2]=USER. The
        # protocol (TCP/UDP) and address can appear at varying
        # indices depending on whether DEVICE is shown — find
        # them by content rather than position. The protocol
        # token is "TCP" or "UDP" (case-insensitive); the next
        # token is "<addr>:<port>" or "[ipv6]:<port>".
        try:
            pid = int(parts[1])
        except ValueError:
            parse_errors.append(f"bad pid: {line[:80]}")
            continue
        proto = None
        addr_idx = None
        for i, tok in enumerate(parts):
            if tok.upper() in ("TCP", "UDP"):
                proto = tok.lower()
                addr_idx = i + 1
                break
        if proto is None or addr_idx is None or addr_idx >= len(parts):
            parse_errors.append(f"no proto token: {line[:80]}")
            continue
        # NAME column may span the rest (e.g. "*:22 (LISTEN)").
        name_field = " ".join(parts[addr_idx:])
        m = re.match(r"^(.*?)(?:\s*\(LISTEN\))?$", name_field)
        addr_port = m.group(1).strip() if m else name_field
        addr, port = _split_addr_port(addr_port)
        if port is None:
            parse_errors.append(f"unparsed addr:port: {name_field}")
            continue
        listeners.append({
            "proto":   proto,
            "address": addr,
            "port":    port,
            "pid":     pid,
            "command": parts[0],
            "user":    parts[2],
        })
    return listeners, parse_errors


def _run_ss(binary: str) -> tuple[list[dict], list[str]]:
    """``ss -tlnp -ulnp`` — the modern netstat replacement on Linux.
    -t TCP, -u UDP, -l listening, -n numeric, -p with-process."""
    listeners: list[dict] = []
    parse_errors: list[str] = []
    for proto_flag, proto in (("-tlnp", "tcp"), ("-ulnp", "udp")):
        rc, stdout, stderr = _run_subprocess([binary, proto_flag])
        if rc != 0:
            parse_errors.append(f"ss {proto_flag} exit={rc}: {stderr.strip()[:80]}")
            continue
        for line in stdout.splitlines()[1:]:
            if not line.strip():
                continue
            cols = line.split()
            if len(cols) < 5:
                parse_errors.append(f"ss short row: {line[:80]}")
                continue
            local = cols[3]  # "0.0.0.0:22" or "[::]:22"
            addr, port = _split_addr_port(local)
            if port is None:
                parse_errors.append(f"ss bad addr: {local}")
                continue
            # cols[-1] for ss looks like
            #   users:(("sshd",pid=1234,fd=3))
            users = cols[-1] if cols[-1].startswith("users:") else ""
            pid_m = re.search(r"pid=(\d+)", users)
            cmd_m = re.search(r'\(\("([^"]+)"', users)
            listeners.append({
                "proto":   proto,
                "address": addr,
                "port":    port,
                "pid":     int(pid_m.group(1)) if pid_m else None,
                "command": cmd_m.group(1) if cmd_m else None,
                "user":    None,
            })
    return listeners, parse_errors


def _run_netstat(binary: str) -> tuple[list[dict], list[str]]:
    """Fallback for ancient Linux: ``netstat -tlnp -ulnp``."""
    listeners: list[dict] = []
    parse_errors: list[str] = []
    for proto_flag, proto in (("-tlnp", "tcp"), ("-ulnp", "udp")):
        rc, stdout, stderr = _run_subprocess([binary, proto_flag])
        if rc != 0:
            parse_errors.append(f"netstat {proto_flag} exit={rc}: {stderr.strip()[:80]}")
            continue
        for line in stdout.splitlines():
            if not line.startswith(proto):
                continue
            cols = line.split()
            if len(cols) < 7:
                parse_errors.append(f"netstat short row: {line[:80]}")
                continue
            local = cols[3]
            addr, port = _split_addr_port(local)
            if port is None:
                parse_errors.append(f"netstat bad addr: {local}")
                continue
            pid_cmd = cols[6]  # "1234/sshd" or "-"
            pid = None
            command = None
            if pid_cmd != "-" and "/" in pid_cmd:
                p, c = pid_cmd.split("/", 1)
                try:
                    pid = int(p)
                except ValueError:
                    pass
                command = c
            listeners.append({
                "proto":   proto,
                "address": addr,
                "port":    port,
                "pid":     pid,
                "command": command,
                "user":    None,
            })
    return listeners, parse_errors


def _split_addr_port(s: str) -> tuple[str, int | None]:
    """Parse an "addr:port" pair handling IPv6 [::]:22 and bare 0.0.0.0:22."""
    if not s:
        return "", None
    if s.startswith("["):
        # IPv6: [::]:22
        m = re.match(r"^\[(.+)\]:(\d+)$", s)
        if not m:
            return s, None
        return m.group(1), int(m.group(2))
    # IPv4 / hostname
    if ":" not in s:
        return s, None
    addr, sep, port_str = s.rpartition(":")
    try:
        return addr, int(port_str)
    except ValueError:
        return s, None
