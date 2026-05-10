"""End-to-end tests for the Skill Forge HTTP surface — ADR-0057 / B201.

POST /skills/forge propose stage is mostly delegation to the existing
``forge.skill_forge.forge_skill`` engine; we exercise it via a tiny
stub provider that returns canned manifest YAML so we don't have to
spin up a real LLM. The other endpoints (install, list, discard) are
tested directly against hand-crafted staged dirs on disk so the
substrate behaviour is covered independently of the propose stage.

What these tests prove:

  - /skills/install correctly mirrors cli/install.py::run_skill
    (validate → copy → audit emit) and lands in the configured
    install dir with the expected forge_skill_installed event.
  - Path traversal is refused (staged_path outside the staged root).
  - Auth rejects requests without X-FSF-Token (ADR-0007 / B148).
  - Overwrite flag prevents accidental clobbering.
  - GET /skills/staged enumerates valid manifests and skips invalid
    ones rather than 500ing.
  - DELETE /skills/staged/{name}/{version} removes the dir and emits
    a forge_skill_proposed event with mode=discarded.

Tests would have caught: pre-existing bugs where a forge HTTP path
silently dropped audit emits, where install returned 200 but the
manifest landed in the wrong dir, or where a path-traversal
attempt could have read /etc/passwd through the staged_path field.
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

API_TOKEN = "test-token-skills-forge"


_VALID_MANIFEST = textwrap.dedent("""
schema_version: 1
name: count_audit
version: '1'
description: |
  Count audit-chain entries by event type. Single-step skill that
  calls audit_chain_verify.v1 to scan, then memory_recall.v1 to
  surface previous results.
requires:
  - audit_chain_verify.v1
inputs:
  type: object
  required: []
  properties: {}
steps:
  - id: verify
    tool: audit_chain_verify.v1
    args: {}
output:
  ok: ${verify.ok}
""").strip()


_INVALID_MANIFEST = textwrap.dedent("""
schema_version: 1
name: !! bad name with spaces and bangs
this isn't even valid YAML at all
""").strip()


class _CannedSkillProvider:
    """Stub provider whose ``complete`` returns a canned manifest YAML
    so /skills/forge can be exercised without hitting Ollama.
    The forge engine's parse step still runs, so this proves the
    happy-path wiring end-to-end.
    """

    name = "local"

    def __init__(self) -> None:
        self._models = {k: "stub:latest" for k in TaskKind}

    @property
    def models(self) -> dict:
        return dict(self._models)

    async def complete(self, prompt, *, task_kind=TaskKind.CONVERSATION, **_):
        # The forge engine's propose prompt expects a YAML manifest as
        # the response. Return a minimal valid SkillDef.
        return _VALID_MANIFEST

    async def healthcheck(self):
        return ProviderHealth(
            name="local", status=ProviderStatus.OK, base_url="http://stub",
            models=self._models, details={"loaded": [], "missing": []},
            error=None,
        )


@pytest.fixture
def forge_env(tmp_path: Path):
    """Daemon configured with isolated dirs + API token + canned provider."""
    for p, name in [(TRAIT_TREE, "trait tree"),
                    (CONST_TEMPLATES, "constitution templates"),
                    (TOOL_CATALOG, "tool catalog")]:
        if not p.exists():
            pytest.skip(f"{name} missing at {p}")

    install_dir = tmp_path / "skills_installed"
    install_dir.mkdir(parents=True, exist_ok=True)
    staged_dir = tmp_path / "skills_staged"
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
        skill_install_dir=install_dir,
        skill_staged_dir=staged_dir,
        default_provider="local",
        frontier_enabled=False,
        allow_write_endpoints=True,
        api_token=API_TOKEN,
        enrich_narrative_default=False,
    )
    app = build_app(settings)
    with TestClient(app) as client:
        app.state.providers = ProviderRegistry(
            providers={"local": _CannedSkillProvider(), "frontier": _CannedSkillProvider()},
            default="local",
        )
        yield client, app, settings, staged_dir, install_dir


def _stage_manifest(staged_root: Path, name: str, version: str, body: str) -> Path:
    """Hand-craft a staged folder bypassing the propose stage."""
    d = staged_root / f"{name}.v{version}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.yaml").write_text(body, encoding="utf-8")
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
class TestSkillsInstall:
    """POST /skills/install — the simpler half of the wiring."""

    def test_happy_path_installs_and_audits(self, forge_env):
        client, _, settings, staged_root, install_dir = forge_env
        staged_dir = _stage_manifest(staged_root, "count_audit", "1", _VALID_MANIFEST)

        resp = client.post(
            "/skills/install",
            headers={"X-FSF-Token": API_TOKEN},
            json={"staged_path": str(staged_dir)},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["name"] == "count_audit"
        assert body["version"] == "1"
        assert body["audit_seq"] >= 1

        # Manifest landed in install dir.
        target = install_dir / "count_audit.v1.yaml"
        assert target.exists()
        assert "name: count_audit" in target.read_text(encoding="utf-8")

        # Audit event with the right shape.
        events = _read_chain(settings.audit_chain_path)
        installed = [e for e in events if e["event_type"] == "forge_skill_installed"]
        assert len(installed) == 1
        ev = installed[0]
        assert ev["event_data"]["skill_name"] == "count_audit"
        assert ev["event_data"]["mode"] == "http_api"
        assert ev["event_data"]["installed_to"].endswith("count_audit.v1.yaml")

    def test_missing_token_returns_401(self, forge_env):
        client, _, _, staged_root, _ = forge_env
        staged_dir = _stage_manifest(staged_root, "count_audit", "1", _VALID_MANIFEST)
        resp = client.post(
            "/skills/install",
            json={"staged_path": str(staged_dir)},
        )
        assert resp.status_code == 401

    def test_missing_staged_dir_returns_404(self, forge_env):
        client, _, _, staged_root, _ = forge_env
        bogus = staged_root / "nope.v1"
        resp = client.post(
            "/skills/install",
            headers={"X-FSF-Token": API_TOKEN},
            json={"staged_path": str(bogus)},
        )
        assert resp.status_code == 404

    def test_path_traversal_refused(self, forge_env, tmp_path):
        client, _, _, staged_root, _ = forge_env
        # Try to install from a path outside staged_root. Should refuse
        # rather than expose an arbitrary file path to chain.append.
        outside = tmp_path / "outside_root"
        outside.mkdir()
        (outside / "manifest.yaml").write_text(_VALID_MANIFEST, encoding="utf-8")
        resp = client.post(
            "/skills/install",
            headers={"X-FSF-Token": API_TOKEN},
            json={"staged_path": str(outside)},
        )
        assert resp.status_code == 400
        assert "not under staged root" in resp.text

    def test_overwrite_required_for_existing(self, forge_env):
        client, _, _, staged_root, _ = forge_env
        staged_dir = _stage_manifest(staged_root, "count_audit", "1", _VALID_MANIFEST)
        # First install succeeds.
        r1 = client.post("/skills/install",
                         headers={"X-FSF-Token": API_TOKEN},
                         json={"staged_path": str(staged_dir)})
        assert r1.status_code == 200
        # Second without overwrite is 409.
        r2 = client.post("/skills/install",
                         headers={"X-FSF-Token": API_TOKEN},
                         json={"staged_path": str(staged_dir)})
        assert r2.status_code == 409
        # Second with overwrite=True succeeds.
        r3 = client.post("/skills/install",
                         headers={"X-FSF-Token": API_TOKEN},
                         json={"staged_path": str(staged_dir), "overwrite": True})
        assert r3.status_code == 200

    def test_invalid_manifest_returns_422(self, forge_env):
        client, _, _, staged_root, _ = forge_env
        staged_dir = _stage_manifest(staged_root, "broken", "1", _INVALID_MANIFEST)
        resp = client.post("/skills/install",
                           headers={"X-FSF-Token": API_TOKEN},
                           json={"staged_path": str(staged_dir)})
        assert resp.status_code == 422
        body = resp.json()
        assert body["detail"]["error"] == "manifest_validation_failed"

    # B204 regression: hallucinated tool refs rejected by default
    # ----------------------------------------------------------------

    def test_unknown_tool_in_requires_returns_422(self, forge_env):
        """B204: a manifest that references a tool not in the live
        catalog must be rejected at install time. Pre-B204 install
        validated only the manifest schema, not whether
        ``requires[]`` resolved against real tools — that gap is
        what produced the B203 smoke that referenced the
        hallucinated text_summarizer.v1.
        """
        client, _, _, staged_root, _ = forge_env
        bad_manifest = textwrap.dedent("""
        schema_version: 1
        name: hallucinated_skill
        version: '1'
        description: Skill that references a non-existent tool.
        requires:
          - text_summarizer.v1
        inputs:
          type: object
          required: [text]
          properties:
            text: {type: string}
        steps:
          - id: summarize
            tool: text_summarizer.v1
            args:
              input_text: ${inputs.text}
        output:
          summary: ${summarize.summary}
        """).strip()
        staged_dir = _stage_manifest(staged_root, "hallucinated_skill", "1", bad_manifest)
        resp = client.post(
            "/skills/install",
            headers={"X-FSF-Token": API_TOKEN},
            json={"staged_path": str(staged_dir)},
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["detail"]["error"] == "unknown_tools_referenced"
        assert "text_summarizer.v1" in body["detail"]["unknown_tools"]

    def test_unknown_tool_force_flag_allows_install(self, forge_env):
        """force_unknown_tools=true overrides the B204 check. Operator
        deliberately landing a partial skill ahead of installing the
        missing tool is a real workflow."""
        client, _, _, staged_root, _ = forge_env
        bad_manifest = textwrap.dedent("""
        schema_version: 1
        name: partial_skill
        version: '1'
        description: References a tool that's not yet installed.
        requires:
          - future_tool.v1
        inputs:
          type: object
          required: []
          properties: {}
        steps:
          - id: stub
            tool: future_tool.v1
            args: {}
        output:
          ok: ${stub.ok}
        """).strip()
        staged_dir = _stage_manifest(staged_root, "partial_skill", "1", bad_manifest)
        resp = client.post(
            "/skills/install",
            headers={"X-FSF-Token": API_TOKEN},
            json={"staged_path": str(staged_dir),
                  "force_unknown_tools": True},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "partial_skill"


# ---------------------------------------------------------------------------
class TestSkillsStagedListing:
    def test_returns_empty_when_nothing_staged(self, forge_env):
        client, *_ = forge_env
        resp = client.get("/skills/staged",
                          headers={"X-FSF-Token": API_TOKEN})
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0
        assert body["staged"] == []

    def test_lists_valid_skips_invalid(self, forge_env):
        client, _, _, staged_root, _ = forge_env
        _stage_manifest(staged_root, "count_audit", "1", _VALID_MANIFEST)
        _stage_manifest(staged_root, "broken", "1", _INVALID_MANIFEST)
        resp = client.get("/skills/staged",
                          headers={"X-FSF-Token": API_TOKEN})
        assert resp.status_code == 200
        body = resp.json()
        # Only the valid one shows; the invalid one is silently
        # skipped per the docstring contract.
        assert body["count"] == 1
        assert body["staged"][0]["name"] == "count_audit"
        assert body["staged"][0]["step_count"] == 1
        assert "audit_chain_verify.v1" in body["staged"][0]["requires"]


# ---------------------------------------------------------------------------
class TestSkillsDiscard:
    def test_removes_staged_and_audits(self, forge_env):
        client, _, settings, staged_root, _ = forge_env
        staged_dir = _stage_manifest(staged_root, "count_audit", "1", _VALID_MANIFEST)
        assert staged_dir.exists()

        resp = client.delete(
            "/skills/staged/count_audit/1",
            headers={"X-FSF-Token": API_TOKEN},
        )
        assert resp.status_code == 200, resp.text
        assert not staged_dir.exists()

        events = _read_chain(settings.audit_chain_path)
        discarded = [e for e in events
                     if e["event_type"] == "forge_skill_proposed"
                     and e["event_data"].get("mode") == "discarded"]
        assert len(discarded) == 1

    def test_missing_returns_404(self, forge_env):
        client, *_ = forge_env
        resp = client.delete(
            "/skills/staged/nope/1",
            headers={"X-FSF-Token": API_TOKEN},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
class TestSkillsForgeProposeStage:
    """Exercises the propose stage end-to-end with the canned provider.

    Proves the wiring (description → engine → staged → audit), not
    LLM intelligence. The canned provider returns a fixed valid
    manifest, so this is essentially testing that the HTTP layer
    correctly invokes the engine and surfaces results — deeper
    propose-stage testing belongs in the engine's own test file.
    """

    def test_happy_path_produces_staged(self, forge_env):
        client, _, settings, staged_root, _ = forge_env
        resp = client.post(
            "/skills/forge",
            headers={"X-FSF-Token": API_TOKEN},
            json={"description": "a test skill that counts audit entries"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["name"] == "count_audit"
        assert body["audit_seq"] is None or body["audit_seq"] >= 1
        # Staged dir exists on disk.
        assert Path(body["staged_path"]).exists()
        assert Path(body["manifest_path"]).exists()

        # forge_skill_proposed event was emitted.
        events = _read_chain(settings.audit_chain_path)
        proposed = [e for e in events if e["event_type"] == "forge_skill_proposed"]
        assert len(proposed) >= 1
        assert proposed[-1]["event_data"]["mode"] == "http_api"

    def test_missing_token_returns_401(self, forge_env):
        client, *_ = forge_env
        resp = client.post(
            "/skills/forge",
            json={"description": "anything goes here for the description"},
        )
        assert resp.status_code == 401

    def test_short_description_rejected(self, forge_env):
        client, *_ = forge_env
        resp = client.post(
            "/skills/forge",
            headers={"X-FSF-Token": API_TOKEN},
            json={"description": "too short"},  # < 10 chars per pydantic min
        )
        assert resp.status_code == 422
