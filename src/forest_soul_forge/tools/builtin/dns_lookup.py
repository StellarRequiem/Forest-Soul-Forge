"""``dns_lookup.v1`` — forward / reverse DNS resolution via stdlib socket.

Catalog entry: see ``config/tool_catalog.yaml`` line ``dns_lookup.v1``.

This tool was originally specified in ADR-0018 alongside the network_watcher
archetype kit but never landed an implementation in v0.1. The 2026-04-30
zombie-tool dissection (see ``docs/audits/2026-04-30-c1-zombie-tool-dissection.md``)
tagged it as IMPLEMENT — DNS resolution is a foundational primitive with
no clean substitute in the current catalog, and operators doing network
observation, threat hunting, or web research need it.

Side effects: ``network`` — issues a real UDP request to the configured
resolver. No other reach. Per-tool ``requires_human_approval`` is left
to constraint policy (forwarded resolver, single packet — usually safe;
operators can elevate if they want approval-gated resolution).

Args:
  hostname (str, optional): hostname to resolve forward (A record).
  ip       (str, optional): IPv4/IPv6 address to resolve reverse (PTR record).
  timeout  (int, optional): socket timeout in seconds. 1-30. Default 5.

Exactly ONE of ``hostname`` or ``ip`` must be supplied. Both, or neither,
is a validation error.

Output:
  {
    "query":      str,        # the input value
    "kind":       str,        # "forward" | "reverse"
    "answer":     str,        # resolved IP or hostname
    "elapsed_ms": int,        # round-trip time
  }

Notes on intentional scope limits:
  * Only A / PTR records. No MX, TXT, CNAME, SRV, etc. Multi-record
    resolution is a v0.3+ candidate.
  * Single-resolver — uses whatever resolver the OS is configured with.
    Custom-resolver / DNS-over-HTTPS variants are not in scope here.
  * Returns the FIRST answer only. ``socket.gethostbyname`` collapses
    multi-IP records to one. Operators who need full record sets should
    wait for a v2 with ``socket.getaddrinfo``.
"""
from __future__ import annotations

import socket
import time
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_MIN_TIMEOUT = 1
_MAX_TIMEOUT = 30
_DEFAULT_TIMEOUT = 5
_MAX_HOSTNAME_LEN = 253        # RFC 1035 cap
_MAX_IP_LEN = 45               # IPv6 max ('::ffff:255.255.255.255' style)


class DnsLookupError(Exception):
    """Wraps stdlib ``socket.gaierror`` / ``socket.herror`` for the dispatcher."""


class DnsLookupTool:
    """Forward / reverse DNS resolution. See module docstring for full args."""

    name = "dns_lookup"
    version = "1"
    side_effects = "network"

    def validate(self, args: dict[str, Any]) -> None:
        hostname = args.get("hostname")
        ip = args.get("ip")
        timeout = args.get("timeout", _DEFAULT_TIMEOUT)

        # Exactly one of hostname / ip must be supplied.
        if hostname is None and ip is None:
            raise ToolValidationError(
                "dns_lookup.v1 requires either ``hostname`` or ``ip``; got neither"
            )
        if hostname is not None and ip is not None:
            raise ToolValidationError(
                "dns_lookup.v1 takes ``hostname`` OR ``ip``, not both"
            )

        if hostname is not None:
            if not isinstance(hostname, str):
                raise ToolValidationError(
                    f"hostname must be a string; got {type(hostname).__name__}"
                )
            if not hostname.strip():
                raise ToolValidationError("hostname must be non-empty")
            if len(hostname) > _MAX_HOSTNAME_LEN:
                raise ToolValidationError(
                    f"hostname exceeds RFC 1035 max ({_MAX_HOSTNAME_LEN} chars); "
                    f"got {len(hostname)}"
                )

        if ip is not None:
            if not isinstance(ip, str):
                raise ToolValidationError(
                    f"ip must be a string; got {type(ip).__name__}"
                )
            if not ip.strip():
                raise ToolValidationError("ip must be non-empty")
            if len(ip) > _MAX_IP_LEN:
                raise ToolValidationError(
                    f"ip exceeds {_MAX_IP_LEN}-char max; got {len(ip)}"
                )
            # Cheap shape check — defer the real validation to socket.
            # We don't want to reimplement IPv4/IPv6 parsing here. The
            # socket call returns gaierror for malformed input which we
            # translate into a clean ToolValidationError below.
            if not any(c in ip for c in ".:"):
                raise ToolValidationError(
                    f"ip must look like an address (contain '.' or ':'); got {ip!r}"
                )

        if not isinstance(timeout, int) or timeout < _MIN_TIMEOUT or timeout > _MAX_TIMEOUT:
            raise ToolValidationError(
                f"timeout must be an integer in [{_MIN_TIMEOUT}, {_MAX_TIMEOUT}]; "
                f"got {timeout!r}"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        hostname = args.get("hostname")
        ip = args.get("ip")
        timeout = int(args.get("timeout", _DEFAULT_TIMEOUT))

        # socket module has no public per-call timeout for gethostbyname;
        # the global ``socket.setdefaulttimeout`` is the documented path.
        # Stash + restore so we don't leak a process-wide timeout change.
        prior_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(float(timeout))

        t0 = time.perf_counter()
        try:
            if hostname is not None:
                # Forward lookup: hostname → IP. RFC 1035 A-record.
                kind = "forward"
                try:
                    answer = socket.gethostbyname(hostname)
                except (socket.gaierror, socket.herror) as e:
                    raise ToolValidationError(
                        f"forward lookup failed for {hostname!r}: {e}"
                    ) from e
                query = hostname
            else:
                # Reverse lookup: IP → hostname. RFC 1035 PTR-record.
                kind = "reverse"
                try:
                    answer = socket.gethostbyaddr(ip)[0]
                except (socket.gaierror, socket.herror) as e:
                    raise ToolValidationError(
                        f"reverse lookup failed for {ip!r}: {e}"
                    ) from e
                query = ip
        finally:
            # Always restore the prior timeout so we don't bleed into
            # adjacent calls (other tools, other agents).
            socket.setdefaulttimeout(prior_timeout)

        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        return ToolResult(
            output={
                "query":      query,
                "kind":       kind,
                "answer":     answer,
                "elapsed_ms": elapsed_ms,
            },
            metadata={
                "timeout_used": timeout,
            },
            tokens_used=None,
            cost_usd=None,
            side_effect_summary=(
                f"dns_lookup: {kind} {query!r} -> {answer!r} ({elapsed_ms}ms)"
            ),
        )
