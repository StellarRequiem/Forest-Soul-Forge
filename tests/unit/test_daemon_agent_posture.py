"""Tests for the agent_posture HTTP router — ADR-0045 T2 (Burst 114b).

Mirrors the plugin_grants test pattern. Coverage:
- GET /agents/{id}/posture: defaults to 'yellow' for newborn agents.
- POST /agents/{id}/posture: writes the column + emits
  agent_posture_changed audit with prior_posture captured.
- GET 404 unknown agent.
- POST 404 unknown agent.
- POST 422 invalid posture value.
- POST 403 when allow_write_endpoints=False.
- Idempotent set: setting the current value still emits the audit
  event (operator may want to record a re-affirmation).
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


def _settings(tmp_path: Path, *, allow_writes: bool = True) -> DaemonSettings:
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
        # B206: opt out of B148 auto-token-generation in unit tests so
        # the test fixtures don't need to thread X-FSF-Token through
        # every request. The api_token=None override is required
        # because pydantic-settings loads FSF_API_TOKEN from .env
        # automatically, and the constructor arg has to win over it.
        # Production daemons leave both at their defaults.
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
def posture_env(tmp_path: Path):
    for p, name in [(TRAIT_TREE, "trait tree"),
                    (CONST_TEMPLATES, "constitution templates"),
                    (TOOL_CATALOG, "tool catalog")]:
        if not p.exists():
            pytest.skip(f"{name} missing at {p}")

    app = build_app(_settings(tmp_path, allow_writes=True))
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
        yield {"client": client, "app": app, "instance_id": instance_id}


class TestGetPosture:
    def test_default_yellow(self, posture_env):
        e = posture_env
        resp = e["client"].get(f"/agents/{e['instance_id']}/posture")
        assert resp.status_code == 200, resp.text
        assert resp.json()["posture"] == "yellow"

    def test_unknown_agent_404(self, posture_env):
        resp = posture_env["client"].get("/agents/does_not_exist/posture")
        assert resp.status_code == 404


class TestSetPosture:
    def test_yellow_to_green(self, posture_env):
        e = posture_env
        resp = e["client"].post(
            f"/agents/{e['instance_id']}/posture",
            json={"posture": "green", "reason": "trust ramp after 100 successful dispatches"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["prior_posture"] == "yellow"
        assert body["posture"] == "green"

        # Audit event landed with prior_posture captured.
        evs = _audit_events(e["app"], "agent_posture_changed")
        assert len(evs) == 1
        ev = evs[0]["event_data"]
        assert ev["instance_id"] == e["instance_id"]
        assert ev["prior_posture"] == "yellow"
        assert ev["new_posture"] == "green"
        assert ev["reason"] == "trust ramp after 100 successful dispatches"

        # GET reflects the change.
        resp = e["client"].get(f"/agents/{e['instance_id']}/posture")
        assert resp.json()["posture"] == "green"

    def test_yellow_to_red(self, posture_env):
        e = posture_env
        resp = e["client"].post(
            f"/agents/{e['instance_id']}/posture",
            json={"posture": "red", "reason": "suspected hallucination"},
        )
        assert resp.status_code == 200
        assert resp.json()["posture"] == "red"

    def test_idempotent_re_set_emits_event(self, posture_env):
        """Re-setting the current value still emits an audit event.
        Operators may want to record a re-affirmation (e.g., 'reviewed
        and confirmed yellow') even when the column doesn't change."""
        e = posture_env
        e["client"].post(
            f"/agents/{e['instance_id']}/posture",
            json={"posture": "yellow", "reason": "review confirmed"},
        )
        evs = _audit_events(e["app"], "agent_posture_changed")
        assert len(evs) == 1
        assert evs[0]["event_data"]["prior_posture"] == "yellow"
        assert evs[0]["event_data"]["new_posture"] == "yellow"

    def test_unknown_agent_404(self, posture_env):
        resp = posture_env["client"].post(
            "/agents/does_not_exist/posture",
            json={"posture": "green"},
        )
        assert resp.status_code == 404

    def test_invalid_posture_422(self, posture_env):
        e = posture_env
        resp = e["client"].post(
            f"/agents/{e['instance_id']}/posture",
            json={"posture": "magenta"},
        )
        assert resp.status_code in (400, 422)

    def test_writes_disabled_returns_403(self, tmp_path: Path):
        if not (TRAIT_TREE.exists() and CONST_TEMPLATES.exists()
                and TOOL_CATALOG.exists()):
            pytest.skip("config files missing")
        s_open = _settings(tmp_path, allow_writes=True)
        app_open = build_app(s_open)
        with TestClient(app_open) as c_open:
            r = c_open.post("/birth", json={
                "profile": {"role": "network_watcher",
                            "trait_values": {},
                            "domain_weight_overrides": {}},
                "agent_name": "alpha",
            })
            instance_id = r.json()["instance_id"]
        s_closed = _settings(tmp_path, allow_writes=False)
        app_closed = build_app(s_closed)
        with TestClient(app_closed) as c_closed:
            r = c_closed.post(
                f"/agents/{instance_id}/posture",
                json={"posture": "green"},
            )
            assert r.status_code == 403
