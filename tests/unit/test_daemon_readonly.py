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
    """Provider that never touches the network. Used to exercise the routes.

    Mirrors the public surface of LocalProvider/FrontierProvider closely
    enough that route handlers can't tell the difference: ``name``,
    ``complete``, ``healthcheck``, and a ``models`` attribute keyed by
    TaskKind. The ``models`` attribute is what /runtime/provider/generate
    reads to resolve the model tag in its response.
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
        # B206: bypass B148 auto-token. api_token=None overrides
        # FSF_API_TOKEN loaded from .env by pydantic-settings.
        api_token=None,
        insecure_no_token=True,
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
        # schema_version bumps with each migration. Currently v12:
        # v6 added memory_entries (ADR-0022 v0.1), v7 added the
        # disclosed_* columns + memory_consents (ADR-0027), v10
        # added conversations + participants + turns (ADR-003Y Y1),
        # v11 added the epistemic-memory metadata + memory_contradictions
        # (ADR-0027-amendment), v12 added flagged_state on
        # memory_contradictions (ADR-0036 T6 — Verifier ratification
        # dial), v13 added scheduled_task_state for the ADR-0041
        # scheduler's persistence (Burst 90), v14 added
        # agent_plugin_grants (Burst 113a), v15 added agents.posture
        # (Burst 114), v16 added memory_procedural_shortcuts
        # (Burst 178, ADR-0054 T1), v17 added agent_catalog_grants
        # (Burst 219, ADR-0060 T1), and v18 added the tool_name
        # column on agent_plugin_grants for per-tool granularity
        # (Burst 235, ADR-0053 T1). Daemon reports the registry's
        # live schema_version; assertion tracks the live value
        # rather than a stale literal.
        assert body["schema_version"] == 20
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
        # B206: relaxed from `count == 1` to `count >= 1` because the
        # daemon's lifespan now emits additional events at startup
        # (agent registration replay, plugin install discovery,
        # forged_tool_loader init per ADR-0058 / B202, etc.) so a
        # freshly-built TestClient sees more than just the genesis.
        # The original assertion would silently break every time a new
        # lifespan-time event got added; the count >= 1 + first-event
        # check is the real invariant.
        assert body["count"] >= 1
        # Genesis is always the FIRST entry on a fresh chain. /audit/tail
        # returns newest-first, so the chain_created event is at the END
        # of the returned list (most-recent-N from the tail). Verify by
        # finding it explicitly rather than indexing position 0.
        event_types = [e["event_type"] for e in body["events"]]
        assert "chain_created" in event_types

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


class TestGenerate:
    """POST /runtime/provider/generate — Phase 4 first slice.

    Exercises the route's mapping from request → provider.complete kwargs
    → response, plus the error-status mapping for the three Provider*
    exception types.
    """

    def test_generate_happy_path(self, daemon_env):
        client, _, _ = daemon_env
        resp = client.post(
            "/runtime/provider/generate",
            json={"prompt": "ping"},
        )
        assert resp.status_code == 200
        body = resp.json()
        # _StubProvider.complete returns f"[stub] {prompt}"
        assert body["response"] == "[stub] ping"
        assert body["provider"] == "local"
        assert body["task_kind"] == "conversation"
        # Stub exposes a models dict with "stub:latest" for every TaskKind.
        assert body["model"] == "stub:latest"

    def test_generate_passes_options_to_provider(self, daemon_env, monkeypatch):
        """system / max_tokens / temperature / task_kind reach .complete()."""
        client, app, _ = daemon_env
        captured: dict = {}

        async def capturing_complete(prompt, *, task_kind, system=None, max_tokens=None, **kwargs):
            captured["prompt"] = prompt
            captured["task_kind"] = task_kind
            captured["system"] = system
            captured["max_tokens"] = max_tokens
            captured["kwargs"] = kwargs
            return "ok"

        provider = app.state.providers.active()
        monkeypatch.setattr(provider, "complete", capturing_complete, raising=False)

        resp = client.post(
            "/runtime/provider/generate",
            json={
                "prompt": "hi",
                "system": "be terse",
                "task_kind": "classify",
                "max_tokens": 64,
                "temperature": 0.2,
            },
        )
        assert resp.status_code == 200
        assert captured["prompt"] == "hi"
        assert captured["system"] == "be terse"
        assert captured["max_tokens"] == 64
        # TaskKind is an enum; stringly compare via .value to avoid import
        # juggling at the test layer.
        assert captured["task_kind"].value == "classify"
        assert captured["kwargs"].get("temperature") == 0.2

    def test_generate_empty_prompt_422(self, daemon_env):
        client, _, _ = daemon_env
        resp = client.post("/runtime/provider/generate", json={"prompt": ""})
        # Pydantic min_length=1 → 422
        assert resp.status_code == 422

    def test_generate_unavailable_maps_to_503(self, daemon_env, monkeypatch):
        """ProviderUnavailable → 503 with a clear detail string."""
        from forest_soul_forge.daemon.providers import ProviderUnavailable

        client, app, _ = daemon_env

        async def boom(*_a, **_k):
            raise ProviderUnavailable("ollama unreachable at http://stub")

        monkeypatch.setattr(app.state.providers.active(), "complete", boom, raising=False)

        resp = client.post("/runtime/provider/generate", json={"prompt": "p"})
        assert resp.status_code == 503
        assert "unavailable" in resp.json()["detail"].lower()

    def test_generate_disabled_maps_to_503(self, daemon_env, monkeypatch):
        """ProviderDisabled → 503 with a different detail wording."""
        from forest_soul_forge.daemon.providers import ProviderDisabled

        client, app, _ = daemon_env

        async def disabled(*_a, **_k):
            raise ProviderDisabled("frontier_enabled=False")

        monkeypatch.setattr(app.state.providers.active(), "complete", disabled, raising=False)

        resp = client.post("/runtime/provider/generate", json={"prompt": "p"})
        assert resp.status_code == 503
        assert "disabled" in resp.json()["detail"].lower()

    def test_generate_provider_error_maps_to_502(self, daemon_env, monkeypatch):
        """Generic ProviderError (e.g. upstream non-2xx) → 502 bad gateway."""
        from forest_soul_forge.daemon.providers import ProviderError

        client, app, _ = daemon_env

        async def upstream_500(*_a, **_k):
            raise ProviderError("local model returned 500: out of memory")

        monkeypatch.setattr(app.state.providers.active(), "complete", upstream_500, raising=False)

        resp = client.post("/runtime/provider/generate", json={"prompt": "p"})
        assert resp.status_code == 502
        assert "provider error" in resp.json()["detail"].lower()

    def test_generate_requires_token_when_configured(self, tmp_path: Path):
        """When FSF_API_TOKEN is set, the endpoint demands the header.

        Builds a fresh app with api_token configured because the shared
        daemon_env fixture deliberately runs token-less to keep the
        common case unblocked.
        """
        # Minimal seeded artifacts (mirrors daemon_env shape, smaller).
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

        settings = DaemonSettings(
            registry_db_path=tmp_path / "r.sqlite",
            artifacts_dir=tmp_path,
            audit_chain_path=audit,
            default_provider="local",
            frontier_enabled=False,
            api_token="t0p_s3cr3t",  # auth ON
        )
        app = build_app(settings)
        with TestClient(app) as client:
            app.state.providers = ProviderRegistry(
                providers={"local": _StubProvider(), "frontier": _StubProvider()},
                default="local",
            )

            # No header → 401.
            r1 = client.post("/runtime/provider/generate", json={"prompt": "p"})
            assert r1.status_code == 401

            # Wrong header → 401.
            r2 = client.post(
                "/runtime/provider/generate",
                json={"prompt": "p"},
                headers={"X-FSF-Token": "wrong"},
            )
            assert r2.status_code == 401

            # Correct header → 200.
            r3 = client.post(
                "/runtime/provider/generate",
                json={"prompt": "p"},
                headers={"X-FSF-Token": "t0p_s3cr3t"},
            )
            assert r3.status_code == 200
            assert r3.json()["response"] == "[stub] p"


class TestToolsCatalog:
    """ADR-0018 T4: read-only tool discovery endpoints.

    The daemon_env fixture builds the daemon with the real tool_catalog.yaml
    (config/tool_catalog.yaml is loaded at lifespan), so these tests
    exercise the actual catalog the frontend will see in production.
    """

    def test_catalog_returns_tools_and_archetypes(self, daemon_env):
        client, _, _ = daemon_env
        resp = client.get("/tools/catalog")
        assert resp.status_code == 200
        body = resp.json()
        assert "version" in body and body["version"]
        # The starter catalog defines >= 1 tool and >= 1 archetype.
        assert isinstance(body["tools"], list) and len(body["tools"]) >= 1
        assert isinstance(body["archetypes"], list) and len(body["archetypes"]) >= 1

        # Each tool entry has the documented shape.
        for td in body["tools"]:
            assert {"name", "version", "description", "side_effects",
                    "archetype_tags"} <= set(td.keys())
            assert td["side_effects"] in (
                "read_only", "network", "filesystem", "external"
            )

        # Archetype refs all resolve in the tools list (load-time integrity
        # check should guarantee this — surface it as a regression alarm).
        tool_keys = {f"{t['name']}.v{t['version']}" for t in body["tools"]}
        for arch in body["archetypes"]:
            assert "role" in arch
            for ref in arch["standard_tools"]:
                assert f"{ref['name']}.v{ref['version']}" in tool_keys

    def test_kit_returns_default_kit_for_known_role(self, daemon_env):
        client, _, _ = daemon_env
        resp = client.get("/tools/kit/network_watcher")
        assert resp.status_code == 200
        body = resp.json()
        assert body["role"] == "network_watcher"
        assert "catalog_version" in body
        # network_watcher has a non-empty default kit in the starter catalog.
        assert isinstance(body["tools"], list) and len(body["tools"]) >= 1
        for t in body["tools"]:
            assert {"name", "version", "description", "side_effects",
                    "constraints", "applied_rules"} <= set(t.keys())
            # Pre-profile defaults: no rules fired, conservative defaults.
            assert t["applied_rules"] == []
            assert t["constraints"]["max_calls_per_session"] == 1000
            assert t["constraints"]["requires_human_approval"] is False
            assert t["constraints"]["audit_every_call"] is True

    def test_kit_unknown_role_returns_empty_kit(self, daemon_env):
        # Roles without a catalog archetype entry get an empty kit, not a
        # 404 — the trait engine knows about more roles than the catalog
        # may have shipped archetypes for, and the UI should render
        # "no default tools" rather than break.
        client, _, _ = daemon_env
        resp = client.get("/tools/kit/no_such_archetype")
        assert resp.status_code == 200
        body = resp.json()
        assert body["role"] == "no_such_archetype"
        assert body["tools"] == []

    def test_preview_includes_resolved_tools(self, daemon_env):
        # The /preview response now carries `resolved_tools` so the
        # frontend can render policy-applied per-tool badges. The
        # network_watcher kit includes dns_lookup (side_effects=network),
        # which a high-caution + high-thoroughness profile flips to
        # requires_human_approval=True with max_calls_per_session=50.
        client, _, _ = daemon_env
        resp = client.post(
            "/preview",
            json={
                "profile": {
                    "role": "network_watcher",
                    "trait_values": {"caution": 90, "thoroughness": 90},
                },
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "resolved_tools" in body
        assert isinstance(body["resolved_tools"], list)
        assert len(body["resolved_tools"]) >= 1

        by_name = {t["name"]: t for t in body["resolved_tools"]}
        # dns_lookup is a network_watcher default and is a side_effects=
        # network tool — both rules should fire under this profile.
        if "dns_lookup" in by_name:
            t = by_name["dns_lookup"]
            assert t["side_effects"] == "network"
            assert t["constraints"]["requires_human_approval"] is True
            assert t["constraints"]["max_calls_per_session"] == 50
            assert "high_caution_approval_on_side_effects" in t["applied_rules"]
            assert "high_thoroughness_caps_external_calls" in t["applied_rules"]

    def test_preview_resolved_tools_respects_overrides(self, daemon_env):
        # tools_remove drops a default; the response's resolved_tools
        # block reflects that.
        client, _, _ = daemon_env
        # Baseline kit size.
        base = client.post(
            "/preview",
            json={"profile": {"role": "network_watcher"}},
        ).json()
        base_count = len(base["resolved_tools"])
        base_names = {t["name"] for t in base["resolved_tools"]}
        # Pick any one to remove that's actually present.
        if "packet_query" in base_names:
            removed = "packet_query"
        else:
            removed = next(iter(base_names))

        cut = client.post(
            "/preview",
            json={
                "profile": {"role": "network_watcher"},
                "tools_remove": [removed],
            },
        ).json()
        assert len(cut["resolved_tools"]) == base_count - 1
        assert removed not in {t["name"] for t in cut["resolved_tools"]}
        # Removing a tool changes the constitution_hash (tools are part
        # of canonical_body per ADR-0018).
        assert cut["constitution_hash_derived"] != base["constitution_hash_derived"]

    def test_preview_unknown_tools_remove_returns_400(self, daemon_env):
        client, _, _ = daemon_env
        resp = client.post(
            "/preview",
            json={
                "profile": {"role": "network_watcher"},
                "tools_remove": ["definitely_not_a_real_tool"],
            },
        )
        assert resp.status_code == 400
        assert "definitely_not_a_real_tool" in resp.json()["detail"]


class TestRegisteredTools:
    """``GET /tools/registered`` — runtime view of the tool registry."""

    def test_lists_registered_builtins(self, daemon_env):
        client, _, _ = daemon_env
        resp = client.get("/tools/registered")
        assert resp.status_code == 200
        body = resp.json()
        names = {t["name"] for t in body["tools"]}
        # Built-ins registered at lifespan.
        assert "timestamp_window" in names
        assert "memory_recall" in names
        assert "memory_write" in names
        assert body["count"] == len(body["tools"])

    def test_classifies_builtin_source(self, daemon_env):
        client, _, _ = daemon_env
        resp = client.get("/tools/registered")
        body = resp.json()
        timestamp = next(
            t for t in body["tools"]
            if t["name"] == "timestamp_window"
        )
        assert timestamp["source"] == "builtin"
        assert timestamp["side_effects"] == "read_only"

    def test_in_catalog_field_set(self, daemon_env):
        client, _, _ = daemon_env
        resp = client.get("/tools/registered")
        body = resp.json()
        # Built-ins should be in the catalog; the tool_catalog.yaml
        # in this repo lists them.
        for t in body["tools"]:
            if t["source"] == "builtin":
                # in_catalog reflects whether the YAML lists it. The
                # readonly test fixture uses an empty catalog, so the
                # built-ins won't be there. Either is fine — we just
                # want the field present.
                assert isinstance(t["in_catalog"], bool)
