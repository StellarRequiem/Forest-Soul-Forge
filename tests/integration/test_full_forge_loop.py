"""End-to-end smoke test — exercises the full forge → install → run loop.

Touches every endpoint we shipped through Skill Forge T5 + frontend
T8 in one test:

    1. Build daemon, birth a network_watcher (already covered by other
       tests but exercised here to keep the scenario self-contained).
    2. Forge a skill manifest via the Skill Forge engine using a fake
       provider that returns a known-good manifest. Skill exercises
       the built-in ``timestamp_window.v1`` tool — the only path that
       lets us actually call a tool inside a skill without ADR-0019
       T5 plugin loader.
    3. Move the staged manifest into the configured ``skill_install_dir``
       (simulates the operator's "fsf install skill" step that lands
       in Round 2a).
    4. Reload the daemon by re-reading the catalog directory directly
       (until POST /skills/reload lands).
    5. GET /skills — assert the new skill appears in the catalog.
    6. POST /agents/{id}/skills/run — pass real inputs.
    7. Assert succeeded, output assembled correctly, audit chain has
       the expected event sequence.

Failures here surface integration bugs that unit tests miss:
deps wiring, lifespan loaders out of sync, write-lock contention
between the skill runtime and the per-step tool dispatcher,
audit-chain ordering across the runtime boundary.
"""
from __future__ import annotations

import asyncio
import textwrap
from dataclasses import dataclass
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


# ---------------------------------------------------------------------------
# Fake provider — answers the skill forge's propose call, plus stubs out
# anything the daemon might call during lifespan health checks.
# ---------------------------------------------------------------------------
@dataclass
class _FakeProvider:
    name: str = "local"
    skill_propose_reply: str = ""

    @property
    def models(self) -> dict:
        return {k: "stub:latest" for k in TaskKind}

    async def complete(self, prompt: str, **kwargs) -> str:
        # The skill forge's propose-stage prompt has a distinctive
        # opening; recognize it and return the canned manifest.
        if "skill manifest" in prompt.lower() or "Workflow description" in prompt:
            return self.skill_propose_reply
        # Anything else gets a stub — we don't expect other paths to
        # hit the provider in this scenario.
        return f"[stub] {prompt[:32]}"

    async def healthcheck(self):
        return ProviderHealth(
            name=self.name, status=ProviderStatus.OK, base_url="http://stub",
            models=self.models, details={"loaded": [], "missing": []},
            error=None,
        )


_PROPOSE_REPLY = textwrap.dedent("""
schema_version: 1
name: get_window_e2e
version: '1'
description: |
  Compute a time window from a relative expression. Used by the
  end-to-end smoke test.
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
def smoke_env(tmp_path: Path):
    """Daemon + fake provider + scratch artifact tree, no real network."""
    for p, name in [(TRAIT_TREE, "trait tree"),
                    (CONST_TEMPLATES, "constitution templates"),
                    (TOOL_CATALOG, "tool catalog")]:
        if not p.exists():
            pytest.skip(f"{name} missing at {p}")

    skill_install_dir = tmp_path / "installed"
    skill_install_dir.mkdir()
    skill_staged_dir = tmp_path / "staged"
    skill_staged_dir.mkdir()

    settings = DaemonSettings(
        registry_db_path=tmp_path / "registry.sqlite",
        artifacts_dir=tmp_path / "souls",
        audit_chain_path=tmp_path / "audit.jsonl",
        trait_tree_path=TRAIT_TREE,
        constitution_templates_path=CONST_TEMPLATES,
        soul_output_dir=tmp_path / "souls",
        tool_catalog_path=TOOL_CATALOG,
        genres_path=GENRES,
        skill_install_dir=skill_install_dir,
        default_provider="local",
        frontier_enabled=False,
        allow_write_endpoints=True,
        enrich_narrative_default=False,
    )
    app = build_app(settings)
    provider = _FakeProvider(skill_propose_reply=_PROPOSE_REPLY)
    yield {
        "app": app,
        "settings": settings,
        "provider": provider,
        "skill_staged_dir": skill_staged_dir,
        "skill_install_dir": skill_install_dir,
        "tmp_path": tmp_path,
    }


def _birth_agent(client: TestClient) -> str:
    """Helper — births a network_watcher and returns its instance_id."""
    resp = client.post("/birth", json={
        "profile": {
            "role": "network_watcher",
            "trait_values": {},
            "domain_weight_overrides": {},
        },
        "agent_name": "SmokeWatcher",
        "agent_version": "v1",
        "owner_id": "smoke",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["instance_id"]


def test_full_forge_loop(smoke_env):
    """The actual scenario."""
    app = smoke_env["app"]
    settings = smoke_env["settings"]
    provider = smoke_env["provider"]

    with TestClient(app) as client:
        app.state.providers = ProviderRegistry(
            providers={"local": provider, "frontier": provider},
            default="local",
        )

        # Stage 1 — birth the agent.
        instance_id = _birth_agent(client)

        # Stage 2 — forge the skill via the engine. We don't go through
        # the CLI subprocess because that would require pip-install in
        # the test env; we drive forge_skill_sync directly with the same
        # fake provider.
        from forest_soul_forge.forge.skill_forge import forge_skill_sync
        result = forge_skill_sync(
            description="Compute a time window from an expression",
            provider=provider,
            out_dir=smoke_env["skill_staged_dir"],
            forged_by="smoke",
        )
        assert result.skill.name == "get_window_e2e"
        assert result.manifest_path.exists()

        # Stage 3 — install: copy the staged manifest into the
        # configured skill_install_dir. Round 2a will replace this
        # with `fsf install skill`.
        installed = smoke_env["skill_install_dir"] / "get_window_e2e.v1.yaml"
        installed.write_text(
            result.manifest_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        # Stage 4 — reload the catalog. Until POST /skills/reload
        # lands (Round 2a), reload by replacing app.state directly.
        from forest_soul_forge.core.skill_catalog import load_catalog
        new_catalog, errors = load_catalog(settings.skill_install_dir)
        assert errors == []
        app.state.skill_catalog = new_catalog

        # Stage 5 — GET /skills returns the new entry.
        resp = client.get("/skills")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["skills"][0]["name"] == "get_window_e2e"
        assert body["skills"][0]["requires"] == ["timestamp_window.v1"]

        # Stage 6 — run the skill on the agent.
        resp = client.post(
            f"/agents/{instance_id}/skills/run",
            json={
                "skill_name": "get_window_e2e",
                "skill_version": "1",
                "session_id": "smoke-1",
                "inputs": {"expr": "last 15 minutes"},
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "succeeded", body
        assert body["steps_executed"] == 1
        out = body["output"]
        assert out["span"] == 900  # 15 minutes * 60s
        assert out["start"]
        assert out["end"]

        # Stage 7 — audit chain has the expected sequence. We check
        # the suffix because births and other ops added entries
        # before. Order: skill_invoked → skill_step_started →
        # tool_call_dispatched → tool_call_succeeded →
        # skill_step_completed → skill_completed.
        chain = app.state.audit_chain
        all_events = chain.read_all()
        types = [e.event_type for e in all_events]
        # The skill run is the very last block; pick the last
        # skill_invoked and walk forward.
        last_invoked = max(
            i for i, t in enumerate(types) if t == "skill_invoked"
        )
        suffix = types[last_invoked:]
        expected = [
            "skill_invoked",
            "skill_step_started",
            "tool_call_dispatched",
            "tool_call_succeeded",
            "skill_step_completed",
            "skill_completed",
        ]
        assert suffix == expected, (
            f"audit chain order wrong\n"
            f"  expected: {expected}\n"
            f"  got:      {suffix}"
        )


# ---------------------------------------------------------------------------
# Write + recall round-trip — proves the memory subsystem end-to-end
# through the runtime. Skill A writes a memory; skill B reads it back.
# ---------------------------------------------------------------------------
_WRITE_SKILL = textwrap.dedent("""
schema_version: 1
name: stash_note
version: '1'
description: Save a note to the agent's episodic memory.
requires: [memory_write.v1]
inputs:
  type: object
  required: [body]
  properties:
    body: {type: string}
steps:
  - id: stash
    tool: memory_write.v1
    args:
      content: ${inputs.body}
      layer: episodic
output:
  entry_id: ${stash.entry_id}
""").strip()


_RECALL_SKILL = textwrap.dedent("""
schema_version: 1
name: read_notes
version: '1'
description: Read recent episodic notes back.
requires: [memory_recall.v1]
inputs:
  type: object
  properties:
    query: {type: string}
steps:
  - id: hits
    tool: memory_recall.v1
    args:
      layer: episodic
      query: ${inputs.query}
      limit: 5
output:
  count:   ${hits.count}
  entries: ${hits.entries}
""").strip()


@pytest.fixture
def memory_smoke_env(tmp_path: Path):
    """Daemon + the two memory skills installed."""
    for p, name in [(TRAIT_TREE, "trait tree"),
                    (CONST_TEMPLATES, "constitution templates"),
                    (TOOL_CATALOG, "tool catalog")]:
        if not p.exists():
            pytest.skip(f"{name} missing at {p}")

    skill_install_dir = tmp_path / "installed"
    skill_install_dir.mkdir()
    (skill_install_dir / "stash_note.v1.yaml").write_text(_WRITE_SKILL, encoding="utf-8")
    (skill_install_dir / "read_notes.v1.yaml").write_text(_RECALL_SKILL, encoding="utf-8")

    settings = DaemonSettings(
        registry_db_path=tmp_path / "registry.sqlite",
        artifacts_dir=tmp_path / "souls",
        audit_chain_path=tmp_path / "audit.jsonl",
        trait_tree_path=TRAIT_TREE,
        constitution_templates_path=CONST_TEMPLATES,
        soul_output_dir=tmp_path / "souls",
        tool_catalog_path=TOOL_CATALOG,
        genres_path=GENRES,
        skill_install_dir=skill_install_dir,
        default_provider="local",
        frontier_enabled=False,
        allow_write_endpoints=True,
        enrich_narrative_default=False,
    )
    app = build_app(settings)
    provider = _FakeProvider()
    yield {"app": app, "settings": settings, "provider": provider}


def test_write_then_recall_loop(memory_smoke_env):
    """Two skills, two runs, one round-trip through the memory subsystem.

    Birth a researcher, run stash_note to save a unique phrase, run
    read_notes to find it back, assert the phrase came through.
    Then check the character sheet shows total_entries=1.
    """
    app = memory_smoke_env["app"]
    provider = memory_smoke_env["provider"]
    UNIQUE_PHRASE = "synapse-cabbage-twirl-9347"

    with TestClient(app) as client:
        app.state.providers = ProviderRegistry(
            providers={"local": provider, "frontier": provider},
            default="local",
        )
        # Birth a system_architect (researcher-genre, ships memory_write +
        # memory_recall in its standard kit per ADR-0034 SW-track).
        # Earlier this used anomaly_investigator but that role's kit
        # doesn't include memory tools — the test always failed with
        # "memory_write.v1 ref ... not in constitution". Fixed under
        # the 2026-04-30 C-1 dissection follow-on.
        resp = client.post("/birth", json={
            "profile": {
                "role": "system_architect",
                "trait_values": {},
                "domain_weight_overrides": {},
            },
            "agent_name": "MemorySmoke",
            "agent_version": "v1",
            "owner_id": "smoke",
        })
        assert resp.status_code == 201, resp.text
        instance_id = resp.json()["instance_id"]

        # Stash a unique note.
        resp = client.post(
            f"/agents/{instance_id}/skills/run",
            json={
                "skill_name": "stash_note",
                "skill_version": "1",
                "session_id": "mem-smoke-1",
                "inputs": {"body": UNIQUE_PHRASE},
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "succeeded", body
        entry_id = body["output"]["entry_id"]
        assert entry_id

        # Read it back.
        resp = client.post(
            f"/agents/{instance_id}/skills/run",
            json={
                "skill_name": "read_notes",
                "skill_version": "1",
                "session_id": "mem-smoke-1",
                "inputs": {"query": "cabbage"},
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "succeeded", body
        out = body["output"]
        assert out["count"] >= 1
        # The phrase comes back verbatim.
        contents = [e["content"] for e in out["entries"]]
        assert any(UNIQUE_PHRASE in c for c in contents), (
            f"recall didn't find the phrase. Got: {contents}"
        )

        # Character sheet now shows the memory entry.
        resp = client.get(f"/agents/{instance_id}/character-sheet")
        assert resp.status_code == 200, resp.text
        memory = resp.json()["memory"]
        assert memory["not_yet_measured"] is False
        assert memory["total_entries"] == 1
        assert memory["layers"]["episodic"] == 1
