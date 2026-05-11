"""Tests for the catalog_grants HTTP router — ADR-0060 T5 (Burst 222).

Exercises POST/DELETE/GET /agents/{instance_id}/tools/grant(s) end
to end via FastAPI's TestClient. Mirrors test_daemon_plugin_grants
so the test posture stays uniform across the two grant tables.

Coverage:
- POST happy path: grant fires + audit event + table row
- POST defaults trust_tier to 'yellow'
- POST 404: unknown agent
- POST 400: unknown tool in catalog (ADR-0060 D5)
- POST gating: refused when allow_write_endpoints=False
- POST re-issue: idempotent — last write wins
- DELETE happy path: revoke fires + audit event + table marks revoked
- DELETE idempotent: re-revoke returns no_op (not 404, per D3)
- DELETE 404: unknown agent
- GET active: returns active grants only
- GET history: includes revoked rows when ?history=true
- Audit event payload: agent_tool_granted carries trust_tier;
  agent_tool_revoked carries the original granted_at_seq.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forest_soul_forge.daemon.app import build_app
from forest_soul_forge.daemon.config import DaemonSettings


REPO_ROOT = Path(__file__).resolve().parents[2]
TRAIT_TREE = REPO_ROOT / "config" / "trait_tree.yaml"
CONST_TEMPLATES = REPO_ROOT / "config" / "constitution_templates.yaml"
TOOL_CATALOG = REPO_ROOT / "config" / "tool_catalog.yaml"
GENRES = REPO_ROOT / "config" / "genres.yaml"

# A tool that exists in the catalog. Use a builtin that no archetype
# implicitly grants so the test exercises the runtime-grant path
# cleanly. audit_chain_verify is read_only and present in catalog.
KNOWN_TOOL = "audit_chain_verify"
KNOWN_VERSION = "1"


def _build_settings(tmp_path: Path, *, allow_writes: bool = True) -> DaemonSettings:
    return DaemonSettings(
        registry_db_path=tmp_path / "registry.sqlite",
        artifacts_dir=tmp_path / "souls",
        audit_chain_path=tmp_path / "audit.jsonl",
        trait_tree_path=TRAIT_TREE,
        constitution_templates_path=CONST_TEMPLATES,
        soul_output_dir=tmp_path / "souls",
        tool_catalog_path=TOOL_CATALOG,
        genres_path=GENRES,
        default_provider="local",
        frontier_enabled=False,
        allow_write_endpoints=allow_writes,
        enrich_narrative_default=False,
        api_token=None,
        insecure_no_token=True,
    )


def _audit_events(app, event_type: str | None = None) -> list[dict]:
    chain_path = Path(app.state.audit_chain.path)
    events = []
    for line in chain_path.read_text().splitlines():
        if line.strip():
            events.append(json.loads(line))
    if event_type:
        events = [e for e in events if e.get("event_type") == event_type]
    return events


@pytest.fixture
def grant_env(tmp_path: Path):
    """Daemon with one agent born — the operator can grant catalog
    tools to it via the runtime-grant endpoints."""
    for p, name in [(TRAIT_TREE, "trait tree"),
                    (CONST_TEMPLATES, "constitution templates"),
                    (TOOL_CATALOG, "tool catalog")]:
        if not p.exists():
            pytest.skip(f"{name} missing at {p}")

    settings = _build_settings(tmp_path, allow_writes=True)
    app = build_app(settings)
    with TestClient(app) as client:
        resp = client.post("/birth", json={
            "profile": {
                "role": "translator",
                "trait_values": {},
                "domain_weight_overrides": {},
            },
            "agent_name": "TranslatorTest",
            "enrich_narrative": False,
        })
        assert resp.status_code in (200, 201), resp.text
        instance_id = resp.json()["instance_id"]

        yield {
            "client":      client,
            "app":         app,
            "instance_id": instance_id,
        }


# ===========================================================================
# POST — grant
# ===========================================================================

class TestGrantCatalogTool:
    def test_happy_path(self, grant_env):
        e = grant_env
        resp = e["client"].post(
            f"/agents/{e['instance_id']}/tools/grant",
            json={
                "tool_name": KNOWN_TOOL,
                "tool_version": KNOWN_VERSION,
                "trust_tier": "yellow",
                "reason": "initial grant",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        g = body["grant"]
        assert g["tool_name"] == KNOWN_TOOL
        assert g["tool_version"] == KNOWN_VERSION
        assert g["tool_key"] == f"{KNOWN_TOOL}.v{KNOWN_VERSION}"
        assert g["trust_tier"] == "yellow"
        assert g["is_active"] is True
        assert g["reason"] == "initial grant"

        evs = _audit_events(e["app"], "agent_tool_granted")
        assert len(evs) == 1
        ev = evs[0]["event_data"]
        assert ev["instance_id"] == e["instance_id"]
        assert ev["tool_name"] == KNOWN_TOOL
        assert ev["tool_version"] == KNOWN_VERSION
        assert ev["trust_tier"] == "yellow"
        assert ev["reason"] == "initial grant"

    def test_default_trust_tier_is_yellow(self, grant_env):
        """ADR-0060 D4 default: yellow. Operator must explicitly pass
        green for fully-autonomous tier."""
        e = grant_env
        resp = e["client"].post(
            f"/agents/{e['instance_id']}/tools/grant",
            json={"tool_name": KNOWN_TOOL, "tool_version": KNOWN_VERSION},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["grant"]["trust_tier"] == "yellow"

    def test_unknown_agent_404(self, grant_env):
        e = grant_env
        resp = e["client"].post(
            "/agents/does_not_exist/tools/grant",
            json={"tool_name": KNOWN_TOOL, "tool_version": KNOWN_VERSION},
        )
        assert resp.status_code == 404, resp.text

    def test_unknown_tool_in_catalog_returns_400(self, grant_env):
        """ADR-0060 D5: grants can only reference tools registered in
        the catalog. A hallucinated tool name refuses 400 before any
        chain emission so the grant can't sit in the audit trail
        as a stale reference."""
        e = grant_env
        resp = e["client"].post(
            f"/agents/{e['instance_id']}/tools/grant",
            json={"tool_name": "no_such_tool", "tool_version": "1"},
        )
        assert resp.status_code == 400, resp.text
        # No chain emission for the failed grant.
        assert _audit_events(e["app"], "agent_tool_granted") == []

    def test_invalid_trust_tier_rejected(self, grant_env):
        e = grant_env
        resp = e["client"].post(
            f"/agents/{e['instance_id']}/tools/grant",
            json={
                "tool_name": KNOWN_TOOL, "tool_version": KNOWN_VERSION,
                "trust_tier": "purple",
            },
        )
        # Pydantic 422 on regex mismatch.
        assert resp.status_code in (400, 422), resp.text

    def test_reissue_overwrites(self, grant_env):
        """Re-granting an already-active grant overwrites trust_tier
        and emits a fresh audit event each time."""
        e = grant_env
        r1 = e["client"].post(
            f"/agents/{e['instance_id']}/tools/grant",
            json={
                "tool_name": KNOWN_TOOL, "tool_version": KNOWN_VERSION,
                "trust_tier": "yellow",
            },
        )
        assert r1.status_code == 200
        r2 = e["client"].post(
            f"/agents/{e['instance_id']}/tools/grant",
            json={
                "tool_name": KNOWN_TOOL, "tool_version": KNOWN_VERSION,
                "trust_tier": "green", "reason": "trust ramp",
            },
        )
        assert r2.status_code == 200
        assert r2.json()["grant"]["trust_tier"] == "green"

        evs = _audit_events(e["app"], "agent_tool_granted")
        assert len(evs) == 2

    def test_blocked_when_writes_disabled(self, tmp_path: Path):
        """allow_write_endpoints=False -> 403."""
        settings = _build_settings(tmp_path, allow_writes=False)
        app = build_app(settings)
        with TestClient(app) as client:
            # birth path is also blocked when writes are disabled —
            # we can't seed an agent. The test target is the gate
            # itself, so call against a synthetic instance_id and
            # verify the 403 (not 404).
            resp = client.post(
                "/agents/x/tools/grant",
                json={"tool_name": KNOWN_TOOL, "tool_version": KNOWN_VERSION},
            )
            assert resp.status_code == 403


# ===========================================================================
# DELETE — revoke
# ===========================================================================

class TestRevokeCatalogTool:
    def test_happy_path(self, grant_env):
        e = grant_env
        # First grant.
        e["client"].post(
            f"/agents/{e['instance_id']}/tools/grant",
            json={"tool_name": KNOWN_TOOL, "tool_version": KNOWN_VERSION},
        )
        # Then revoke.
        resp = e["client"].request(
            "DELETE",
            f"/agents/{e['instance_id']}/tools/grant/{KNOWN_TOOL}/{KNOWN_VERSION}",
            json={"reason": "no longer needed"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert "revoked_at_seq" in body

        evs = _audit_events(e["app"], "agent_tool_revoked")
        assert len(evs) == 1
        ev = evs[0]["event_data"]
        assert ev["tool_name"] == KNOWN_TOOL
        assert ev["tool_version"] == KNOWN_VERSION
        assert ev["reason"] == "no longer needed"
        # The revoke event carries the original grant's seq for lineage.
        assert ev["granted_at_seq"] >= 1

    def test_idempotent_when_already_revoked(self, grant_env):
        """ADR-0060 D3: DELETE is idempotent. Re-revoking returns
        200 with no_op=True rather than 404."""
        e = grant_env
        e["client"].post(
            f"/agents/{e['instance_id']}/tools/grant",
            json={"tool_name": KNOWN_TOOL, "tool_version": KNOWN_VERSION},
        )
        e["client"].request(
            "DELETE",
            f"/agents/{e['instance_id']}/tools/grant/{KNOWN_TOOL}/{KNOWN_VERSION}",
        )
        # Second revoke is no-op.
        r2 = e["client"].request(
            "DELETE",
            f"/agents/{e['instance_id']}/tools/grant/{KNOWN_TOOL}/{KNOWN_VERSION}",
        )
        assert r2.status_code == 200
        assert r2.json().get("no_op") is True
        # Still only one revoke event in the chain.
        assert len(_audit_events(e["app"], "agent_tool_revoked")) == 1

    def test_idempotent_when_no_grant_ever_existed(self, grant_env):
        """No prior grant + DELETE = no_op (not 404). The agent
        exists; the grant doesn't."""
        e = grant_env
        resp = e["client"].request(
            "DELETE",
            f"/agents/{e['instance_id']}/tools/grant/{KNOWN_TOOL}/{KNOWN_VERSION}",
        )
        assert resp.status_code == 200
        assert resp.json().get("no_op") is True

    def test_unknown_agent_404(self, grant_env):
        """Unknown agent does 404 even though revoke itself is
        idempotent. The agent dimension is the authority check."""
        e = grant_env
        resp = e["client"].request(
            "DELETE",
            f"/agents/does_not_exist/tools/grant/{KNOWN_TOOL}/{KNOWN_VERSION}",
        )
        assert resp.status_code == 404


# ===========================================================================
# GET — list
# ===========================================================================

class TestListCatalogGrants:
    def test_empty_when_no_grants(self, grant_env):
        e = grant_env
        resp = e["client"].get(f"/agents/{e['instance_id']}/tools/grants")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0
        assert body["grants"] == []

    def test_active_filters_revoked_by_default(self, grant_env):
        e = grant_env
        # Grant + revoke.
        e["client"].post(
            f"/agents/{e['instance_id']}/tools/grant",
            json={"tool_name": KNOWN_TOOL, "tool_version": KNOWN_VERSION},
        )
        e["client"].request(
            "DELETE",
            f"/agents/{e['instance_id']}/tools/grant/{KNOWN_TOOL}/{KNOWN_VERSION}",
        )
        # Default list -> empty (revoked excluded).
        resp = e["client"].get(f"/agents/{e['instance_id']}/tools/grants")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_history_includes_revoked(self, grant_env):
        e = grant_env
        e["client"].post(
            f"/agents/{e['instance_id']}/tools/grant",
            json={"tool_name": KNOWN_TOOL, "tool_version": KNOWN_VERSION},
        )
        e["client"].request(
            "DELETE",
            f"/agents/{e['instance_id']}/tools/grant/{KNOWN_TOOL}/{KNOWN_VERSION}",
        )
        # history=true -> revoked rows appear.
        resp = e["client"].get(
            f"/agents/{e['instance_id']}/tools/grants?history=true",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["grants"][0]["is_active"] is False
        assert body["grants"][0]["revoked_at_seq"] is not None

    def test_unknown_agent_404(self, grant_env):
        e = grant_env
        resp = e["client"].get("/agents/does_not_exist/tools/grants")
        assert resp.status_code == 404
