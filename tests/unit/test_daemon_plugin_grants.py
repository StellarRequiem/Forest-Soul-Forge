"""Tests for the plugin_grants HTTP router — ADR-0043 follow-up #2
operator surface (Burst 113b).

Exercises POST/DELETE/GET /agents/{instance_id}/plugin-grants end
to end via FastAPI's TestClient. Mirrors the test_daemon_memory_consents
pattern so the test posture stays uniform across mutating endpoints.

Coverage:
- POST happy path: grant fires + audit event + table row
- POST defaults trust_tier to 'yellow'
- POST 404: unknown agent
- POST gating: refused when allow_write_endpoints=False
- POST re-issue: idempotent semantics, last write wins
- DELETE happy path: revoke fires + audit event + table marks revoked
- DELETE 404: no active grant
- DELETE 404: unknown agent
- GET active: returns active grants only
- GET history: includes revoked rows when ?history=true
- Audit event payload: agent_plugin_granted carries trust_tier;
  agent_plugin_revoked carries prior_trust_tier.
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
    """Daemon with one agent born — the operator can grant plugins
    to it."""
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
                "role": "network_watcher",
                "trait_values": {},
                "domain_weight_overrides": {},
            },
            "agent_name": "alpha",
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

class TestGrantPlugin:
    def test_happy_path(self, grant_env):
        e = grant_env
        resp = e["client"].post(
            f"/agents/{e['instance_id']}/plugin-grants",
            json={"plugin_name": "github", "trust_tier": "yellow",
                  "reason": "initial install"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        g = body["grant"]
        assert g["plugin_name"] == "github"
        assert g["trust_tier"] == "yellow"
        assert g["is_active"] is True
        assert g["reason"] == "initial install"

        # Audit event fired.
        evs = _audit_events(e["app"], "agent_plugin_granted")
        assert len(evs) == 1
        # event_data is the payload dict.
        ev = evs[0]["event_data"]
        assert ev["instance_id"] == e["instance_id"]
        assert ev["plugin_name"] == "github"
        assert ev["trust_tier"] == "yellow"
        assert ev["reason"] == "initial install"

    def test_default_trust_tier_is_yellow(self, grant_env):
        e = grant_env
        resp = e["client"].post(
            f"/agents/{e['instance_id']}/plugin-grants",
            json={"plugin_name": "github"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["grant"]["trust_tier"] == "yellow"

    def test_unknown_agent_404(self, grant_env):
        e = grant_env
        resp = e["client"].post(
            "/agents/does_not_exist/plugin-grants",
            json={"plugin_name": "github"},
        )
        assert resp.status_code == 404, resp.text

    def test_invalid_trust_tier_rejected(self, grant_env):
        e = grant_env
        resp = e["client"].post(
            f"/agents/{e['instance_id']}/plugin-grants",
            json={"plugin_name": "github", "trust_tier": "purple"},
        )
        # Pydantic 422 on regex mismatch.
        assert resp.status_code in (400, 422), resp.text

    def test_reissue_overwrites(self, grant_env):
        """Re-granting an already-active grant overwrites trust_tier
        and emits a fresh audit event each time."""
        e = grant_env
        resp1 = e["client"].post(
            f"/agents/{e['instance_id']}/plugin-grants",
            json={"plugin_name": "github", "trust_tier": "yellow"},
        )
        assert resp1.status_code == 200
        resp2 = e["client"].post(
            f"/agents/{e['instance_id']}/plugin-grants",
            json={"plugin_name": "github", "trust_tier": "green",
                  "reason": "trust ramp"},
        )
        assert resp2.status_code == 200
        assert resp2.json()["grant"]["trust_tier"] == "green"

        # Two grant events, not one.
        evs = _audit_events(e["app"], "agent_plugin_granted")
        assert len(evs) == 2

    def test_writes_disabled_returns_403(self, tmp_path: Path):
        if not (TRAIT_TREE.exists() and CONST_TEMPLATES.exists()
                and TOOL_CATALOG.exists()):
            pytest.skip("config files missing")
        # First birth with writes ENABLED so we have an agent…
        s_open = _build_settings(tmp_path, allow_writes=True)
        app_open = build_app(s_open)
        with TestClient(app_open) as c_open:
            r = c_open.post("/birth", json={
                "profile": {"role": "network_watcher",
                            "trait_values": {},
                            "domain_weight_overrides": {}},
                "agent_name": "alpha",
            })
            assert r.status_code in (200, 201)
            instance_id = r.json()["instance_id"]
        # …then re-open the same registry with writes DISABLED and
        # try to grant. Should refuse at the gate, not hit the table.
        s_closed = DaemonSettings(
            registry_db_path=s_open.registry_db_path,
            artifacts_dir=s_open.artifacts_dir,
            audit_chain_path=s_open.audit_chain_path,
            trait_tree_path=TRAIT_TREE,
            constitution_templates_path=CONST_TEMPLATES,
            soul_output_dir=s_open.soul_output_dir,
            tool_catalog_path=TOOL_CATALOG,
            genres_path=GENRES,
            default_provider="local",
            frontier_enabled=False,
            allow_write_endpoints=False,
            enrich_narrative_default=False,
        )
        app_closed = build_app(s_closed)
        with TestClient(app_closed) as c_closed:
            r = c_closed.post(
                f"/agents/{instance_id}/plugin-grants",
                json={"plugin_name": "github"},
            )
            assert r.status_code == 403, r.text


# ===========================================================================
# DELETE — revoke
# ===========================================================================

class TestRevokePlugin:
    def test_happy_path(self, grant_env):
        e = grant_env
        # Grant then revoke.
        e["client"].post(
            f"/agents/{e['instance_id']}/plugin-grants",
            json={"plugin_name": "github", "trust_tier": "yellow"},
        )
        resp = e["client"].request(
            "DELETE",
            f"/agents/{e['instance_id']}/plugin-grants/github",
            json={"reason": "rotated keys"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["plugin_name"] == "github"

        # Audit event fired with prior_trust_tier captured.
        evs = _audit_events(e["app"], "agent_plugin_revoked")
        assert len(evs) == 1
        ev = evs[0]["event_data"]
        assert ev["instance_id"] == e["instance_id"]
        assert ev["plugin_name"] == "github"
        assert ev["prior_trust_tier"] == "yellow"
        assert ev["reason"] == "rotated keys"

    def test_no_active_grant_404(self, grant_env):
        e = grant_env
        resp = e["client"].request(
            "DELETE",
            f"/agents/{e['instance_id']}/plugin-grants/never-granted",
        )
        assert resp.status_code == 404, resp.text

    def test_unknown_agent_404(self, grant_env):
        e = grant_env
        resp = e["client"].request(
            "DELETE",
            "/agents/does_not_exist/plugin-grants/github",
        )
        assert resp.status_code == 404, resp.text


# ===========================================================================
# GET — list
# ===========================================================================

class TestListPluginGrants:
    def test_active_only_by_default(self, grant_env):
        e = grant_env
        e["client"].post(
            f"/agents/{e['instance_id']}/plugin-grants",
            json={"plugin_name": "github"},
        )
        e["client"].post(
            f"/agents/{e['instance_id']}/plugin-grants",
            json={"plugin_name": "brave"},
        )
        e["client"].request(
            "DELETE",
            f"/agents/{e['instance_id']}/plugin-grants/brave",
        )
        resp = e["client"].get(
            f"/agents/{e['instance_id']}/plugin-grants",
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        names = {g["plugin_name"] for g in body["grants"]}
        # Only github (active); brave was revoked.
        assert names == {"github"}
        assert body["count"] == 1

    def test_history_includes_revoked(self, grant_env):
        e = grant_env
        e["client"].post(
            f"/agents/{e['instance_id']}/plugin-grants",
            json={"plugin_name": "github"},
        )
        e["client"].request(
            "DELETE",
            f"/agents/{e['instance_id']}/plugin-grants/github",
        )
        resp = e["client"].get(
            f"/agents/{e['instance_id']}/plugin-grants?history=true",
        )
        assert resp.status_code == 200, resp.text
        grants = resp.json()["grants"]
        assert len(grants) == 1
        assert grants[0]["is_active"] is False

    def test_unknown_agent_404(self, grant_env):
        e = grant_env
        resp = e["client"].get(
            "/agents/does_not_exist/plugin-grants",
        )
        assert resp.status_code == 404, resp.text

    def test_get_is_ungated(self, tmp_path: Path):
        """GET should work even when allow_write_endpoints=False —
        same posture as /audit + /healthz + GET /plugins."""
        if not (TRAIT_TREE.exists() and CONST_TEMPLATES.exists()
                and TOOL_CATALOG.exists()):
            pytest.skip("config files missing")
        # Birth an agent under write-enabled mode, then re-open closed.
        s_open = _build_settings(tmp_path, allow_writes=True)
        app_open = build_app(s_open)
        with TestClient(app_open) as c_open:
            r = c_open.post("/birth", json={
                "profile": {"role": "network_watcher",
                            "trait_values": {},
                            "domain_weight_overrides": {}},
                "agent_name": "alpha",
            })
            instance_id = r.json()["instance_id"]
        s_closed = DaemonSettings(
            registry_db_path=s_open.registry_db_path,
            artifacts_dir=s_open.artifacts_dir,
            audit_chain_path=s_open.audit_chain_path,
            trait_tree_path=TRAIT_TREE,
            constitution_templates_path=CONST_TEMPLATES,
            soul_output_dir=s_open.soul_output_dir,
            tool_catalog_path=TOOL_CATALOG,
            genres_path=GENRES,
            default_provider="local",
            frontier_enabled=False,
            allow_write_endpoints=False,
            enrich_narrative_default=False,
        )
        app_closed = build_app(s_closed)
        with TestClient(app_closed) as c_closed:
            resp = c_closed.get(
                f"/agents/{instance_id}/plugin-grants",
            )
            # Should be 200 (GET is ungated).
            assert resp.status_code == 200, resp.text
