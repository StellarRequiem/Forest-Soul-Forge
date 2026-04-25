"""End-to-end tests for the daemon's write endpoints (/birth /spawn /archive).

Skips the module if FastAPI / pydantic-settings aren't installed. When
they are, the tests drive the full write path: TraitEngine loads from
the real ``config/trait_tree.yaml``, constitution templates load from
the real YAML, a scratch audit chain and SQLite registry are used, and
the stub provider is injected so no network call is required.
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


class _StubProvider:
    """Provider that never touches the network. Mirrors the public surface
    of LocalProvider/FrontierProvider closely enough that route handlers
    and the voice_renderer can't tell the difference: ``name``,
    ``complete``, ``healthcheck``, plus a ``models`` attribute keyed by
    TaskKind for narrative-tag resolution.
    """

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
            name="local",
            status=ProviderStatus.OK,
            base_url="http://stub",
            models=self._models,
            details={"loaded": ["stub:latest"], "missing": []},
            error=None,
        )


@pytest.fixture
def write_env(tmp_path: Path):
    """Build a daemon app wired to a scratch registry and audit chain.

    Uses the real trait tree + constitution templates so we exercise the
    whole pipeline end-to-end. Soul output goes to ``tmp_path/souls`` so
    the test leaves nothing behind.
    """
    if not TRAIT_TREE.exists():
        pytest.skip(f"trait tree missing at {TRAIT_TREE}")
    if not CONST_TEMPLATES.exists():
        pytest.skip(f"constitution templates missing at {CONST_TEMPLATES}")

    db = tmp_path / "registry.sqlite"
    audit = tmp_path / "audit.jsonl"
    souls = tmp_path / "souls"

    settings = DaemonSettings(
        registry_db_path=db,
        artifacts_dir=souls,
        audit_chain_path=audit,
        trait_tree_path=TRAIT_TREE,
        constitution_templates_path=CONST_TEMPLATES,
        soul_output_dir=souls,
        default_provider="local",
        frontier_enabled=False,
        allow_write_endpoints=True,
        # Keep existing tests deterministic — narrative enrichment
        # (ADR-0017) defaults off in this fixture so soul.md contents
        # stay byte-stable across runs. Tests that exercise enrichment
        # opt in explicitly by passing enrich_narrative=True per request,
        # or via the dedicated enrich_env fixture below.
        enrich_narrative_default=False,
    )
    app = build_app(settings)
    with TestClient(app) as client:
        # Replace provider registry with stubs so /healthz doesn't try
        # to hit Ollama.
        app.state.providers = ProviderRegistry(
            providers={"local": _StubProvider(), "frontier": _StubProvider()},
            default="local",
        )
        yield client, app, tmp_path


def _sample_birth_body(
    agent_name: str = "TestWatcher", role: str = "network_watcher"
) -> dict:
    return {
        "profile": {
            "role": role,
            "trait_values": {},
            "domain_weight_overrides": {},
        },
        "agent_name": agent_name,
        "agent_version": "v1",
        "owner_id": "test-owner",
    }


class TestBirth:
    def test_birth_creates_agent_and_artifacts(self, write_env):
        client, _, tmp = write_env
        resp = client.post("/birth", json=_sample_birth_body())
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["agent_name"] == "TestWatcher"
        assert body["role"] == "network_watcher"
        assert body["sibling_index"] == 1
        assert body["status"] == "active"
        assert Path(body["soul_path"]).exists()
        assert Path(body["constitution_path"]).exists()

    def test_birth_twin_gets_sibling_index_2(self, write_env):
        client, _, _ = write_env
        body = _sample_birth_body(agent_name="Twin")
        first = client.post("/birth", json=body).json()
        second = client.post("/birth", json=body).json()
        assert first["dna"] == second["dna"]
        assert first["sibling_index"] == 1
        assert second["sibling_index"] == 2
        # instance_id suffix only shows up on the twin
        assert not first["instance_id"].endswith("_2")
        assert second["instance_id"].endswith("_2")

    def test_birth_unknown_role_400(self, write_env):
        client, _, _ = write_env
        body = _sample_birth_body()
        body["profile"]["role"] = "not_a_role"
        resp = client.post("/birth", json=body)
        assert resp.status_code == 400
        assert "unknown role" in resp.json()["detail"]

    def test_birth_unknown_trait_400(self, write_env):
        client, _, _ = write_env
        body = _sample_birth_body()
        body["profile"]["trait_values"] = {"does_not_exist": 50}
        resp = client.post("/birth", json=body)
        assert resp.status_code == 400
        assert "unknown trait" in resp.json()["detail"]

    def test_birth_bad_trait_value_400(self, write_env):
        client, _, _ = write_env
        # Grab a real trait name from the engine
        from forest_soul_forge.core.trait_engine import TraitEngine
        engine = TraitEngine(TRAIT_TREE)
        trait_name = next(iter(engine._traits_by_name))  # noqa: SLF001
        body = _sample_birth_body()
        body["profile"]["trait_values"] = {trait_name: 999}
        resp = client.post("/birth", json=body)
        assert resp.status_code == 400

    def test_birth_override_changes_hash(self, write_env):
        client, _, _ = write_env
        a = client.post("/birth", json=_sample_birth_body(agent_name="A")).json()
        b_body = _sample_birth_body(agent_name="B")
        b_body["constitution_override"] = "forbid: extra_thing"
        b = client.post("/birth", json=b_body).json()
        # Same profile -> same derived DNA, but override differs so
        # constitution_hash must diverge.
        assert a["dna"] == b["dna"]
        assert a["constitution_hash"] != b["constitution_hash"]


class TestSpawn:
    def test_spawn_child_lineage(self, write_env):
        client, _, _ = write_env
        parent = client.post("/birth", json=_sample_birth_body(agent_name="Parent")).json()
        child_body = _sample_birth_body(agent_name="Child")
        child_body["parent_instance_id"] = parent["instance_id"]
        # Tweak one trait so the child has a different DNA from the parent.
        from forest_soul_forge.core.trait_engine import TraitEngine
        engine = TraitEngine(TRAIT_TREE)
        trait_name = next(iter(engine._traits_by_name))  # noqa: SLF001
        child_body["profile"]["trait_values"] = {trait_name: 42}
        resp = client.post("/spawn", json=child_body)
        assert resp.status_code == 201, resp.text
        child = resp.json()
        assert child["parent_instance"] == parent["instance_id"]
        assert child["dna"] != parent["dna"]
        # Ancestor list should contain the parent
        anc = client.get(f"/agents/{child['instance_id']}/ancestors").json()
        assert anc["count"] == 1
        assert anc["agents"][0]["instance_id"] == parent["instance_id"]

    def test_spawn_unknown_parent_404(self, write_env):
        client, _, _ = write_env
        body = _sample_birth_body(agent_name="Orphan")
        body["parent_instance_id"] = "not-a-real-id"
        resp = client.post("/spawn", json=body)
        assert resp.status_code == 404


class TestArchive:
    def test_archive_flips_status(self, write_env):
        client, _, _ = write_env
        agent = client.post("/birth", json=_sample_birth_body(agent_name="ToArchive")).json()
        assert agent["status"] == "active"
        resp = client.post(
            "/archive",
            json={"instance_id": agent["instance_id"], "reason": "end-of-test"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "archived"
        # Confirm via GET
        got = client.get(f"/agents/{agent['instance_id']}").json()
        assert got["status"] == "archived"

    def test_archive_unknown_404(self, write_env):
        client, _, _ = write_env
        resp = client.post(
            "/archive", json={"instance_id": "not-a-real-id", "reason": "test"}
        )
        assert resp.status_code == 404

    def test_archive_idempotent(self, write_env):
        client, _, _ = write_env
        agent = client.post("/birth", json=_sample_birth_body(agent_name="Twice")).json()
        r1 = client.post(
            "/archive",
            json={"instance_id": agent["instance_id"], "reason": "one"},
        )
        r2 = client.post(
            "/archive",
            json={"instance_id": agent["instance_id"], "reason": "again"},
        )
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r2.json()["status"] == "archived"


class TestAuditMirror:
    def test_birth_emits_agent_created_event(self, write_env):
        client, _, _ = write_env
        agent = client.post("/birth", json=_sample_birth_body(agent_name="Mirror")).json()
        tail = client.get("/audit/tail", params={"n": 10}).json()
        types = [e["event_type"] for e in tail["events"]]
        assert "agent_created" in types
        # Confirm the agent's DNA is in the agent_created payload
        found = [e for e in tail["events"] if e["event_type"] == "agent_created"]
        assert any(agent["dna"] == e["agent_dna"] for e in found)

    def test_archive_emits_agent_archived_event(self, write_env):
        client, _, _ = write_env
        agent = client.post("/birth", json=_sample_birth_body(agent_name="ArchiveMe")).json()
        client.post(
            "/archive",
            json={"instance_id": agent["instance_id"], "reason": "retiring"},
        )
        tail = client.get("/audit/tail", params={"n": 10}).json()
        types = [e["event_type"] for e in tail["events"]]
        assert "agent_archived" in types


class TestWritesDisabled:
    def test_birth_blocked_when_writes_disabled(self, tmp_path: Path):
        if not TRAIT_TREE.exists() or not CONST_TEMPLATES.exists():
            pytest.skip("fixtures missing")
        settings = DaemonSettings(
            registry_db_path=tmp_path / "r.sqlite",
            artifacts_dir=tmp_path / "a",
            audit_chain_path=tmp_path / "audit.jsonl",
            trait_tree_path=TRAIT_TREE,
            constitution_templates_path=CONST_TEMPLATES,
            soul_output_dir=tmp_path / "souls",
            default_provider="local",
            frontier_enabled=False,
            allow_write_endpoints=False,
        )
        app = build_app(settings)
        with TestClient(app) as client:
            app.state.providers = ProviderRegistry(
                providers={"local": _StubProvider(), "frontier": _StubProvider()},
                default="local",
            )
            resp = client.post("/birth", json=_sample_birth_body())
            assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Idempotency — ADR-0007 X-Idempotency-Key
# ---------------------------------------------------------------------------
class TestIdempotency:
    """Ensure X-Idempotency-Key short-circuits replays and rejects mismatches.

    These tests protect against two failure modes we want to *never* see
    in production:
      1. A flaky client retries /birth after the server actually succeeded
         but the response was lost — we must not create a second agent.
      2. A client reuses the same key across two genuinely different
         payloads — we must refuse rather than silently serve the stale
         cached body.
    """

    def test_birth_same_key_same_body_returns_cached(self, write_env):
        client, _, _ = write_env
        body = _sample_birth_body(agent_name="Idem1")
        key = "test-key-birth-stable-001"
        first = client.post("/birth", json=body, headers={"X-Idempotency-Key": key})
        second = client.post("/birth", json=body, headers={"X-Idempotency-Key": key})
        assert first.status_code == 201
        assert second.status_code == 201
        # Response must be byte-identical — same instance_id, not a new twin.
        assert first.json() == second.json()
        assert first.json()["sibling_index"] == 1
        # Only one agent for this DNA.
        agents_for_dna = client.get(
            "/agents", params={"role": "network_watcher"}
        ).json()
        matching = [
            a for a in agents_for_dna["agents"] if a["agent_name"] == "Idem1"
        ]
        assert len(matching) == 1

    def test_birth_same_key_different_body_409(self, write_env):
        client, _, _ = write_env
        key = "test-key-birth-mismatch-002"
        body1 = _sample_birth_body(agent_name="Mismatch1")
        body2 = _sample_birth_body(agent_name="Mismatch2")
        r1 = client.post("/birth", json=body1, headers={"X-Idempotency-Key": key})
        r2 = client.post("/birth", json=body2, headers={"X-Idempotency-Key": key})
        assert r1.status_code == 201
        assert r2.status_code == 409
        assert "idempotency" in r2.json()["detail"].lower()

    def test_empty_idempotency_key_400(self, write_env):
        client, _, _ = write_env
        resp = client.post(
            "/birth",
            json=_sample_birth_body(agent_name="Empty"),
            headers={"X-Idempotency-Key": "   "},
        )
        assert resp.status_code == 400

    def test_spawn_idempotent_replay(self, write_env):
        client, _, _ = write_env
        parent = client.post(
            "/birth", json=_sample_birth_body(agent_name="IdemParent")
        ).json()
        from forest_soul_forge.core.trait_engine import TraitEngine
        engine = TraitEngine(TRAIT_TREE)
        trait_name = next(iter(engine._traits_by_name))  # noqa: SLF001
        child_body = _sample_birth_body(agent_name="IdemChild")
        child_body["parent_instance_id"] = parent["instance_id"]
        child_body["profile"]["trait_values"] = {trait_name: 33}
        key = "test-key-spawn-stable-003"
        r1 = client.post("/spawn", json=child_body, headers={"X-Idempotency-Key": key})
        r2 = client.post("/spawn", json=child_body, headers={"X-Idempotency-Key": key})
        assert r1.status_code == 201
        assert r2.status_code == 201
        assert r1.json() == r2.json()

    def test_archive_idempotent_replay(self, write_env):
        client, _, _ = write_env
        agent = client.post(
            "/birth", json=_sample_birth_body(agent_name="IdemArchive")
        ).json()
        body = {"instance_id": agent["instance_id"], "reason": "test"}
        key = "test-key-archive-stable-004"
        r1 = client.post("/archive", json=body, headers={"X-Idempotency-Key": key})
        r2 = client.post("/archive", json=body, headers={"X-Idempotency-Key": key})
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json() == r2.json()


# ---------------------------------------------------------------------------
# Auth — X-FSF-Token (ADR-0007)
# ---------------------------------------------------------------------------
class TestAuth:
    """When ``api_token`` is set, write endpoints require the header."""

    def _auth_env(self, tmp_path: Path, token: str = "s3cr3t"):
        if not TRAIT_TREE.exists() or not CONST_TEMPLATES.exists():
            pytest.skip("fixtures missing")
        settings = DaemonSettings(
            registry_db_path=tmp_path / "r.sqlite",
            artifacts_dir=tmp_path / "a",
            audit_chain_path=tmp_path / "audit.jsonl",
            trait_tree_path=TRAIT_TREE,
            constitution_templates_path=CONST_TEMPLATES,
            soul_output_dir=tmp_path / "souls",
            default_provider="local",
            frontier_enabled=False,
            allow_write_endpoints=True,
            api_token=token,
        )
        app = build_app(settings)
        client = TestClient(app)
        app.state.providers = ProviderRegistry(
            providers={"local": _StubProvider(), "frontier": _StubProvider()},
            default="local",
        )
        return client, app

    def test_missing_token_401(self, tmp_path: Path):
        client, app = self._auth_env(tmp_path)
        with client:
            resp = client.post("/birth", json=_sample_birth_body())
            assert resp.status_code == 401

    def test_wrong_token_401(self, tmp_path: Path):
        client, app = self._auth_env(tmp_path)
        with client:
            resp = client.post(
                "/birth",
                json=_sample_birth_body(),
                headers={"X-FSF-Token": "wrong"},
            )
            assert resp.status_code == 401

    def test_correct_token_passes(self, tmp_path: Path):
        client, app = self._auth_env(tmp_path, token="correct-horse-battery")
        with client:
            resp = client.post(
                "/birth",
                json=_sample_birth_body(),
                headers={"X-FSF-Token": "correct-horse-battery"},
            )
            assert resp.status_code == 201

    def test_healthz_surfaces_auth_required(self, tmp_path: Path):
        client, app = self._auth_env(tmp_path)
        with client:
            resp = client.get("/healthz")
            assert resp.status_code == 200
            body = resp.json()
            assert body["auth_required"] is True
            assert body["writes_enabled"] is True


# ---------------------------------------------------------------------------
# /traits endpoint
# ---------------------------------------------------------------------------
class TestTraitsEndpoint:
    def test_traits_returns_tree(self, write_env):
        client, _, _ = write_env
        resp = client.get("/traits")
        assert resp.status_code == 200
        body = resp.json()
        # Basic shape checks — the tree is authored in YAML so we're not
        # re-asserting every trait, just that the serializer produced the
        # expected top-level keys.
        assert "version" in body
        assert "domains" in body
        assert "roles" in body
        assert "flagged_combinations" in body
        assert len(body["domains"]) > 0
        # Each domain has subdomains and each subdomain has traits.
        first_domain = body["domains"][0]
        assert len(first_domain["subdomains"]) > 0
        first_sub = first_domain["subdomains"][0]
        assert len(first_sub["traits"]) > 0
        # Computed tier_weight should be present on every trait.
        for sd in first_domain["subdomains"]:
            for t in sd["traits"]:
                assert "tier_weight" in t
                assert isinstance(t["tier_weight"], (int, float))


# ---------------------------------------------------------------------------
# /preview endpoint
# ---------------------------------------------------------------------------
class TestPreviewEndpoint:
    def test_preview_matches_birth_hash(self, write_env):
        """The constitution hash from /preview must equal what /birth writes."""
        client, _, _ = write_env
        body = _sample_birth_body(agent_name="PreviewA")
        preview_resp = client.post(
            "/preview",
            json={
                "profile": body["profile"],
                "constitution_override": None,
            },
        )
        assert preview_resp.status_code == 200
        preview = preview_resp.json()
        birth_resp = client.post("/birth", json=body)
        assert birth_resp.status_code == 201
        birth = birth_resp.json()
        # DNA and the effective hash must match — that's the whole point
        # of /preview: predict what /birth would produce without writing.
        assert preview["dna"] == birth["dna"]
        assert preview["dna_full"] == birth["dna_full"]
        assert preview["constitution_hash_effective"] == birth["constitution_hash"]

    def test_preview_override_folds_into_hash(self, write_env):
        client, _, _ = write_env
        profile = _sample_birth_body()["profile"]
        base = client.post(
            "/preview", json={"profile": profile, "constitution_override": None}
        ).json()
        with_override = client.post(
            "/preview",
            json={"profile": profile, "constitution_override": "extra: policy"},
        ).json()
        # Derived hash is the same (pure function of profile); effective
        # diverges when override is folded in.
        assert base["constitution_hash_derived"] == with_override["constitution_hash_derived"]
        assert base["constitution_hash_effective"] != with_override["constitution_hash_effective"]

    def test_preview_is_zero_write(self, write_env):
        """A /preview call must not create rows in the registry."""
        client, _, _ = write_env
        before = client.get("/agents").json()["count"]
        client.post(
            "/preview",
            json={
                "profile": {
                    "role": "network_watcher",
                    "trait_values": {},
                    "domain_weight_overrides": {},
                },
                "constitution_override": None,
            },
        )
        after = client.get("/agents").json()["count"]
        assert before == after

    def test_preview_unknown_role_400(self, write_env):
        client, _, _ = write_env
        resp = client.post(
            "/preview",
            json={
                "profile": {
                    "role": "not_a_role",
                    "trait_values": {},
                    "domain_weight_overrides": {},
                },
                "constitution_override": None,
            },
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# ADR-0017 — LLM-enriched soul.md narrative
# ---------------------------------------------------------------------------
@pytest.fixture
def enrich_env(tmp_path: Path):
    """Variant of write_env with ``enrich_narrative_default=True`` so the
    settings-default fallback path is exercisable.

    Tests that don't depend on the global default can use ``write_env``
    and pass ``enrich_narrative=True`` per request instead.
    """
    if not TRAIT_TREE.exists() or not CONST_TEMPLATES.exists():
        pytest.skip("fixtures missing")

    db = tmp_path / "registry.sqlite"
    audit = tmp_path / "audit.jsonl"
    souls = tmp_path / "souls"

    settings = DaemonSettings(
        registry_db_path=db,
        artifacts_dir=souls,
        audit_chain_path=audit,
        trait_tree_path=TRAIT_TREE,
        constitution_templates_path=CONST_TEMPLATES,
        soul_output_dir=souls,
        default_provider="local",
        frontier_enabled=False,
        allow_write_endpoints=True,
        enrich_narrative_default=True,
    )
    app = build_app(settings)
    with TestClient(app) as client:
        app.state.providers = ProviderRegistry(
            providers={"local": _StubProvider(), "frontier": _StubProvider()},
            default="local",
        )
        yield client, app, tmp_path


def _read_soul(soul_path: str) -> str:
    return Path(soul_path).read_text(encoding="utf-8")


class TestEnrichNarrative:
    """ADR-0017 — LLM-enriched soul.md `## Voice` section.

    Confirms: enrich_narrative=True triggers a provider.complete() call
    and inserts a Voice section + narrative_* frontmatter; False bypasses
    it; the settings default applies when the field is absent; provider
    failures fall back to the template; and the audit chain captures the
    narrative provenance.
    """

    def test_enrich_true_inserts_voice_section(self, write_env):
        client, _, _ = write_env
        body = _sample_birth_body(agent_name="VoiceWatcher")
        body["enrich_narrative"] = True
        resp = client.post("/birth", json=body)
        assert resp.status_code == 201, resp.text
        soul_text = _read_soul(resp.json()["soul_path"])
        assert "## Voice" in soul_text
        # Stub provider returns "[stub] {prompt}" so the rendered voice
        # body starts with that prefix.
        assert "[stub]" in soul_text
        # Frontmatter records provenance.
        assert 'narrative_provider: "local"' in soul_text
        assert 'narrative_model: "stub:latest"' in soul_text
        assert "narrative_generated_at:" in soul_text

    def test_enrich_false_no_voice_section(self, write_env):
        client, _, _ = write_env
        body = _sample_birth_body(agent_name="QuietWatcher")
        body["enrich_narrative"] = False
        resp = client.post("/birth", json=body)
        assert resp.status_code == 201, resp.text
        soul_text = _read_soul(resp.json()["soul_path"])
        assert "## Voice" not in soul_text
        assert "narrative_provider" not in soul_text
        assert "narrative_model" not in soul_text

    def test_enrich_default_settings_path(self, enrich_env):
        """When request omits the field, FSF_ENRICH_NARRATIVE_DEFAULT applies."""
        client, _, _ = enrich_env
        body = _sample_birth_body(agent_name="DefaultWatcher")
        # No enrich_narrative key → falls through to settings (which is
        # True in this fixture).
        resp = client.post("/birth", json=body)
        assert resp.status_code == 201, resp.text
        soul_text = _read_soul(resp.json()["soul_path"])
        assert "## Voice" in soul_text
        assert 'narrative_provider: "local"' in soul_text

    def test_provider_unavailable_falls_back_to_template(
        self, write_env, monkeypatch
    ):
        """When provider.complete raises, voice_renderer returns a templated
        VoiceText with provider="template". Soul still has the section,
        frontmatter records the fallback, /birth still succeeds.
        """
        from forest_soul_forge.daemon.providers import ProviderUnavailable

        client, app, _ = write_env

        async def raise_unavailable(*_a, **_k):
            raise ProviderUnavailable("ollama unreachable at http://stub")

        monkeypatch.setattr(
            app.state.providers.active(), "complete", raise_unavailable, raising=False
        )

        body = _sample_birth_body(agent_name="FallbackWatcher")
        body["enrich_narrative"] = True
        resp = client.post("/birth", json=body)
        assert resp.status_code == 201, resp.text
        soul_text = _read_soul(resp.json()["soul_path"])
        assert "## Voice" in soul_text
        assert 'narrative_provider: "template"' in soul_text
        assert 'narrative_model: "template"' in soul_text
        # Templated body marks itself with the italic provenance line.
        assert "_(template fallback —" in soul_text

    def test_audit_event_carries_narrative_fields(self, write_env):
        """The agent_created audit event_data records narrative_*."""
        client, _, _ = write_env
        body = _sample_birth_body(agent_name="AuditedVoice")
        body["enrich_narrative"] = True
        resp = client.post("/birth", json=body)
        assert resp.status_code == 201, resp.text
        instance_id = resp.json()["instance_id"]

        # Pull the audit tail and find the agent_created event for this
        # instance — its event_json should contain the narrative_* fields.
        tail = client.get("/audit/tail", params={"n": 50}).json()
        created = [
            e
            for e in tail["events"]
            if e["event_type"] == "agent_created"
            and e["instance_id"] == instance_id
        ]
        assert len(created) == 1, f"expected one agent_created event, got {created}"
        import json as _json
        ev = _json.loads(created[0]["event_json"])
        assert ev.get("narrative_provider") == "local"
        assert ev.get("narrative_model") == "stub:latest"
        assert "narrative_generated_at" in ev

    def test_spawn_with_enrich_true_inserts_voice(self, write_env):
        """Spawn path mirrors birth — voice renderer is invoked, child
        soul.md gets the Voice section + narrative_* frontmatter."""
        client, _, _ = write_env
        # Birth a parent first (deterministic, no voice).
        parent_body = _sample_birth_body(agent_name="Parent")
        parent_body["enrich_narrative"] = False
        parent = client.post("/birth", json=parent_body).json()
        # Now spawn a child WITH enrichment.
        child_body = _sample_birth_body(agent_name="Child")
        child_body["enrich_narrative"] = True
        child_body["parent_instance_id"] = parent["instance_id"]
        resp = client.post("/spawn", json=child_body)
        assert resp.status_code == 201, resp.text
        soul_text = _read_soul(resp.json()["soul_path"])
        assert "## Voice" in soul_text
        assert 'narrative_provider: "local"' in soul_text


class TestVoiceRendererUnit:
    """Direct unit tests for forest_soul_forge.soul.voice_renderer.render_voice.

    These bypass the daemon entirely and exercise the renderer's
    error-handling contract: provider exceptions become templated
    VoiceText, the system prompt is delivered to the provider's
    complete() call, and the model tag is resolved from .models.
    """

    def _profile_and_engine(self):
        """Build a real profile against the real trait engine."""
        if not TRAIT_TREE.exists():
            pytest.skip(f"trait tree missing at {TRAIT_TREE}")
        from forest_soul_forge.core.trait_engine import TraitEngine
        engine = TraitEngine(TRAIT_TREE)
        profile = engine.build_profile(role="network_watcher")
        return profile, engine

    def test_happy_path_returns_provider_text(self):
        from forest_soul_forge.core.dna import Lineage
        from forest_soul_forge.soul.voice_renderer import render_voice
        import asyncio

        profile, engine = self._profile_and_engine()
        provider = _StubProvider()
        settings = DaemonSettings(
            registry_db_path=Path("/tmp/x.sqlite"),
            artifacts_dir=Path("/tmp/x"),
            audit_chain_path=Path("/tmp/x.jsonl"),
            trait_tree_path=TRAIT_TREE,
            constitution_templates_path=CONST_TEMPLATES,
        )
        role = engine.get_role(profile.role)
        result = asyncio.run(
            render_voice(
                provider,
                profile=profile,
                role=role,
                engine=engine,
                lineage=Lineage.root(),
                settings=settings,
            )
        )
        assert result.provider == "local"
        assert result.model == "stub:latest"
        assert result.markdown.startswith("[stub]")
        assert result.generated_at  # non-empty timestamp

    def test_provider_unavailable_returns_template(self):
        from forest_soul_forge.core.dna import Lineage
        from forest_soul_forge.daemon.providers import ProviderUnavailable
        from forest_soul_forge.soul.voice_renderer import render_voice
        import asyncio

        profile, engine = self._profile_and_engine()

        class _BadProvider(_StubProvider):
            async def complete(self, *_a, **_k):
                raise ProviderUnavailable("simulated outage")

        provider = _BadProvider()
        settings = DaemonSettings(
            registry_db_path=Path("/tmp/x.sqlite"),
            artifacts_dir=Path("/tmp/x"),
            audit_chain_path=Path("/tmp/x.jsonl"),
            trait_tree_path=TRAIT_TREE,
            constitution_templates_path=CONST_TEMPLATES,
        )
        role = engine.get_role(profile.role)
        result = asyncio.run(
            render_voice(
                provider,
                profile=profile,
                role=role,
                engine=engine,
                lineage=Lineage.root(),
                settings=settings,
            )
        )
        assert result.provider == "template"
        assert result.model == "template"
        assert "_(template fallback —" in result.markdown

    def test_invalid_task_kind_returns_template_with_note(self):
        """A misconfigured FSF_NARRATIVE_TASK_KIND falls back to template
        with a diagnostic note, rather than raising at birth time."""
        from forest_soul_forge.core.dna import Lineage
        from forest_soul_forge.soul.voice_renderer import render_voice
        import asyncio

        profile, engine = self._profile_and_engine()
        provider = _StubProvider()
        settings = DaemonSettings(
            registry_db_path=Path("/tmp/x.sqlite"),
            artifacts_dir=Path("/tmp/x"),
            audit_chain_path=Path("/tmp/x.jsonl"),
            trait_tree_path=TRAIT_TREE,
            constitution_templates_path=CONST_TEMPLATES,
            narrative_task_kind="not_a_real_kind",
        )
        role = engine.get_role(profile.role)
        result = asyncio.run(
            render_voice(
                provider,
                profile=profile,
                role=role,
                engine=engine,
                lineage=Lineage.root(),
                settings=settings,
            )
        )
        assert result.provider == "template"
        assert "invalid FSF_NARRATIVE_TASK_KIND" in result.markdown
