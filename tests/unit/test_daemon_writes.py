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
        # B206: bypass B148 auto-token-generation in the unit fixture.
        # Token-required behavior is exercised by the dedicated
        # _auth_env fixture below which sets api_token explicitly.
        # api_token=None is required to override FSF_API_TOKEN loaded
        # from .env by pydantic-settings.
        api_token=None,
        insecure_no_token=True,
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


# ============================================================================
# ADR-0049 T4 (Burst 243) — birth-time ed25519 keypair generation
# ============================================================================
# Birth issues a per-agent keypair. Private key lands in the
# AgentKeyStore (backed by the ADR-0052 secrets store); public key
# lands in BOTH the agents.public_key column AND the soul.md
# frontmatter. The two copies must agree (the registry reads the
# frontmatter to populate the column during ingest).

class TestBirthGeneratesKeypair:
    def _read_pubkey_from_soul(self, soul_path: Path) -> str | None:
        """Parse the soul.md frontmatter and pull the public_key
        field. Returns None if missing (legacy soul)."""
        import re
        text = Path(soul_path).read_text(encoding="utf-8")
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, flags=re.DOTALL)
        if not m:
            return None
        body = m.group(1)
        for line in body.splitlines():
            if line.startswith("public_key:"):
                # ` public_key: "abc=" ` — strip whitespace + quotes.
                v = line.split(":", 1)[1].strip().strip('"').strip("'")
                return v or None
        return None

    def test_birth_writes_pubkey_to_soul_frontmatter(self, write_env):
        client, _, _ = write_env
        resp = client.post("/birth", json=_sample_birth_body())
        assert resp.status_code == 201, resp.text
        body = resp.json()
        soul_pubkey = self._read_pubkey_from_soul(Path(body["soul_path"]))
        assert soul_pubkey is not None, "public_key missing from soul.md frontmatter"
        # base64-encoded raw 32-byte ed25519 public key → 44 chars
        # (32 bytes base64 = ceil(32/3)*4 = 44 chars with padding).
        assert len(soul_pubkey) == 44, (
            f"expected 44-char base64 public key, got {len(soul_pubkey)}: {soul_pubkey!r}"
        )

    def test_birth_writes_pubkey_to_agents_table(self, write_env):
        client, app, _ = write_env
        resp = client.post("/birth", json=_sample_birth_body())
        assert resp.status_code == 201, resp.text
        instance_id = resp.json()["instance_id"]
        registry = app.state.registry
        row = registry._conn.execute(
            "SELECT public_key FROM agents WHERE instance_id = ?;",
            (instance_id,),
        ).fetchone()
        assert row is not None
        pubkey = row["public_key"] if hasattr(row, "keys") else row[0]
        assert pubkey is not None, "agents.public_key not populated at birth"
        assert len(pubkey) == 44

    def test_soul_pubkey_matches_agents_row_pubkey(self, write_env):
        """The two copies (soul frontmatter + agents column) MUST
        agree — they're derived from the same generated key. Drift
        between them would invalidate the verify-on-replay path."""
        client, app, _ = write_env
        resp = client.post("/birth", json=_sample_birth_body())
        assert resp.status_code == 201, resp.text
        body = resp.json()
        soul_pubkey = self._read_pubkey_from_soul(Path(body["soul_path"]))
        registry = app.state.registry
        row = registry._conn.execute(
            "SELECT public_key FROM agents WHERE instance_id = ?;",
            (body["instance_id"],),
        ).fetchone()
        agents_pubkey = row[0]
        assert soul_pubkey == agents_pubkey

    def test_private_key_stored_in_agent_keystore(self, write_env):
        """The matching private key must be fetchable from the
        AgentKeyStore by instance_id. This is the sign-on-emit
        path's input (T5 will use it)."""
        from forest_soul_forge.security.keys import resolve_agent_key_store

        client, _, _ = write_env
        resp = client.post("/birth", json=_sample_birth_body())
        instance_id = resp.json()["instance_id"]
        store = resolve_agent_key_store()
        priv = store.fetch(instance_id)
        assert priv is not None, "private key not stored after birth"
        # ed25519 raw private keys are 32 bytes.
        assert len(priv) == 32, f"expected 32-byte ed25519 private, got {len(priv)}"

    def test_each_birth_yields_distinct_keypair(self, write_env):
        """Two distinct agents must get distinct keypairs — public
        key is part of the agent's identity per ADR-0049 D1."""
        client, app, _ = write_env
        r1 = client.post("/birth", json=_sample_birth_body(agent_name="A"))
        r2 = client.post("/birth", json=_sample_birth_body(agent_name="B"))
        instance1 = r1.json()["instance_id"]
        instance2 = r2.json()["instance_id"]
        assert instance1 != instance2
        registry = app.state.registry
        pk1 = registry._conn.execute(
            "SELECT public_key FROM agents WHERE instance_id = ?;", (instance1,),
        ).fetchone()[0]
        pk2 = registry._conn.execute(
            "SELECT public_key FROM agents WHERE instance_id = ?;", (instance2,),
        ).fetchone()[0]
        assert pk1 != pk2

    def test_birth_pubkey_is_valid_ed25519(self, write_env):
        """The stored public key bytes must round-trip through
        ed25519's loader without raising — proves the public key
        format on the wire is what verifiers expect."""
        import base64 as _b64
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )

        client, _, _ = write_env
        resp = client.post("/birth", json=_sample_birth_body())
        soul_pubkey = self._read_pubkey_from_soul(Path(resp.json()["soul_path"]))
        # Decode + load — raises on shape mismatch.
        pk_bytes = _b64.b64decode(soul_pubkey)
        Ed25519PublicKey.from_public_bytes(pk_bytes)


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
            # B206: writes-disabled test; auth is orthogonal. Override
            # FSF_API_TOKEN from .env explicitly.
            api_token=None,
            insecure_no_token=True,
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
        # B206: bypass B148 auto-token; api_token=None overrides .env.
        api_token=None,
        insecure_no_token=True,
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


class TestRegenerateVoice:
    """POST /agents/{id}/regenerate-voice — re-runs the renderer in place.

    Verifies the soul.md is patched (not rewritten end-to-end), the
    audit chain captures a voice_regenerated event with before/after
    provider+model, and the agent's identity (dna, constitution_hash)
    is unchanged.
    """

    def _birth_unenriched(self, client, name="RegenSubject"):
        body = _sample_birth_body(agent_name=name)
        body["enrich_narrative"] = False
        resp = client.post("/birth", json=body)
        assert resp.status_code == 201, resp.text
        return resp.json()

    def test_regenerate_voice_inserts_section_when_missing(self, write_env):
        client, _, _ = write_env
        agent = self._birth_unenriched(client, "RegenA")
        soul_text_before = Path(agent["soul_path"]).read_text(encoding="utf-8")
        assert "## Voice" not in soul_text_before
        assert "narrative_provider" not in soul_text_before

        resp = client.post(
            f"/agents/{agent['instance_id']}/regenerate-voice"
        )
        assert resp.status_code == 200, resp.text

        soul_text_after = Path(agent["soul_path"]).read_text(encoding="utf-8")
        assert "## Voice" in soul_text_after
        assert 'narrative_provider: "local"' in soul_text_after
        assert 'narrative_model: "stub:latest"' in soul_text_after
        # Identity invariants unchanged.
        assert f'dna: {agent["dna"]}' in soul_text_after
        assert f'constitution_hash: "{agent["constitution_hash"]}"' in soul_text_after

    def test_regenerate_voice_replaces_existing_section(self, write_env, monkeypatch):
        """When soul already has a Voice section + narrative_* fields,
        regenerate replaces both with the new values."""
        client, app, _ = write_env

        # First birth — enriched, gives us a Voice section + narrative_* lines.
        body = _sample_birth_body(agent_name="RegenB")
        body["enrich_narrative"] = True
        agent = client.post("/birth", json=body).json()
        text_v1 = Path(agent["soul_path"]).read_text(encoding="utf-8")
        assert text_v1.count("## Voice") == 1
        assert text_v1.count("narrative_provider:") == 1

        # Swap the stub provider's complete to return DIFFERENT content
        # so we can detect that the regenerate actually pulled new text.
        async def custom_complete(prompt, *, task_kind, **_):
            return "REGENERATED_VOICE_CONTENT_MARKER"

        monkeypatch.setattr(
            app.state.providers.active(), "complete", custom_complete, raising=False
        )

        resp = client.post(f"/agents/{agent['instance_id']}/regenerate-voice")
        assert resp.status_code == 200, resp.text

        text_v2 = Path(agent["soul_path"]).read_text(encoding="utf-8")
        # Still exactly ONE Voice section and ONE narrative_provider line —
        # the regenerate replaced, didn't duplicate.
        assert text_v2.count("## Voice") == 1
        assert text_v2.count("narrative_provider:") == 1
        # New content is in the file.
        assert "REGENERATED_VOICE_CONTENT_MARKER" in text_v2
        # Old stub content is gone.
        assert "[stub] " not in text_v2.split("## Voice")[1].split("## ")[0]

    def test_regenerate_voice_unknown_agent_404(self, write_env):
        client, _, _ = write_env
        resp = client.post("/agents/not-a-real-id/regenerate-voice")
        assert resp.status_code == 404

    def test_regenerate_voice_missing_soul_file_409(self, write_env):
        client, _, _ = write_env
        agent = self._birth_unenriched(client, "RegenC")
        # Simulate file drift — soul.md gone but registry row still there.
        Path(agent["soul_path"]).unlink()

        resp = client.post(f"/agents/{agent['instance_id']}/regenerate-voice")
        assert resp.status_code == 409
        assert "soul file missing" in resp.json()["detail"]

    def test_regenerate_voice_audits_event(self, write_env):
        client, _, _ = write_env
        agent = self._birth_unenriched(client, "RegenD")

        resp = client.post(f"/agents/{agent['instance_id']}/regenerate-voice")
        assert resp.status_code == 200

        tail = client.get("/audit/tail", params={"n": 50}).json()
        regens = [
            e for e in tail["events"]
            if e["event_type"] == "voice_regenerated"
            and e["instance_id"] == agent["instance_id"]
        ]
        assert len(regens) == 1
        import json as _json
        ev = _json.loads(regens[0]["event_json"])
        # prev_provider/model are None (was unenriched birth).
        assert ev.get("previous_provider") is None
        assert ev.get("previous_model") is None
        assert ev.get("narrative_provider") == "local"
        assert ev.get("narrative_model") == "stub:latest"
        assert "narrative_generated_at" in ev


class TestToolKit:
    """ADR-0018 T2 — birth/spawn resolve and write the agent's tool kit.

    Verifies: standard archetype kit appears in soul.md by default;
    tools_add and tools_remove override correctly; unknown refs return
    400; the resolved kit lands in audit event_data.
    """

    def test_birth_default_kit_lands_in_soul(self, write_env):
        """No tools_add / tools_remove → soul.md has the
        network_watcher standard kit from config/tool_catalog.yaml.

        Post C-1 dissection (2026-04-30) the network_watcher kit is:
          - traffic_flow_local.v1   (was flow_summary.v1; read_only)
          - timestamp_window.v1     (read_only)
        Both fit network_watcher's observer-genre read_only ceiling.
        Notable absences:
          - packet_query.v1 — deferred to Phase G tshark_pcap_query.v1
          - dns_lookup.v1   — exists in the catalog (newly implemented)
                              but kept OUT of the standard kit because
                              its network side-effect would trip the
                              observer-genre ceiling. Operators who
                              want it pass tools_add at birth time.
        See docs/audits/2026-04-30-c1-zombie-tool-dissection.md.
        """
        client, _, _ = write_env
        body = _sample_birth_body(agent_name="ToolDefault")
        body["enrich_narrative"] = False  # keep soul.md byte-deterministic
        resp = client.post("/birth", json=body)
        assert resp.status_code == 201, resp.text
        soul_text = Path(resp.json()["soul_path"]).read_text(encoding="utf-8")
        # Standard kit for network_watcher (per config/tool_catalog.yaml):
        assert "name: traffic_flow_local" in soul_text
        assert "name: timestamp_window" in soul_text
        # dns_lookup is in the catalog but NOT in the default kit:
        assert "name: dns_lookup" not in soul_text
        # Catalog version is pinned.
        assert "tool_catalog_version:" in soul_text

    def test_birth_tools_add_appends_new_tool(self, write_env):
        client, _, _ = write_env
        body = _sample_birth_body(agent_name="ToolAdd")
        body["enrich_narrative"] = False
        # Use a tool that exists in the catalog but isn't in
        # network_watcher's standard kit. log_correlate fits — it lives
        # in security_mid swarm kits, not network_watcher.
        body["tools_add"] = [{"name": "log_correlate", "version": "1"}]
        resp = client.post("/birth", json=body)
        assert resp.status_code == 201, resp.text
        soul_text = Path(resp.json()["soul_path"]).read_text(encoding="utf-8")
        # log_correlate isn't in network_watcher's standard kit, so it
        # must have come from tools_add.
        assert "name: log_correlate" in soul_text

    def test_birth_tools_remove_drops_standard_tool(self, write_env):
        client, _, _ = write_env
        body = _sample_birth_body(agent_name="ToolRemove")
        body["enrich_narrative"] = False
        body["tools_remove"] = ["traffic_flow_local"]
        resp = client.post("/birth", json=body)
        assert resp.status_code == 201, resp.text
        soul_text = Path(resp.json()["soul_path"]).read_text(encoding="utf-8")
        assert "name: traffic_flow_local" not in soul_text
        # timestamp_window is still in the network_watcher kit and
        # should remain after the targeted removal.
        assert "name: timestamp_window" in soul_text

    def test_birth_tools_add_unknown_returns_400(self, write_env):
        client, _, _ = write_env
        body = _sample_birth_body(agent_name="ToolBadAdd")
        body["enrich_narrative"] = False
        body["tools_add"] = [{"name": "definitely_not_a_real_tool", "version": "1"}]
        resp = client.post("/birth", json=body)
        assert resp.status_code == 400
        assert "definitely_not_a_real_tool" in resp.json()["detail"]

    def test_birth_tools_remove_unknown_returns_400(self, write_env):
        client, _, _ = write_env
        body = _sample_birth_body(agent_name="ToolBadRemove")
        body["enrich_narrative"] = False
        body["tools_remove"] = ["this_tool_does_not_exist"]
        resp = client.post("/birth", json=body)
        assert resp.status_code == 400
        assert "this_tool_does_not_exist" in resp.json()["detail"]

    def test_audit_event_carries_resolved_tools(self, write_env):
        client, _, _ = write_env
        body = _sample_birth_body(agent_name="ToolAudited")
        body["enrich_narrative"] = False
        body["tools_remove"] = ["traffic_flow_local"]
        resp = client.post("/birth", json=body)
        assert resp.status_code == 201, resp.text
        instance_id = resp.json()["instance_id"]

        tail = client.get("/audit/tail", params={"n": 50}).json()
        created = [
            e for e in tail["events"]
            if e["event_type"] == "agent_created"
            and e["instance_id"] == instance_id
        ]
        assert len(created) == 1
        import json as _json
        ev = _json.loads(created[0]["event_json"])
        assert "tools" in ev
        assert "tool_catalog_version" in ev
        names = {t["name"] for t in ev["tools"]}
        # Post C-1 dissection (2026-04-30): network_watcher kit is
        # traffic_flow_local + timestamp_window. We remove
        # traffic_flow_local; timestamp_window should remain.
        assert "timestamp_window" in names
        assert "traffic_flow_local" not in names  # we removed it


class TestGenreTraitFloors:
    """ADR-0038 T1 — birth refuses when a Companion's profile falls below
    the genre's declared min_trait_floors. Symmetric to the existing kit-
    tier ceiling check (TestToolKit) but on traits, not tools."""

    def test_companion_default_traits_pass_floor(self, write_env):
        # operator_companion is the v0.2 Companion-genre role registered
        # in the trait engine. Defaults are evidence_demand=85,
        # transparency=85 — both above the floor (50/60).
        client, _, _ = write_env
        body = _sample_birth_body(
            agent_name="Companion-Defaults", role="operator_companion"
        )
        resp = client.post("/birth", json=body)
        assert resp.status_code == 201, resp.text

    def test_companion_below_evidence_demand_floor_400(self, write_env):
        client, _, _ = write_env
        body = _sample_birth_body(
            agent_name="Companion-LowEvidence", role="operator_companion"
        )
        body["profile"]["trait_values"] = {"evidence_demand": 30}
        resp = client.post("/birth", json=body)
        assert resp.status_code == 400, resp.text
        detail = resp.json()["detail"]
        # ADR-0038 T1: error names the genre + the offending trait + floor.
        assert "genre trait-floor violation" in detail
        assert "companion" in detail
        assert "evidence_demand=30" in detail
        assert "floor 50" in detail

    def test_companion_below_transparency_floor_400(self, write_env):
        client, _, _ = write_env
        body = _sample_birth_body(
            agent_name="Companion-LowTransparency", role="operator_companion"
        )
        body["profile"]["trait_values"] = {"transparency": 40}
        resp = client.post("/birth", json=body)
        assert resp.status_code == 400, resp.text
        detail = resp.json()["detail"]
        assert "transparency=40" in detail
        assert "floor 60" in detail

    def test_companion_below_both_floors_lists_both(self, write_env):
        # Operator should see every violation in one error, not whack-a-mole.
        client, _, _ = write_env
        body = _sample_birth_body(
            agent_name="Companion-BothLow", role="operator_companion"
        )
        body["profile"]["trait_values"] = {
            "evidence_demand": 10, "transparency": 20,
        }
        resp = client.post("/birth", json=body)
        assert resp.status_code == 400, resp.text
        detail = resp.json()["detail"]
        assert "evidence_demand=10" in detail
        assert "transparency=20" in detail

    def test_observer_genre_has_no_floor_to_violate(self, write_env):
        # Other genres declare no floors at v0.2. Birthing an Observer
        # with evidence_demand=0 succeeds — the floor mechanic is
        # opt-in, not a global default.
        client, _, _ = write_env
        body = _sample_birth_body(
            agent_name="Observer-LowEverything", role="network_watcher"
        )
        body["profile"]["trait_values"] = {
            "evidence_demand": 0, "transparency": 0,
        }
        resp = client.post("/birth", json=body)
        assert resp.status_code == 201, resp.text


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
