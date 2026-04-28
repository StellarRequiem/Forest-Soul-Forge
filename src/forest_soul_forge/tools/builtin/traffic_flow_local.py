"""``traffic_flow_local.v1`` — parse local OS flow tables.

ADR-0033 Phase B2. NetNinja's flow telemetry surface — captures
the current connection table (TCP + UDP, all states, not just
LISTEN like port_policy_audit) so lateral_movement_detect can
operate on (src, dst, port, proto, pid) records.

Backends:

  * **lsof** — ``lsof -i -P -n`` (macOS/BSD; all states)
  * **ss** — ``ss -tan`` + ``ss -uan`` (Linux; -a = all states,
              not just listening)

Output records have a uniform shape regardless of backend:
``{src, dst, src_port, dst_port, proto, state, pid, command,
user}``. ESTABLISHED + SYN_SENT + LISTEN all surface; TIME_WAIT
gets dropped by default (operator can opt in via include_timewait
arg) since it's noisy and rarely tells you anything actionable.

side_effects=read_only — same as port_policy_audit. The tool
reads the OS connection table; doesn't make connections of its
own.

Caps: 5000 flow records per call.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_BACKENDS = ("lsof", "ss")
_TIMEOUT_SECONDS = 30
_MAX_FLOWS = 5000


class TrafficFlowLocalTool:
    """Snapshot the OS flow table.

    Args:
      backends (list[str], optional): subset of ['lsof', 'ss'].
        Default: try lsof, then ss; first available wins.
      include_timewait (bool, optional): include TIME_WAIT entries.
        Default False (they're noisy and rarely useful).

    Output:
      {
        "flows": [
          {"src": str, "dst": str, "src_port": int, "dst_port": int,
           "proto": str, "state": str|null, "pid": int|null,
           "command": str|null, "user": str|null},
          ...
        ],
        "count":        int,
        "truncated":    bool,
        "backend_used": str | null,
        "skipped":      [{"backend": str, "reason": str}, ...],
        "parse_errors": [str, ...]
      }
    """

    name = "traffic_flow_local"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        backends = args.get("backends")
        if backends is not None:
            if not isinstance(backends, list):
                raise ToolValidationError("backends must be a list")
            for b in backends:
                if b not in _BACKENDS:
                    raise ToolValidationError(
                        f"backend {b!r} not in {list(_BACKENDS)}"
                    )
        if "include_timewait" in args and not isinstance(args["include_timewait"], bool):
            raise ToolValidationError(
                "include_timewait must be a boolean"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        order = args.get("backends") or list(_BACKENDS)
        include_timewait = bool(args.get("include_timewait", False))
        skipped: list[dict[str, str]] = []
        parse_errors: list[str] = []
        flows: list[dict[str, Any]] = []
        backend_used: str | None = None

        for backend in order:
            binary = shutil.which(backend)
            if binary is None:
                skipped.append({"backend": backend, "reason": "binary_not_on_path"})
                continue
            try:
                if backend == "lsof":
                    found, perrs = _run_lsof(binary, include_timewait)
                elif backend == "ss":
                    found, perrs = _run_ss(binary, include_timewait)
                else:
                    continue
            except subprocess.TimeoutExpired:
                skipped.append({"backend": backend, "reason": "timeout"})
                continue
            backend_used = backend
            flows = found
            parse_errors = perrs
            break

        truncated = False
        if len(flows) > _MAX_FLOWS:
            flows = flows[:_MAX_FLOWS]
            truncated = True

        return ToolResult(
            output={
                "flows":        flows,
                "count":        len(flows),
                "truncated":    truncated,
                "backend_used": backend_used,
                "skipped":      skipped,
                "parse_errors": parse_errors,
            },
            metadata={
                "backends_tried":   list(order),
                "include_timewait": include_timewait,
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"{len(flows)} flow{'s' if len(flows) != 1 else ''} "
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


def _split_addr_port(s: str) -> tuple[str, int | None]:
    if not s:
        return "", None
    if s.startswith("["):
        m = re.match(r"^\[(.+)\]:(\d+)$", s)
        if not m:
            return s, None
        return m.group(1), int(m.group(2))
    if ":" not in s:
        return s, None
    addr, _sep, port_str = s.rpartition(":")
    try:
        return addr, int(port_str)
    except ValueError:
        return s, None


def _run_lsof(binary: str, include_timewait: bool) -> tuple[list[dict], list[str]]:
    flows: list[dict] = []
    parse_errors: list[str] = []
    rc, stdout, stderr = _run_subprocess([binary, "-i", "-P", "-n"])
    if rc != 0 and not stdout.strip() and stderr.strip():
        parse_errors.append(f"lsof exit={rc}: {stderr.strip()[:120]}")
        return flows, parse_errors
    for line in stdout.splitlines()[1:]:
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        # Find proto token (TCP/UDP) by content
        proto = None
        idx = None
        for i, tok in enumerate(parts):
            if tok.upper() in ("TCP", "UDP"):
                proto = tok.lower()
                idx = i + 1
                break
        if proto is None or idx is None or idx >= len(parts):
            continue
        name_field = " ".join(parts[idx:])
        # NAME column shapes:
        #   "*:80 (LISTEN)"
        #   "127.0.0.1:443->10.0.0.5:54321 (ESTABLISHED)"
        state_m = re.search(r"\(([A-Z_]+)\)$", name_field)
        state = state_m.group(1) if state_m else None
        if state == "TIME_WAIT" and not include_timewait:
            continue
        addr_part = re.sub(r"\s*\([A-Z_]+\)$", "", name_field).strip()
        if "->" in addr_part:
            local, remote = addr_part.split("->", 1)
            src, src_port = _split_addr_port(local.strip())
            dst, dst_port = _split_addr_port(remote.strip())
        else:
            # LISTEN-style: "*:80" — only local side.
            src, src_port = _split_addr_port(addr_part)
            dst, dst_port = "", None
        flows.append({
            "src":      src, "dst": dst,
            "src_port": src_port, "dst_port": dst_port,
            "proto":    proto, "state": state,
            "pid":      pid, "command": parts[0], "user": parts[2],
        })
    return flows, parse_errors


def _run_ss(binary: str, include_timewait: bool) -> tuple[list[dict], list[str]]:
    flows: list[dict] = []
    parse_errors: list[str] = []
    for proto_flag, proto in (("-tanp", "tcp"), ("-uanp", "udp")):
        rc, stdout, stderr = _run_subprocess([binary, proto_flag])
        if rc != 0:
            parse_errors.append(f"ss {proto_flag} exit={rc}: {stderr.strip()[:80]}")
            continue
        for line in stdout.splitlines()[1:]:
            if not line.strip():
                continue
            cols = line.split()
            if len(cols) < 5:
                continue
            state = cols[0]
            if state == "TIME-WAIT" and not include_timewait:
                continue
            local = cols[3]
            remote = cols[4]
            src, src_port = _split_addr_port(local)
            dst, dst_port = _split_addr_port(remote) if remote != "*" else ("", None)
            users = cols[-1] if cols[-1].startswith("users:") else ""
            pid_m = re.search(r"pid=(\d+)", users)
            cmd_m = re.search(r'\(\("([^"]+)"', users)
            flows.append({
                "src":      src, "dst": dst,
                "src_port": src_port, "dst_port": dst_port,
                "proto":    proto, "state": state,
                "pid":      int(pid_m.group(1)) if pid_m else None,
                "command":  cmd_m.group(1) if cmd_m else None,
                "user":     None,
            })
    return flows, parse_errors
