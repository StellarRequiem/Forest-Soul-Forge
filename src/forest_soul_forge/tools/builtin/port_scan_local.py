"""``port_scan_local.v1`` — TCP/UDP scan against 127.0.0.1/lo only.

ADR-0033 Phase B2. NetNinja's active probe surface — actively
connect to a port range on the loopback interface to find
services that may be listening but weren't surfaced by a
read-only port_policy_audit (e.g. a service in a different
namespace, a process with restricted /proc visibility).

**Strict loopback-only.** Targets that don't resolve to 127.0.0.1
or ::1 are refused at validation time. Doing otherwise turns this
tool into an externally-targeted scanner — a category we don't
ship without explicit operator + adapter wiring (and a real
network engagement scope).

side_effects=network — the tool generates SYN packets even though
they don't leave the machine. Per the genre approval policy, this
is allowed at security_mid (network is the bar) but
security_high gates it through approval.

Caps:
  * Single target only (loopback)
  * 1024 ports max per call
  * 5-second total timeout per port
  * 100 ports/second max sweep rate (to be a polite citizen of the
    OS — kernel SYN-flood detection still fires at higher rates)
"""
from __future__ import annotations

import asyncio
import socket
import time
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_LOOPBACK_TARGETS = frozenset({"127.0.0.1", "::1", "localhost"})
_MAX_PORTS = 1024
_PER_PORT_TIMEOUT = 0.5  # seconds
_MAX_RATE_PORTS_PER_SECOND = 100


class PortScanLocalTool:
    """Probe TCP/UDP ports on the loopback interface.

    Args:
      target (str, optional): "127.0.0.1" (default), "::1", or
                               "localhost". Anything else refused.
      ports  (list[int], optional): explicit list of ports.
      port_range (object, optional): {start: int, end: int} (inclusive).
                                      Used when ports omitted.
                                      Default: {start:1, end:1024}.
      proto  (str, optional): "tcp" (default) | "udp". UDP scanning
                               is approximate — closed ports return
                               nothing rather than RST, so we treat
                               "no response after timeout" as
                               "filtered_or_open".

    Output:
      {
        "target": str,
        "proto":  str,
        "ports_scanned": int,
        "open":   [int, ...],
        "closed_count": int,
        "filtered_or_open_count": int,   # UDP; or TCP timeouts
        "error_count": int,
      }
    """

    name = "port_scan_local"
    version = "1"
    side_effects = "network"

    def validate(self, args: dict[str, Any]) -> None:
        target = args.get("target", "127.0.0.1")
        if target not in _LOOPBACK_TARGETS:
            raise ToolValidationError(
                f"target must be one of {sorted(_LOOPBACK_TARGETS)}; "
                f"port_scan_local refuses non-loopback scans by design"
            )
        ports = args.get("ports")
        port_range = args.get("port_range")
        if ports is not None:
            if not isinstance(ports, list) or not all(isinstance(p, int) for p in ports):
                raise ToolValidationError(
                    "ports must be a list of integers when provided"
                )
            if not all(1 <= p <= 65535 for p in ports):
                raise ToolValidationError(
                    "every port must be in 1..65535"
                )
            if len(ports) > _MAX_PORTS:
                raise ToolValidationError(
                    f"ports must be ≤ {_MAX_PORTS}; got {len(ports)}"
                )
        if port_range is not None:
            if not isinstance(port_range, dict):
                raise ToolValidationError(
                    "port_range must be a {start, end} mapping"
                )
            start = port_range.get("start")
            end = port_range.get("end")
            if not isinstance(start, int) or not isinstance(end, int):
                raise ToolValidationError(
                    "port_range.start and .end must be integers"
                )
            if not (1 <= start <= end <= 65535):
                raise ToolValidationError(
                    "port_range must satisfy 1 ≤ start ≤ end ≤ 65535"
                )
            if (end - start + 1) > _MAX_PORTS:
                raise ToolValidationError(
                    f"port_range size must be ≤ {_MAX_PORTS}; "
                    f"got {end - start + 1}"
                )
        proto = args.get("proto")
        if proto is not None and proto not in ("tcp", "udp"):
            raise ToolValidationError(
                f"proto must be 'tcp' or 'udp'; got {proto!r}"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        target = args.get("target", "127.0.0.1")
        proto = args.get("proto", "tcp")
        if "ports" in args:
            ports = list(args["ports"])
        else:
            pr = args.get("port_range") or {"start": 1, "end": 1024}
            ports = list(range(pr["start"], pr["end"] + 1))

        # Resolve target to a numeric address. localhost → 127.0.0.1.
        if target == "localhost":
            target = "127.0.0.1"

        open_ports: list[int] = []
        closed = 0
        filtered = 0
        errors = 0
        rate_window_start = time.monotonic()
        rate_count = 0

        for port in ports:
            # Rate limit: at most _MAX_RATE_PORTS_PER_SECOND. Track
            # against a sliding 1-second window.
            now = time.monotonic()
            if now - rate_window_start >= 1.0:
                rate_window_start = now
                rate_count = 0
            elif rate_count >= _MAX_RATE_PORTS_PER_SECOND:
                await asyncio.sleep(1.0 - (now - rate_window_start))
                rate_window_start = time.monotonic()
                rate_count = 0
            rate_count += 1

            if proto == "tcp":
                state = await _probe_tcp(target, port)
            else:
                state = await _probe_udp(target, port)
            if state == "open":
                open_ports.append(port)
            elif state == "closed":
                closed += 1
            elif state == "filtered":
                filtered += 1
            else:
                errors += 1

        return ToolResult(
            output={
                "target":                  target,
                "proto":                   proto,
                "ports_scanned":           len(ports),
                "open":                    open_ports,
                "closed_count":            closed,
                "filtered_or_open_count":  filtered,
                "error_count":             errors,
            },
            metadata={
                "rate_limit_pps": _MAX_RATE_PORTS_PER_SECOND,
                "per_port_timeout_seconds": _PER_PORT_TIMEOUT,
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"{proto} {target}: {len(open_ports)} open, "
                f"{closed} closed, {filtered} filtered/open, {errors} error"
            ),
        )


async def _probe_tcp(target: str, port: int) -> str:
    """connect_ex on a non-blocking socket. Returns 'open' on
    successful connect, 'closed' on RST, 'filtered' on timeout."""
    loop = asyncio.get_event_loop()
    try:
        future = asyncio.open_connection(host=target, port=port)
        reader, writer = await asyncio.wait_for(future, timeout=_PER_PORT_TIMEOUT)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return "open"
    except asyncio.TimeoutError:
        return "filtered"
    except ConnectionRefusedError:
        return "closed"
    except OSError:
        return "error"


async def _probe_udp(target: str, port: int) -> str:
    """Send a 0-byte UDP datagram; if we get an ICMP unreachable
    back fast, the kernel surfaces it as ConnectionRefusedError on
    recv and we call it 'closed'. No response within the timeout
    is ambiguous: 'filtered_or_open' (the standard UDP-scan caveat)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    sock.settimeout(_PER_PORT_TIMEOUT)
    try:
        try:
            sock.sendto(b"", (target, port))
        except OSError:
            return "error"
        # Try to read the response (ICMP unreachable surfaces here).
        loop = asyncio.get_event_loop()
        try:
            await asyncio.wait_for(
                loop.sock_recv(sock, 1024),
                timeout=_PER_PORT_TIMEOUT,
            )
            return "open"  # got data back
        except asyncio.TimeoutError:
            return "filtered"
        except ConnectionRefusedError:
            return "closed"
        except OSError:
            return "error"
    finally:
        sock.close()
