"""End-to-end tests for the Prompt-Tool Forge HTTP surface — ADR-0058 / B202.

Mirrors the shape of test_daemon_skills_forge.py but for prompt-template
tools. The forged tool gets registered live in app.state.tool_registry
on install — proves the dispatcher can reach it without a daemon
restart. Lifespan walk of data/forge/tools/installed/ tested in a
separate fixture.
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

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

API_TOKEN = "test-token-tools-forge"


_VALID_SPEC = textwrap.dedent("""
name: summarize_audit
version: '1'
description: Summarize the most recent N audit chain entries.
input_schema:
  type: object
  required: [n_entries]
  properties:
    n_entries: {type: integer, minimum: 1, maximum: 100}
prompt_template: |
  Summarize the following {n_entries} audit chain entries.
archetype_tags: [observer, communicator]
""").strip()


_INVALID_SPEC = textwrap.dedent("""
name: !!badname with spaces
this isn't valid YAML at all
""").strip()


_SPEC_WITH_UNDECLARED_VAR = textwrap.dedent("""
name: bad_template
version: '1'
description: Has a template variable that's not in input_schema.
input_schema:
  type: object
  required: [a]
  properties:
    a: {type: string}
prompt_template: 'Use {a} and {missing_var}'
""").strip()


class _CannedToolProvider:
    """Stub provider whose complete() returns a canned tool spec YAML."""

    name = "local"

    def __init__(self) -> None:
        self._models = {k: "stub:latest" for k in TaskKind}

    @property
    def models(self) -> dict:
        return dict(self._models)

    async def complete(self, prompt, *, system=None,
                       task_kind=TaskKind.CONVERSATION, **_):
        # The forge engine's propose prompt expects YAML back. Return
        # the canned spec for any prompt.
        return _VALID_SPEC

    async def healthcheck(self):
        return ProviderHealth(
            name="local", status=ProviderStatus.OK, base_url="http://stub",
            models=self._models, details={"loaded": [], "missing": []},
            error=None,
        )


@pytest.fixture
def tools_forge_env(tmp_path: Path):
    for p, name in [(TRAIT_TREE, "trait tree"),
                    (CONST_TEMPLATES, "constitution templates"),
                    (TOOL_CATALOG, "tool catalog")]:
        if not p.exists():
            pytest.skip(f"{name} missing at {p}")

    install_dir = tmp_path / "tools_installed"
    install_dir.mkdir(parents=True, exist_ok=True)
    staged_dir = tmp_path / "tools_staged"
    staged_dir.mkdir(parents=True, exist_ok=True)

    settings = DaemonSettings(
        registry_db_path=tmp_path / "registry.sqlite",
        artifacts_dir=tmp_path / "souls",
        audit_chain_path=tmp_path / "audit.jsonl",
        trait_tree_path=TRAIT_TREE,
        constitution_templates_path=CONST_TEMPLATES,
        soul_output_dir=tmp_path / "souls",
        tool_catalog_path=TOOL_CATALOG,
        genres_path=GENRES,
        tool_install_dir=install_dir,
        tool_staged_dir=staged_dir,
        default_provider="local",
        frontier_enabled=False,
        allow_write_endpoints=True,
        api_token=API_TOKEN,
        enrich_narrative_default=False,
    )
    app = build_app(settings)
    with TestClient(app) as client:
        app.state.providers = ProviderRegistry(
            providers={"local": _CannedToolProvider(), "frontier": _CannedToolProvider()},
            default="local",
        )
        yield client, app, settings, staged_dir, install_dir


def _stage_spec(staged_root: Path, name: str, version: str, body: str) -> Path:
    d = staged_root / f"{name}.v{version}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "spec.yaml").write_text(body, encoding="utf-8")
    (d / "forge.log").write_text("# test fixture\n", encoding="utf-8")
    return d


def _read_chain(audit_path: Path) -> list[dict[str, Any]]:
    out = []
    with audit_path.open("r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if raw:
                out.append(json.loads(raw))
    return out


# ---------------------------------------------------------------------------
class TestToolsInstall:
    def test_happy_path_installs_registers_audits(self, tools_forge_env):
        client, app, settings, staged_root, install_dir = tools_forge_env
        staged = _stage_spec(staged_root, "summarize_audit", "1", _VALID_SPEC)

        resp = client.post(
            "/tools/install",
            headers={"X-FSF-Token": API_TOKEN},
            json={"staged_path": str(staged)},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "summarize_audit"
        assert body["audit_seq"] >= 1

        # File on disk.
        target = install_dir / "summarize_audit.v1.yaml"
        assert target.exists()

        # Live in the registry — the dispatcher can find it now.
        registry = app.state.tool_registry
        assert registry.get("summarize_audit", "1") is not None

        # Catalog augmented.
        catalog = app.state.tool_catalog
        assert "summarize_audit.v1" in catalog.tools

        # Audit event.
        events = _read_chain(settings.audit_chain_path)
        installed = [e for e in events if e["event_type"] == "forge_tool_installed"]
        assert len(installed) == 1
        assert installed[0]["event_data"]["tool_name"] == "summarize_audit"
        assert installed[0]["event_data"]["implementation"] == "prompt_template_tool.v1"
        assert installed[0]["event_data"]["mode"] == "http_api"

    def test_missing_token_returns_401(self, tools_forge_env):
        client, _, _, staged_root, _ = tools_forge_env
        staged = _stage_spec(staged_root, "summarize_audit", "1", _VALID_SPEC)
        resp = client.post("/tools/install", json={"staged_path": str(staged)})
        assert resp.status_code == 401

    def test_path_traversal_refused(self, tools_forge_env, tmp_path):
        client, _, _, staged_root, _ = tools_forge_env
        outside = tmp_path / "outside_root"
        outside.mkdir()
        (outside / "spec.yaml").write_text(_VALID_SPEC, encoding="utf-8")
        resp = client.post(
            "/tools/install",
            headers={"X-FSF-Token": API_TOKEN},
            json={"staged_path": str(outside)},
        )
        assert resp.status_code == 400

    def test_overwrite_required_for_existing(self, tools_forge_env):
        client, _, _, staged_root, _ = tools_forge_env
        staged = _stage_spec(staged_root, "summarize_audit", "1", _VALID_SPEC)
        r1 = client.post("/tools/install",
                         headers={"X-FSF-Token": API_TOKEN},
                         json={"staged_path": str(staged)})
        assert r1.status_code == 200
        r2 = client.post("/tools/install",
                         headers={"X-FSF-Token": API_TOKEN},
                         json={"staged_path": str(staged)})
        assert r2.status_code == 409
        r3 = client.post("/tools/install",
                         headers={"X-FSF-Token": API_TOKEN},
                         json={"staged_path": str(staged), "overwrite": True})
        assert r3.status_code == 200

    def test_invalid_spec_returns_422(self, tools_forge_env):
        client, _, _, staged_root, _ = tools_forge_env
        staged = _stage_spec(staged_root, "broken", "1", _INVALID_SPEC)
        resp = client.post("/tools/install",
                           headers={"X-FSF-Token": API_TOKEN},
                           json={"staged_path": str(staged)})
        assert resp.status_code == 422

    def test_template_var_undeclared_returns_422(self, tools_forge_env):
        """Spec where prompt_template uses {missing_var} not in
        input_schema.properties must be rejected at install."""
        client, _, _, staged_root, _ = tools_forge_env
        staged = _stage_spec(staged_root, "bad_template", "1", _SPEC_WITH_UNDECLARED_VAR)
        resp = client.post("/tools/install",
                           headers={"X-FSF-Token": API_TOKEN},
                           json={"staged_path": str(staged)})
        assert resp.status_code == 422
        body = resp.json()
        assert body["detail"]["path"] == "prompt_template"
        assert "missing_var" in str(body["detail"])


# ---------------------------------------------------------------------------
class TestToolsStagedListing:
    def test_returns_empty(self, tools_forge_env):
        client, *_ = tools_forge_env
        resp = client.get("/tools/staged/forged",
                          headers={"X-FSF-Token": API_TOKEN})
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_lists_valid_skips_invalid(self, tools_forge_env):
        client, _, _, staged_root, _ = tools_forge_env
        _stage_spec(staged_root, "summarize_audit", "1", _VALID_SPEC)
        _stage_spec(staged_root, "broken", "1", _INVALID_SPEC)
        resp = client.get("/tools/staged/forged",
                          headers={"X-FSF-Token": API_TOKEN})
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["staged"][0]["name"] == "summarize_audit"


class TestToolsDiscard:
    def test_removes_and_audits(self, tools_forge_env):
        client, _, settings, staged_root, _ = tools_forge_env
        staged = _stage_spec(staged_root, "summarize_audit", "1", _VALID_SPEC)
        assert staged.exists()
        resp = client.delete(
            "/tools/staged/forged/summarize_audit/1",
            headers={"X-FSF-Token": API_TOKEN},
        )
        assert resp.status_code == 200, resp.text
        assert not staged.exists()
        events = _read_chain(settings.audit_chain_path)
        discarded = [e for e in events
                     if e["event_type"] == "forge_tool_proposed"
                     and e["event_data"].get("mode") == "discarded"]
        assert len(discarded) == 1


class TestToolsForgeProposeStage:
    def test_happy_path(self, tools_forge_env):
        client, _, settings, *_ = tools_forge_env
        resp = client.post(
            "/tools/forge",
            headers={"X-FSF-Token": API_TOKEN},
            json={"description": "summarize the audit chain entries"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "summarize_audit"
        assert "n_entries" in body["input_schema_keys"]
        # Audit fired.
        events = _read_chain(settings.audit_chain_path)
        proposed = [e for e in events
                    if e["event_type"] == "forge_tool_proposed"
                    and e["event_data"].get("mode") == "http_api"]
        assert len(proposed) >= 1


class TestPromptTemplateToolUnit:
    """Direct unit coverage of the PromptTemplateTool class itself."""

    def test_template_substitution(self):
        from forest_soul_forge.tools.builtin.prompt_template_tool import (
            PromptTemplateTool,
        )
        from forest_soul_forge.tools.base import ToolValidationError

        t = PromptTemplateTool(
            name="t", version="1", description="x",
            input_schema={"type": "object", "required": ["x"],
                          "properties": {"x": {"type": "string"}}},
            prompt_template="Hello {x}!",
        )
        # Direct format check (since execute is async + needs provider)
        rendered = t._prompt_template.format(x="world")
        assert rendered == "Hello world!"

        # Validate happy path + missing required.
        t.validate({"x": "world"})
        with pytest.raises(ToolValidationError, match="missing required"):
            t.validate({})

    def test_input_type_check(self):
        from forest_soul_forge.tools.builtin.prompt_template_tool import (
            PromptTemplateTool,
        )
        from forest_soul_forge.tools.base import ToolValidationError

        t = PromptTemplateTool(
            name="t", version="1", description="x",
            input_schema={
                "type": "object",
                "required": ["n"],
                "properties": {"n": {"type": "integer", "minimum": 1, "maximum": 100}},
            },
            prompt_template="N={n}",
        )
        t.validate({"n": 50})
        with pytest.raises(ToolValidationError, match="below minimum"):
            t.validate({"n": 0})
        with pytest.raises(ToolValidationError, match="above maximum"):
            t.validate({"n": 101})
        with pytest.raises(ToolValidationError, match="expected type"):
            t.validate({"n": "fifty"})
