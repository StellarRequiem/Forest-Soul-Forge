"""End-to-end test for ``POST /agents/{id}/skills/run`` — ADR-0031 T2b.

Births a real network_watcher, drops a skill manifest into the
configured install dir, dispatches a single-step skill that calls
``timestamp_window.v1`` (built-in tool), checks the assembled output.

The skill's tool dispatch flows through the real ToolDispatcher so
this also exercises constraint resolution + counter + accounting +
genre floor for skill-driven dispatches.
"""
from __future__ import annotations

import textwrap
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


_SKILL_MANIFEST = textwrap.dedent("""
schema_version: 1
name: get_window
version: '1'
description: Compute a time window from a relative expression.
requires:
  - timestamp_window.v1
inputs:
  type: object
  required: [expr]
  properties:
    expr: {type: string}
steps:
  - id: window
    tool: timestamp_window.v1
    args:
      expression: ${inputs.expr}
output:
  start: ${window.start}
  end:   ${window.end}
  span:  ${window.span_seconds}
""").strip()


@pytest.fixture
def skill_run_env(tmp_path: Path):
    """Daemon with the real configs + a freshly born network_watcher +
    a get_window skill installed at the configured install dir."""
    for p, name in [(TRAIT_TREE, "trait tree"),
                    (CONST_TEMPLATES, "constitution templates"),
                    (TOOL_CATALOG, "tool catalog")]:
        if not p.exists():
            pytest.skip(f"{name} missing at {p}")

    skill_dir = tmp_path / "skills_installed"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "get_window.v1.yaml").write_text(
        _SKILL_MANIFEST, encoding="utf-8",
    )

    settings = DaemonSettings(
        registry_db_path=tmp_path / "registry.sqlite",
        artifacts_dir=tmp_path / "souls",
        audit_chain_path=tmp_path / "audit.jsonl",
        trait_tree_path=TRAIT_TREE,
        constitution_templates_path=CONST_TEMPLATES,
        soul_output_dir=tmp_path / "souls",
        tool_catalog_path=TOOL_CATALOG,
        genres_path=GENRES,
        skill_install_dir=skill_dir,
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
        resp = client.post("/birth", json={
            "profile": {
                "role": "network_watcher",
                "trait_values": {},
                "domain_weight_overrides": {},
            },
            "agent_name": "SkillRunWatcher",
            "agent_version": "v1",
            "owner_id": "test-owner",
        })
        assert resp.status_code == 201, resp.text
        instance_id = resp.json()["instance_id"]
        yield client, app, instance_id


class TestSkillsRunEndpoint:
    def test_succeeded_returns_assembled_output(self, skill_run_env):
        client, _, instance_id = skill_run_env
        resp = client.post(
            f"/agents/{instance_id}/skills/run",
            json={
                "skill_name": "get_window",
                "skill_version": "1",
                "session_id": "sess-skill-1",
                "inputs": {"expr": "last 5 minutes"},
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "succeeded"
        assert body["skill_name"] == "get_window"
        assert body["steps_executed"] == 1
        out = body["output"]
        assert out["span"] == 300
        assert out["start"]
        assert out["end"]

    def test_unknown_skill_404(self, skill_run_env):
        client, _, instance_id = skill_run_env
        resp = client.post(
            f"/agents/{instance_id}/skills/run",
            json={
                "skill_name": "nonexistent",
                "skill_version": "1",
                "session_id": "sess-x",
                "inputs": {},
            },
        )
        assert resp.status_code == 404
        assert "not installed" in resp.json()["detail"]

    def test_unknown_agent_404(self, skill_run_env):
        client, _, _ = skill_run_env
        resp = client.post(
            "/agents/no-such-agent/skills/run",
            json={
                "skill_name": "get_window",
                "skill_version": "1",
                "session_id": "sess-x",
                "inputs": {"expr": "last 1 minutes"},
            },
        )
        assert resp.status_code == 404

    def test_skill_step_failure_returns_failed_status(self, skill_run_env):
        client, app, instance_id = skill_run_env
        # Install a manifest that requires an args field the input
        # doesn't provide — arg resolution fails.
        bad_manifest = textwrap.dedent("""
        schema_version: 1
        name: bad_skill
        version: '1'
        description: Refers to a missing input.
        requires: [timestamp_window.v1]
        inputs:
          type: object
          properties: {present: {type: string}}
        steps:
          - id: w
            tool: timestamp_window.v1
            args:
              expression: ${inputs.missing}
        output: {}
        """).strip()
        skill_dir = Path(app.state.settings.skill_install_dir)
        (skill_dir / "bad_skill.v1.yaml").write_text(bad_manifest, encoding="utf-8")

        resp = client.post(
            f"/agents/{instance_id}/skills/run",
            json={
                "skill_name": "bad_skill",
                "skill_version": "1",
                "session_id": "sess-bad",
                "inputs": {"present": "x"},
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "failed"
        assert body["failed_step_id"] == "w"
        assert body["failure_reason"] == "expression_error"
