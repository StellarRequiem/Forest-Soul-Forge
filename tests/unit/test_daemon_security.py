"""Tests for ADR-0062 T6 — /security/* operator-facing router.

Coverage:
- GET /security/status returns combined summary card
- GET /security/iocs returns the catalog with rule shape
- GET /security/recent-scans returns agent_security_scan_completed
  events only (filters other types out)
- GET /security/quarantined returns empty when no REJECTED.md
  exists; returns dir info when one does
- POST /security/reload returns post-reload rule_count
- ADR tranche list reports all 6 shipped
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
IOC_CATALOG = REPO_ROOT / "config" / "security_iocs.yaml"


class _StubProvider:
    name = "stub"

    async def status(self):
        return {"ok": True}


@pytest.fixture
def env(tmp_path: Path):
    for p in (TRAIT_TREE, CONST_TEMPLATES, TOOL_CATALOG, GENRES, IOC_CATALOG):
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
    def test_status_returns_rule_count(self, env):
        client, _ = env
        r = client.get("/security/status")
        assert r.status_code == 200
        body = r.json()
        assert "ioc_rule_count" in body
        assert body["ioc_rule_count"] >= 1
        for k in (
            "refused_last_24h", "allowed_last_24h", "critical_last_24h",
            "quarantined_count", "surface_counts",
        ):
            assert k in body
        # All 6 tranches present.
        assert body["adr_tranches_shipped"] == [
            "T1", "T2", "T3", "T4", "T5", "T6",
        ]

    def test_status_catalog_errors_is_list(self, env):
        client, _ = env
        r = client.get("/security/status")
        assert isinstance(r.json()["ioc_catalog_errors"], list)


class TestIocsEndpoint:
    def test_iocs_returns_rule_list(self, env):
        client, _ = env
        r = client.get("/security/iocs")
        assert r.status_code == 200
        body = r.json()
        assert body["rule_count"] >= 1
        assert isinstance(body["rules"], list)
        # Each rule has the expected shape.
        rule = body["rules"][0]
        for k in ("id", "severity", "pattern", "applies_to", "rationale", "references"):
            assert k in rule

    def test_iocs_ordered_critical_first(self, env):
        client, _ = env
        r = client.get("/security/iocs")
        body = r.json()
        # CRITICAL rules sort before HIGH, etc.
        rank = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0}
        ranks = [rank.get(r["severity"], -1) for r in body["rules"]]
        assert ranks == sorted(ranks, reverse=True)


class TestRecentScansEndpoint:
    def test_empty_chain_returns_empty(self, env):
        client, _ = env
        r = client.get("/security/recent-scans")
        assert r.status_code == 200
        # Genesis is in the chain but is not a security scan event.
        assert r.json()["count"] == 0

    def test_filters_out_non_scan_events(self, env):
        client, app = env
        chain = app.state.audit_chain
        chain.append("agent_created", {"role": "test"})
        chain.append(
            "agent_security_scan_completed",
            {
                "install_kind": "marketplace",
                "staging_dir": "/tmp/x",
                "decision": "refuse",
                "refused_on_tier": "CRITICAL",
                "critical_count": 1, "high_count": 0,
                "medium_count": 0, "low_count": 0, "info_count": 0,
            },
            agent_dna=None,
        )
        r = client.get("/security/recent-scans")
        events = r.json()["events"]
        assert len(events) == 1
        assert events[0]["event_type"] == "agent_security_scan_completed"


class TestQuarantinedEndpoint:
    def test_empty_when_no_rejection_markers(self, env):
        client, _ = env
        r = client.get("/security/quarantined")
        assert r.status_code == 200
        body = r.json()
        # No REJECTED.md anywhere → empty list.
        assert body["count"] == 0
        assert body["quarantined"] == []


class TestReloadEndpoint:
    def test_reload_returns_rule_count(self, env):
        client, _ = env
        r = client.post("/security/reload", json={})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["rule_count"] >= 1
        assert isinstance(body["catalog_errors"], list)
