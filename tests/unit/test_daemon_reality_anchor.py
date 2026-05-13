"""Tests for ADR-0063 T7 — /reality-anchor/* operator-facing router.

Coverage:
- GET /reality-anchor/status returns the combined summary
- GET /reality-anchor/ground-truth lists facts
- GET /reality-anchor/recent-events returns reality_anchor_* events
  (filters out non-anchor events)
- GET /reality-anchor/corrections lists repeat offenders
- POST /reality-anchor/reload returns fact_count + errors
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forest_soul_forge.daemon.app import build_app
from forest_soul_forge.daemon.config import DaemonSettings
from forest_soul_forge.daemon.providers import ProviderRegistry


REPO_ROOT = Path(__file__).resolve().parents[2]
TRAIT_TREE = REPO_ROOT / "config" / "trait_tree.yaml"
CONST_TEMPLATES = REPO_ROOT / "config" / "constitution_templates.yaml"
TOOL_CATALOG = REPO_ROOT / "config" / "tool_catalog.yaml"
GENRES = REPO_ROOT / "config" / "genres.yaml"
GROUND_TRUTH = REPO_ROOT / "config" / "ground_truth.yaml"


class _StubProvider:
    name = "stub"

    async def status(self):
        return {"ok": True}


@pytest.fixture
def env(tmp_path: Path):
    for p in (TRAIT_TREE, CONST_TEMPLATES, TOOL_CATALOG, GENRES, GROUND_TRUTH):
        if not p.exists():
            pytest.skip(f"required config missing: {p}")

    settings = DaemonSettings(
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
        allow_write_endpoints=True,
        enrich_narrative_default=False,
        api_token=None,
        insecure_no_token=True,
    )
    app = build_app(settings)
    with TestClient(app) as client:
        app.state.providers = ProviderRegistry(
            providers={"local": _StubProvider(), "frontier": _StubProvider()},
            default="local",
        )
        yield client, app


class TestStatusEndpoint:
    def test_status_returns_fact_count(self, env):
        client, _ = env
        r = client.get("/reality-anchor/status")
        assert r.status_code == 200
        body = r.json()
        assert "fact_count" in body
        assert body["fact_count"] >= 1
        assert "refused_last_24h" in body
        assert "flagged_last_24h" in body
        assert "repeat_offender_24h" in body
        assert "total_corrections" in body
        assert body["adr_tranches_shipped"] == [
            "T1", "T2", "T3", "T4", "T5", "T6", "T7",
        ]

    def test_status_includes_catalog_errors_list(self, env):
        client, _ = env
        r = client.get("/reality-anchor/status")
        assert r.status_code == 200
        # Always a list, even when empty.
        assert isinstance(r.json()["catalog_errors"], list)


class TestGroundTruthEndpoint:
    def test_ground_truth_returns_fact_list(self, env):
        client, _ = env
        r = client.get("/reality-anchor/ground-truth")
        assert r.status_code == 200
        body = r.json()
        assert body["fact_count"] >= 1
        assert isinstance(body["facts"], list)
        # Each fact has the full shape.
        f = body["facts"][0]
        for key in (
            "id", "statement", "domain_keywords", "canonical_terms",
            "forbidden_terms", "severity",
        ):
            assert key in f

    def test_license_fact_present(self, env):
        client, _ = env
        r = client.get("/reality-anchor/ground-truth")
        ids = {f["id"] for f in r.json()["facts"]}
        # The bootstrap catalog includes the license fact (ELv2).
        assert "license" in ids


class TestRecentEventsEndpoint:
    def test_empty_chain_returns_empty_list(self, env):
        client, _ = env
        r = client.get("/reality-anchor/recent-events")
        assert r.status_code == 200
        body = r.json()
        # genesis is in the chain but isn't a reality_anchor_* event.
        assert body["count"] == 0
        assert body["events"] == []

    def test_filters_out_non_anchor_events(self, env):
        client, app = env
        # Inject a reality_anchor_refused and an unrelated event.
        chain = app.state.audit_chain
        chain.append("agent_created", {"role": "test"})  # non-anchor
        chain.append(
            "reality_anchor_refused",
            {
                "instance_id": "ag1", "tool_key": "x.v1",
                "fact_id": "license", "severity": "HIGH",
                "claim": "test claim",
            },
            agent_dna="a" * 12,
        )
        r = client.get("/reality-anchor/recent-events")
        assert r.status_code == 200
        types = {e["event_type"] for e in r.json()["events"]}
        assert types == {"reality_anchor_refused"}


class TestCorrectionsEndpoint:
    def test_empty_table_returns_empty(self, env):
        client, _ = env
        r = client.get("/reality-anchor/corrections")
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 0
        assert body["corrections"] == []

    def test_returns_only_repeats(self, env):
        client, app = env
        rac = app.state.registry.reality_anchor_corrections
        # First sighting — not a repeat.
        rac.bump_or_create(
            claim="hallucinated claim",
            fact_id="license", worst_severity="HIGH",
            now_iso="2026-05-12T10:00:00Z",
            agent_dna="a" * 12, instance_id="ag1",
            decision="warned", surface="dispatcher",
        )
        r = client.get("/reality-anchor/corrections")
        assert r.json()["count"] == 0  # default min=2

        # Bump again — now a repeat.
        rac.bump_or_create(
            claim="hallucinated claim",
            fact_id="license", worst_severity="HIGH",
            now_iso="2026-05-12T10:05:00Z",
            agent_dna="a" * 12, instance_id="ag1",
            decision="warned", surface="dispatcher",
        )
        r = client.get("/reality-anchor/corrections")
        body = r.json()
        assert body["count"] == 1
        row = body["corrections"][0]
        assert row["repetition_count"] == 2
        assert row["contradicts_fact_id"] == "license"


class TestReloadEndpoint:
    def test_reload_returns_post_reload_state(self, env):
        client, _ = env
        r = client.post("/reality-anchor/reload", json={})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["fact_count"] >= 1
        assert isinstance(body["catalog_errors"], list)
