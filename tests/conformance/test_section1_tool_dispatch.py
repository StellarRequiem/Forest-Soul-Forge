"""Conformance §1 — Tool dispatch protocol.

Spec: docs/spec/kernel-api-v0.6.md §1.

These tests assert observable behavior of the dispatcher through its
HTTP wrapper (POST /agents/{id}/tools/call → DispatchOutcome). We
don't import ToolDispatcher directly — that would couple the
conformance suite to the reference Python implementation, defeating
the purpose of HTTP-only testing.
"""
from __future__ import annotations

import httpx
import pytest


# ----- §1 (general) — tool catalog reachable + non-empty -----------------


def test_section1_tool_catalog_reachable(client: httpx.Client) -> None:
    """§1: GET /tools returns the registered tool catalog.

    Per spec §5.3, this is a read endpoint and must respond 200 with a
    JSON body containing a ``tools`` list. The catalog count is
    spec-version-dependent; we assert non-zero.
    """
    resp = client.get("/tools")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "tools" in body, "response missing required 'tools' field"
    assert isinstance(body["tools"], list)
    assert len(body["tools"]) > 0, "tool catalog is empty"


def test_section1_tool_entry_shape(client: httpx.Client) -> None:
    """§1: each tool catalog entry has the documented shape.

    Per spec §1.4 + §5, the per-tool fields ``name`` and ``version``
    are part of the contract; ``side_effects`` is documented as one
    of {read_only, network, filesystem, external}.
    """
    body = client.get("/tools").json()
    side_effects_allowed = {"read_only", "network", "filesystem", "external"}
    for tool in body["tools"]:
        assert "name" in tool, f"tool missing 'name': {tool}"
        assert "version" in tool, f"tool missing 'version': {tool}"
        assert "side_effects" in tool, f"tool missing 'side_effects': {tool}"
        assert tool["side_effects"] in side_effects_allowed, (
            f"tool {tool['name']}.{tool['version']} has invalid side_effects "
            f"{tool['side_effects']!r}; spec §1 requires one of {side_effects_allowed}"
        )


def test_section1_mcp_call_v1_present(client: httpx.Client) -> None:
    """§1.4: mcp_call.v1 must be in the catalog.

    The spec calls this out as a v1.0 freeze surface — the dispatcher
    tool that routes to operator-registered MCP servers. Any kernel
    build claiming v0.6 conformance must register it.
    """
    body = client.get("/tools").json()
    matches = [t for t in body["tools"] if t["name"] == "mcp_call" and t["version"] == "1"]
    assert len(matches) == 1, "mcp_call.v1 not registered (spec §1.4 requires)"


# ----- §1.3 — governance pipeline observable refusal codes ---------------


def test_section1_unknown_tool_refusal_shape(client: httpx.Client) -> None:
    """§1.2 + §0.5: dispatching an unknown tool returns DispatchRefused
    with code='unknown-tool' (or HTTP 4xx with code in error envelope).

    We can't dispatch without a real agent, but we CAN probe the
    error envelope shape: a request for /agents/nonexistent-id/tools/call
    should return a structured 4xx with the documented envelope.
    """
    resp = client.post(
        "/agents/conformance-nonexistent-id/tools/call",
        json={
            "tool_name": "definitely_not_a_real_tool",
            "tool_version": "1",
            "args": {},
            "session_id": "conformance-probe",
            "tool_version_pin": "1",
        },
    )
    # Either 404 (agent not found, hits before tool lookup) or 422
    # (validation error, hits before dispatch). Both are documented in
    # spec §5.6's status × code table.
    assert resp.status_code in {400, 401, 403, 404, 422}, (
        f"unexpected status {resp.status_code}; spec §5.6 lists the "
        f"valid status codes for refused dispatches. Body: {resp.text[:200]}"
    )
    # Per spec §0.5 + §5.6, the error envelope MUST include 'detail'.
    body = resp.json()
    assert "detail" in body, f"error response missing 'detail' field: {body}"
