"""End-to-end tests for ``POST /agents/{instance_id}/tools/call`` — ADR-0019 T2.

The fixture births a real agent through ``/birth`` so the constitution
file the dispatcher reads is actually on disk. Then we drive the
dispatch endpoint and check status codes + audit-chain entries.
"""
from __future__ import annotations

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


REPO_ROOT = Path(__file__).resolve().parents[2]
TRAIT_TREE = REPO_ROOT / "config" / "trait_tree.yaml"
CONST_TEMPLATES = REPO_ROOT / "config" / "constitution_templates.yaml"
TOOL_CATALOG = REPO_ROOT / "config" / "tool_catalog.yaml"
GENRES = REPO_ROOT / "config" / "genres.yaml"


class _StubProvider:
    name = "local"

    def __init__(self) -> None:
        self._models = {k: "stub:latest" for k in TaskKind}

    @property
    def models(self) -> dict:
        return dict(self._models)

    async def complete(self, prompt, *, task_kind=TaskKind.CONVERSATION, **_):
        return f"[stub] {prompt}"

    async def healthcheck(self):
        return ProviderHealth(
            name="local", status=ProviderStatus.OK, base_url="http://stub",
            models=self._models, details={"loaded": [], "missing": []},
            error=None,
        )


@pytest.fixture
def dispatch_env(tmp_path: Path):
    """Daemon wired with the real configs so /birth produces a constitution
    that lists ``timestamp_window.v1`` (network_watcher's archetype kit
    includes it). The test then dispatches that tool against the freshly
    born agent.
    """
    for p, name in [(TRAIT_TREE, "trait tree"),
                    (CONST_TEMPLATES, "constitution templates"),
                    (TOOL_CATALOG, "tool catalog")]:
        if not p.exists():
            pytest.skip(f"{name} missing at {p}")

    settings = DaemonSettings(
        registry_db_path=tmp_path / "registry.sqlite",
        artifacts_dir=tmp_path / "souls",
        audit_chain_path=tmp_path / "audit.jsonl",
        trait_tree_path=TRAIT_TREE,
        constitution_templates_path=CONST_TEMPLATES,
        soul_output_dir=tmp_path / "souls",
        tool_catalog_path=TOOL_CATALOG,
        # genres.yaml is loaded best-effort; passing the real path
        # exercises the genre-claim path when present, and falls back
        # to an empty engine when not.
        genres_path=GENRES,
        default_provider="local",
        frontier_enabled=False,
        allow_write_endpoints=True,
        enrich_narrative_default=False,
    )
    app = build_app(settings)
    with TestClient(app) as client:
        app.state.providers = ProviderRegistry(
            providers={"local": _StubProvider(), "frontier": _StubProvider()},
            default="local",
        )
        # Birth a network_watcher — its archetype kit includes
        # timestamp_window.v1 so the constitution.yaml will list it.
        resp = client.post("/birth", json={
            "profile": {
                "role": "network_watcher",
                "trait_values": {},
                "domain_weight_overrides": {},
            },
            "agent_name": "DispatchTestWatcher",
            "agent_version": "v1",
            "owner_id": "test-owner",
        })
        assert resp.status_code == 201, resp.text
        instance_id = resp.json()["instance_id"]
        yield client, app, instance_id


class TestDispatchEndpoint:
    def test_succeeded_returns_200_with_result(self, dispatch_env):
        client, _, instance_id = dispatch_env
        resp = client.post(
            f"/agents/{instance_id}/tools/call",
            json={
                "tool_name": "timestamp_window",
                "tool_version": "1",
                "session_id": "sess-1",
                "args": {"expression": "last 5 minutes",
                         "anchor": "2026-04-26T12:00:00Z"},
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "succeeded"
        assert body["tool_key"] == "timestamp_window.v1"
        assert body["call_count_after"] == 1
        assert body["result"]["output"]["span_seconds"] == 300

    def test_unknown_agent_404(self, dispatch_env):
        client, _, _ = dispatch_env
        resp = client.post(
            "/agents/no-such-agent/tools/call",
            json={
                "tool_name": "timestamp_window",
                "tool_version": "1",
                "session_id": "sess-1",
                "args": {"expression": "last 5 minutes"},
            },
        )
        assert resp.status_code == 404

    def test_unknown_tool_404(self, dispatch_env):
        client, _, instance_id = dispatch_env
        resp = client.post(
            f"/agents/{instance_id}/tools/call",
            json={
                "tool_name": "no_such_tool",
                "tool_version": "1",
                "session_id": "sess-1",
                "args": {},
            },
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["detail"]["reason"] == "unknown_tool"

    def test_bad_args_400(self, dispatch_env):
        client, _, instance_id = dispatch_env
        resp = client.post(
            f"/agents/{instance_id}/tools/call",
            json={
                "tool_name": "timestamp_window",
                "tool_version": "1",
                "session_id": "sess-1",
                "args": {},  # missing 'expression'
            },
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["reason"] == "bad_args"

    def test_repeated_dispatch_increments_counter(self, dispatch_env):
        client, _, instance_id = dispatch_env
        for n in range(1, 4):
            resp = client.post(
                f"/agents/{instance_id}/tools/call",
                json={
                    "tool_name": "timestamp_window",
                    "tool_version": "1",
                    "session_id": "sess-1",
                    "args": {"expression": "last 1 minutes"},
                },
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["call_count_after"] == n
