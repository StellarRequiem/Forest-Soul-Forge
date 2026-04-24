"""End-to-end tests for the daemon's read-only endpoints.

Skips the whole module if FastAPI / pydantic-settings / httpx aren't
installed (the [daemon] extra). When they ARE installed, the tests use
FastAPI's TestClient — no real network, no real model server. The local
provider is stubbed so we don't need Ollama running.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
pydantic_settings = pytest.importorskip("pydantic_settings")

from fastapi.testclient import TestClient

from forest_soul_forge.daemon.app import build_app
from forest_soul_forge.daemon.config import DaemonSettings
from forest_soul_forge.daemon.providers import (
    ProviderHealth,
    ProviderRegistry,
    ProviderStatus,
    TaskKind,
)


class _StubProvider:
    """Provider that never touches the network. Used to exercise the routes."""

    name = "local"

    def __init__(self) -> None:
        self._models = {k: "stub:latest" for k in TaskKind}

    async def complete(self, prompt, *, task_kind=TaskKind.CONVERSATION, **_):
        return f"[stub] {prompt}"

    async def healthcheck(self):
        return ProviderHealth(
            name="local",
            status=ProviderStatus.OK,
            base_url="http://stub",
            models=self._models,
            details={"loaded": ["stub:latest"], "missing": []},
            error=None,
        )


@pytest.fixture
def daemon_env(tmp_path: Path, monkeypatch):
    """Build a daemon app against a tmp registry seeded from tmp artifacts."""
    # Seed a minimal artifact + empty audit chain so the registry has
    # something to serve.
    soul = tmp_path / "a.soul.md"
    const_file = tmp_path / "a.constitution.yaml"
    const_file.write_text("# placeholder\n", encoding="utf-8")
    soul.write_text(
        "---\n"
        "schema_version: 1\n"
        "dna: aaaaaaaaaaaa\n"
        'dna_full: "' + ("a" * 64) + '"\n'
        "role: network_watcher\n"
        'agent_name: "StubWatcher"\n'
        'agent_version: "v1"\n'
        'generated_at: "2026-04-23 12:00:00Z"\n'
        'constitution_hash: "' + ("0" * 64) + '"\n'
        'constitution_file: "a.constitution.yaml"\n'
        "parent_dna: null\n"
        "spawned_by: null\n"
        "lineage: []\n"
        "lineage_depth: 0\n"
        "---\n"
        "\n# body\n",
        encoding="utf-8",
    )
    audit = tmp_path / "audit.jsonl"
    audit.write_text(
        json.dumps({
            "seq": 0,
            "timestamp": "2026-04-23T12:00:00Z",
            "prev_hash": "GENESIS",
            "entry_hash": "h0",
            "agent_dna": None,
            "event_type": "chain_created",
            "event_data": {},
        }) + "\n",
        encoding="utf-8",
    )

    db = tmp_path / "registry.sqlite"
    settings = DaemonSettings(
        registry_db_path=db,
        artifacts_dir=tmp_path,
        audit_chain_path=audit,
        default_provider="local",
        frontier_enabled=False,
    )
    app = build_app(settings)

    # Seed registry by rebuilding from artifacts.
    with TestClient(app) as client:
        # Lifespan has now run: app.state.registry and app.state.providers
        # are populated with the real objects. Replace the provider
        # registry with a stub so healthchecks don't try to reach Ollama.
        # (Doing this BEFORE the context manager is wrong — lifespan
        # overwrites app.state.providers on startup.)
        app.state.providers = ProviderRegistry(
            providers={"local": _StubProvider(), "frontier": _StubProvider()},
            default="local",
        )
        reg = app.state.registry
        reg.rebuild_from_artifacts(tmp_path, audit)
        yield client, app, tmp_path


class TestHealth:
    def test_healthz_reports_local_provider(self, daemon_env):
        client, app, _ = daemon_env
        resp = client.get("/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["schema_version"] == 1
        assert body["canonical_contract"] == "artifacts-authoritative"
        assert body["active_provider"] == "local"
        assert body["provider"]["status"] == "ok"


class TestAgents:
    def test_list_agents_returns_seeded_agent(self, daemon_env):
        client, _, _ = daemon_env
        resp = client.get("/agents")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["agents"][0]["agent_name"] == "StubWatcher"

    def test_list_agents_filtered_by_role(self, daemon_env):
        client, _, _ = daemon_env
        resp = client.get("/agents", params={"role": "network_watcher"})
        assert resp.status_code == 200
        assert resp.json()["count"] == 1
        resp2 = client.get("/agents", params={"role": "does_not_exist"})
        assert resp2.json()["count"] == 0

    def test_by_dna_returns_incarnations(self, daemon_env):
        client, _, _ = daemon_env
        resp = client.get("/agents/by-dna/aaaaaaaaaaaa")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_get_agent_404_on_unknown(self, daemon_env):
        client, _, _ = daemon_env
        resp = client.get("/agents/not-a-real-id")
        assert resp.status_code == 404

    def test_ancestors_404_on_unknown(self, daemon_env):
        client, _, _ = daemon_env
        resp = client.get("/agents/not-a-real-id/ancestors")
        assert resp.status_code == 404


class TestAudit:
    def test_audit_tail_returns_seeded_events(self, daemon_env):
        client, _, _ = daemon_env
        resp = client.get("/audit/tail", params={"n": 10})
        assert resp.status_code == 200
        body = resp.json()
        # Only genesis event — agent_dna is None so it was mirrored as-is.
        assert body["count"] == 1
        assert body["events"][0]["event_type"] == "chain_created"

    def test_audit_tail_n_bounds(self, daemon_env):
        client, _, _ = daemon_env
        resp = client.get("/audit/tail", params={"n": 0})
        # Pydantic validation: n must be >= 1
        assert resp.status_code == 422


class TestRuntime:
    def test_get_provider_returns_info(self, daemon_env):
        client, _, _ = daemon_env
        resp = client.get("/runtime/provider")
        assert resp.status_code == 200
        body = resp.json()
        assert body["active"] == "local"
        assert body["default"] == "local"
        assert set(body["known"]) == {"local", "frontier"}
        assert body["health"]["status"] == "ok"

    def test_put_provider_flips_active(self, daemon_env):
        client, _, _ = daemon_env
        resp = client.put("/runtime/provider", json={"provider": "frontier"})
        assert resp.status_code == 200
        assert resp.json()["active"] == "frontier"
        # Confirm via GET
        assert client.get("/runtime/provider").json()["active"] == "frontier"

    def test_put_provider_unknown_400(self, daemon_env):
        client, _, _ = daemon_env
        resp = client.put("/runtime/provider", json={"provider": "nonexistent"})
        assert resp.status_code == 400
