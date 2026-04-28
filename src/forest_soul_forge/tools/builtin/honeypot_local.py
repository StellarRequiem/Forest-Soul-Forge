"""``honeypot_local.v1`` — bind a TCP listener that logs connection attempts.

ADR-0033 Phase B3. The active reconnaissance signal: an attacker
scanning the host can't tell a real service from a fake one
without connecting, and any connection to a port that should
have nothing on it is by definition suspicious.

The tool binds a TCP socket on a chosen high port (default
2222), accepts connections for a bounded duration, sends an
optional banner string (e.g. a fake SSH version), captures
{timestamp, src_ip, src_port, banner_sent, bytes_received[:128]}
for each attempt, and closes. Output is the captured event list
plus stats. The operator's downstream skill scrapes these via
log_correlate / log_scan, or simply reads the JSONL log file.

side_effects=network — the tool binds a port (durable for the
window) AND emits banner bytes to whoever connects. For high-
tier agents the binding alone is enough to require approval.
The banner is operator-controlled — the tool does NOT default
to anything that could mislead a curious operator's own scan
into thinking they have a real SSH service running.

**Caps:**
  * port range 1024..65535 (no privileged ports — the helper
    pattern owns those)
  * duration_seconds ≤ 300 (5 min); minimum 1
  * max_connections ≤ 1000; minimum 1
  * banner ≤ 256 bytes
  * each captured payload truncated at 128 bytes (we don't want
    to store an attacker's full payload — that grows unbounded)

**Refusals:**
  * port already in use (bind fails) → ToolValidationError
  * port out of range → ToolValidationError

The tool runs the accept loop on the asyncio loop, so the agent
runtime stays responsive. Closes idle connections after 5
seconds — long enough to capture a banner-grab probe, short
enough that a deliberately-slow attacker can't hold the slot.
"""
from __future__ import annotations

import asyncio
import socket
import time
from datetime import datetime, timezone
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_MIN_PORT = 1024
_MAX_PORT = 65535
_MAX_DURATION = 300
_MIN_DURATION = 1
_MAX_CONNECTIONS = 1000
_MAX_BANNER_BYTES = 256
_MAX_PAYLOAD_BYTES = 128
_PER_CONN_TIMEOUT = 5.0


class HoneypotLocalTool:
    """Bind a TCP listener; capture connection attempts; report.

    Args:
      port              (int, required): TCP port. 1024..65535.
      duration_seconds  (int, required): how long to listen.
        1..300.
      banner            (str, optional): bytes to send on
        connect. ≤ 256 chars. Empty / omitted = silent.
      bind_host         (str, optional, default '127.0.0.1'):
        bind address. Loopback-only is the safe default; an
        operator who wants the honeypot reachable on the LAN
        sets '0.0.0.0' explicitly. The tool does not refuse
        other addresses but flags non-loopback in metadata.
      max_connections   (int, optional, default 100): hard cap
        on captured events. Once reached, listener closes
        early — prevents a flood from filling memory.

    Output:
      {
        "port":             int,
        "bind_host":        str,
        "duration_seconds": int,
        "started_at":       str (ISO-8601 UTC),
        "ended_at":         str (ISO-8601 UTC),
        "events": [
          {"timestamp": str, "src_ip": str, "src_port": int,
           "banner_sent": bool, "bytes_received_preview": str,
           "bytes_received_count": int},
          ...
        ],
        "event_count":      int,
        "ended_reason":     "duration"|"max_connections"|"error",
        "skipped":          [{"name":..., "reason":...}, ...],
      }
    """

    name = "honeypot_local"
    version = "1"
    side_effects = "network"

    def validate(self, args: dict[str, Any]) -> None:
        port = args.get("port")
        if not isinstance(port, int) or isinstance(port, bool):
            raise ToolValidationError(
                f"port must be an integer in [{_MIN_PORT}, {_MAX_PORT}]; got {port!r}"
            )
        if port < _MIN_PORT or port > _MAX_PORT:
            raise ToolValidationError(
                f"port must be in [{_MIN_PORT}, {_MAX_PORT}]; got {port}"
            )
        dur = args.get("duration_seconds")
        if not isinstance(dur, int) or isinstance(dur, bool):
            raise ToolValidationError(
                f"duration_seconds must be an integer in "
                f"[{_MIN_DURATION}, {_MAX_DURATION}]; got {dur!r}"
            )
        if dur < _MIN_DURATION or dur > _MAX_DURATION:
            raise ToolValidationError(
                f"duration_seconds must be in [{_MIN_DURATION}, {_MAX_DURATION}]; got {dur}"
            )
        banner = args.get("banner")
        if banner is not None:
            if not isinstance(banner, str):
                raise ToolValidationError("banner must be a string")
            if len(banner.encode("utf-8")) > _MAX_BANNER_BYTES:
                raise ToolValidationError(
                    f"banner must be ≤ {_MAX_BANNER_BYTES} bytes"
                )
        bind_host = args.get("bind_host")
        if bind_host is not None and not isinstance(bind_host, str):
            raise ToolValidationError("bind_host must be a string")
        max_conn = args.get("max_connections")
        if max_conn is not None:
            if not isinstance(max_conn, int) or isinstance(max_conn, bool):
                raise ToolValidationError("max_connections must be an integer")
            if max_conn < 1 or max_conn > _MAX_CONNECTIONS:
                raise ToolValidationError(
                    f"max_connections must be in [1, {_MAX_CONNECTIONS}]"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        port      = args["port"]
        dur       = args["duration_seconds"]
        banner    = args.get("banner") or ""
        bind_host = args.get("bind_host") or "127.0.0.1"
        max_conn  = int(args.get("max_connections", 100))

        events:  list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        if bind_host != "127.0.0.1":
            skipped.append({
                "name": "bind_warning",
                "reason": (
                    f"binding non-loopback host {bind_host!r}; honeypot "
                    "reachable beyond this machine. Confirm operator intent."
                ),
            })
        started_at = datetime.now(timezone.utc)

        # Pre-flight bind check so a port-in-use failure surfaces
        # as a clean ToolValidationError rather than crashing the
        # async server start.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((bind_host, port))
        except OSError as e:
            sock.close()
            raise ToolValidationError(
                f"honeypot_local.v1: cannot bind {bind_host}:{port} — {e}"
            )
        sock.close()

        ended_reason = "duration"
        deadline = time.time() + dur

        async def handle(reader, writer):
            nonlocal events
            if len(events) >= max_conn:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                return
            ts = datetime.now(timezone.utc).isoformat()
            peer = writer.get_extra_info("peername") or ("?", 0)
            src_ip, src_port = peer[0], peer[1]
            banner_sent = False
            if banner:
                try:
                    writer.write(banner.encode("utf-8"))
                    await writer.drain()
                    banner_sent = True
                except Exception:
                    pass
            payload = b""
            try:
                payload = await asyncio.wait_for(
                    reader.read(_MAX_PAYLOAD_BYTES + 1),
                    timeout=_PER_CONN_TIMEOUT,
                )
            except (asyncio.TimeoutError, Exception):
                pass
            events.append({
                "timestamp":             ts,
                "src_ip":                src_ip,
                "src_port":              src_port,
                "banner_sent":           banner_sent,
                "bytes_received_count":  len(payload),
                "bytes_received_preview": payload[:_MAX_PAYLOAD_BYTES].decode(
                    "utf-8", errors="replace",
                ),
            })
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

        try:
            server = await asyncio.start_server(handle, bind_host, port)
        except OSError as e:
            raise ToolValidationError(
                f"honeypot_local.v1: server start failed — {e}"
            )

        try:
            while time.time() < deadline:
                if len(events) >= max_conn:
                    ended_reason = "max_connections"
                    break
                # Sleep in short ticks so the cap check + deadline
                # check both fire promptly without busy-looping.
                await asyncio.sleep(0.1)
        finally:
            server.close()
            try:
                await server.wait_closed()
            except Exception:
                pass

        ended_at = datetime.now(timezone.utc)

        return ToolResult(
            output={
                "port":             port,
                "bind_host":        bind_host,
                "duration_seconds": dur,
                "started_at":       started_at.isoformat(),
                "ended_at":         ended_at.isoformat(),
                "events":           events,
                "event_count":      len(events),
                "ended_reason":     ended_reason,
                "skipped":          skipped,
            },
            metadata={
                "port":         port,
                "bind_host":    bind_host,
                "event_count":  len(events),
                "ended_reason": ended_reason,
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"honeypot {bind_host}:{port}: {len(events)} "
                f"connection{'s' if len(events) != 1 else ''} captured "
                f"(ended via {ended_reason})"
            ),
        )
