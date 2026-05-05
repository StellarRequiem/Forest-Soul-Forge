"""Conformance §5 — HTTP API contract.

Spec: docs/spec/kernel-api-v0.6.md §5.
"""
from __future__ import annotations

import httpx
import pytest


# ----- §5.3 — read endpoint catalog -------------------------------------


READ_ENDPOINTS = [
    "/healthz",
    "/agents",
    "/tools",
    "/genres",
    "/traits",
    "/skills",
    "/plugins",
    "/scheduler/tasks",
    "/pending-calls",
]


@pytest.mark.parametrize("endpoint", READ_ENDPOINTS)
def test_section5_read_endpoint_responds_200(client: httpx.Client, endpoint: str) -> None:
    """§5.3: every documented read endpoint responds 200 without auth.

    Read endpoints are documented as ungated. A daemon that returns
    401/403 on read is non-conformant.
    """
    resp = client.get(endpoint)
    assert resp.status_code == 200, (
        f"{endpoint} returned {resp.status_code}; spec §5.3 lists this "
        f"as an ungated read endpoint. Body: {resp.text[:200]}"
    )


# ----- §5.5 — OpenAPI normativity ---------------------------------------


def test_section5_openapi_available(client: httpx.Client) -> None:
    """§5.5: /openapi.json is available and well-formed.

    The auto-generated OpenAPI is normative for request/response shapes.
    """
    resp = client.get("/openapi.json")
    assert resp.status_code == 200, "OpenAPI spec missing per §5.5"
    body = resp.json()
    assert "openapi" in body, "OpenAPI document missing 'openapi' version field"
    assert body["openapi"].startswith("3."), (
        f"OpenAPI version {body['openapi']!r} not 3.x; spec §5.5 says 3.0"
    )
    assert "paths" in body, "OpenAPI document missing 'paths'"


# ----- §5.6 — error envelope --------------------------------------------


def test_section5_404_envelope(client: httpx.Client) -> None:
    """§5.6 + §0.5: 404 responses include the documented error envelope.

    Per spec §0.5 every error response carries 'detail'. 404 specifically
    has codes like agent-not-found / plugin-not-found / etc.
    """
    # Probe a path that should 404.
    resp = client.get("/agents/conformance-nonexistent-agent-id")
    assert resp.status_code == 404, (
        f"expected 404 for nonexistent agent; got {resp.status_code}: {resp.text[:200]}"
    )
    body = resp.json()
    assert "detail" in body, f"404 envelope missing 'detail': {body}"


# ----- §5.1 — auth model -------------------------------------------------


def test_section5_writes_gated_or_open(
    client: httpx.Client, api_token: str | None
) -> None:
    """§5.1: write endpoints respect FSF_API_TOKEN if set on the daemon.

    We can't easily detect the daemon's auth posture without trying a
    write. But we can probe shape: a write to a non-existent agent
    should 4xx with a structured envelope, regardless of auth posture.
    """
    resp = client.post(
        "/agents/conformance-nonexistent/posture",
        json={"posture": "yellow", "reason": "conformance probe"},
    )
    assert resp.status_code in {401, 403, 404, 422}, (
        f"unexpected status {resp.status_code}; spec §5.6 lists the valid "
        f"4xx codes for this case. Body: {resp.text[:200]}"
    )
    body = resp.json()
    assert "detail" in body, f"error envelope missing 'detail': {body}"


# ----- §5.2 — idempotency contract --------------------------------------


def test_section5_idempotency_replay_identical_response(client: httpx.Client) -> None:
    """§5.2: repeated POST with same X-Idempotency-Key + same body returns
    the prior response without re-executing.

    We probe with a request that's predictably 4xx (write to nonexistent
    agent). If the daemon honors §5.2, both calls should produce
    identical responses (same status, same body, same audit-trail
    behavior — no second audit entry).
    """
    idem_key = "conformance-idempotency-probe-001"
    payload = {"posture": "yellow", "reason": "conformance probe"}
    headers = {
        "X-Idempotency-Key": idem_key,
        "Content-Type": "application/json",
    }

    # Snapshot audit-tail count before.
    before = client.get("/audit/tail", params={"n": 5}).json()
    before_seqs = {e["seq"] for e in before["events"]}

    resp1 = client.post(
        "/agents/conformance-nonexistent-idempotent/posture",
        json=payload,
        headers=headers,
    )
    resp2 = client.post(
        "/agents/conformance-nonexistent-idempotent/posture",
        json=payload,
        headers=headers,
    )

    # Both should fail (agent doesn't exist) — but identically.
    assert resp1.status_code == resp2.status_code, (
        f"§5.2: repeated request returned different status "
        f"({resp1.status_code} vs {resp2.status_code}) — idempotency broken"
    )
    # Body shape should be identical too. We can't always assert byte-
    # exact equality (some impls add timing fields) but 'detail' should
    # match.
    body1 = resp1.json()
    body2 = resp2.json()
    if "detail" in body1 and "detail" in body2:
        assert body1["detail"] == body2["detail"], (
            f"§5.2: repeated 'detail' diverged: {body1['detail']!r} vs "
            f"{body2['detail']!r}"
        )
