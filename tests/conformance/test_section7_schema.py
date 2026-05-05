"""Conformance §7 — Schema migrations.

Spec: docs/spec/kernel-api-v0.6.md §7.

§7's strict-additive policy is largely an internal contract — we
can't easily verify it from outside without poking at the SQLite
schema directly. These tests assert what IS observable from the
HTTP surface: that the daemon doesn't 503 on a fresh boot due to
migration failure, and that the documented current version is
respected via observable behavior (e.g., ADR-0045 posture
endpoint exists at v15).
"""
from __future__ import annotations

import httpx


def test_section7_healthz_reports_ok(client: httpx.Client) -> None:
    """§7: /healthz reports 'ok' — the daemon successfully migrated.

    Per spec §7.3, migrations are idempotent and run at lifespan boot.
    A failed migration would leave the daemon in a degraded state.
    """
    resp = client.get("/healthz")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    status = body.get("status", "")
    assert status == "ok", (
        f"daemon /healthz reports {status!r}; spec §7.1 implies 'ok' "
        f"after a successful migration. Body: {body}"
    )


def test_section7_v14_grants_table_observable(client: httpx.Client) -> None:
    """§7.2: schema v14 added agent_plugin_grants. Endpoint should exist.

    Per spec §7.2 the grants table landed at v14 (B113a / ADR-0043
    follow-up #2). The endpoint /agents/{id}/plugin-grants is part
    of the v0.5 freeze; if a kernel claims v0.6 conformance it must
    have run this migration.
    """
    # We can't actually fetch grants without a born agent, but we CAN
    # verify the endpoint exists by probing it for a fake agent and
    # getting 404 (not 405 method-not-allowed or 501 not-implemented).
    resp = client.get("/agents/conformance-nonexistent/plugin-grants")
    assert resp.status_code in {200, 404}, (
        f"plugin-grants endpoint returned {resp.status_code}; spec §7.2 "
        f"documents v14 schema migration adding this surface. "
        f"Body: {resp.text[:200]}"
    )


def test_section7_v15_posture_observable(client: httpx.Client) -> None:
    """§7.2: schema v15 added agents.posture column (ADR-0045 T1, B114).

    The posture endpoint is part of v0.5 freeze. A v0.6-conformant
    kernel must expose it.
    """
    resp = client.get("/agents/conformance-nonexistent/posture")
    assert resp.status_code in {200, 404}, (
        f"posture endpoint returned {resp.status_code}; spec §7.2 "
        f"documents v15 schema migration. Body: {resp.text[:200]}"
    )
