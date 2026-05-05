"""Conformance suite shared fixtures.

ADR-0044 P4. Tests are HTTP-only; no internal forest_soul_forge
imports. This conftest provides the daemon URL fixture + an httpx
client that points at it.

External integrators run the suite against their own daemon by
setting ``FSF_DAEMON_URL``. CI runs against ``localhost:7423`` by
default, the kernel's documented port per the spec §0.4.
"""
from __future__ import annotations

import os

import httpx
import pytest


@pytest.fixture(scope="session")
def daemon_url() -> str:
    """Daemon URL under test. Override via ``FSF_DAEMON_URL`` env var."""
    return os.environ.get("FSF_DAEMON_URL", "http://127.0.0.1:7423").rstrip("/")


@pytest.fixture(scope="session")
def api_token() -> str | None:
    """API token if the daemon under test requires auth (spec §5.1).

    Read from ``FSF_API_TOKEN`` per the spec's auth-fallback chain.
    Tests skip write-endpoint assertions cleanly if writes are gated
    and no token is provided.
    """
    return os.environ.get("FSF_API_TOKEN") or None


@pytest.fixture(scope="session")
def client(daemon_url: str, api_token: str | None) -> httpx.Client:
    """An httpx.Client pre-configured with the daemon URL + auth.

    Session-scoped so the TCP connection is reused across tests —
    matters for slow CI environments where each handshake adds
    latency.
    """
    headers = {}
    if api_token:
        headers["X-FSF-Token"] = api_token
    with httpx.Client(base_url=daemon_url, headers=headers, timeout=10.0) as c:
        yield c


@pytest.fixture(scope="session", autouse=True)
def _daemon_reachable(client: httpx.Client) -> None:
    """Refuse to run any conformance test against an unreachable daemon.

    Better to fail fast on `pytest tests/conformance/` than emit 50
    cascading errors when the operator forgot to bring up the daemon.
    """
    try:
        resp = client.get("/healthz")
    except httpx.ConnectError as exc:
        pytest.exit(
            f"Daemon at {client.base_url} is not reachable: {exc}\n"
            "See docs/runbooks/headless-install.md for how to bring up a kernel-only daemon.",
            returncode=2,
        )
    if resp.status_code != 200:
        pytest.exit(
            f"Daemon at {client.base_url}/healthz returned {resp.status_code}; "
            f"expected 200.\nResponse: {resp.text[:200]}",
            returncode=2,
        )
