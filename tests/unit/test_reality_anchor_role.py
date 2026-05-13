"""Tests for ADR-0063 T4 — reality_anchor role + singleton enforcement.

Coverage:
- The reality_anchor role is in trait_tree.yaml, genres.yaml,
  tool_catalog.yaml, constitution_templates.yaml
- First /birth of role=reality_anchor succeeds
- Second /birth of role=reality_anchor returns 409 (singleton)
- 409 detail names the existing agent so the operator can find it
- Archiving the first then birthing a second succeeds
- A reality_anchor birth lands the expected kit (verify_claim.v1,
  memory_recall.v1, audit_chain_verify.v1, llm_think.v1, delegate.v1)
- The constitution template embeds the role's policies + the
  self-referential reality_anchor.enabled=true flag
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from forest_soul_forge.daemon.app import build_app
from forest_soul_forge.daemon.config import DaemonSettings
from forest_soul_forge.daemon.providers import ProviderRegistry


REPO_ROOT = Path(__file__).resolve().parents[2]
TRAIT_TREE = REPO_ROOT / "config" / "trait_tree.yaml"
CONST_TEMPLATES = REPO_ROOT / "config" / "constitution_templates.yaml"
TOOL_CATALOG = REPO_ROOT / "config" / "tool_catalog.yaml"
GENRES = REPO_ROOT / "config" / "genres.yaml"


# ===========================================================================
# Catalog presence
# ===========================================================================


class TestCatalogPresence:
    def test_role_in_trait_tree(self):
        tt = yaml.safe_load(TRAIT_TREE.read_text(encoding="utf-8"))
        assert "reality_anchor" in tt["roles"]

    def test_role_in_guardian_genre(self):
        gn = yaml.safe_load(GENRES.read_text(encoding="utf-8"))
        assert "reality_anchor" in gn["genres"]["guardian"]["roles"]

    def test_role_in_tool_catalog(self):
        tc = yaml.safe_load(TOOL_CATALOG.read_text(encoding="utf-8"))
        kit = tc["archetypes"]["reality_anchor"]["standard_tools"]
        # The kit must include verify_claim.v1 — the role's reason
        # to exist.
        assert "verify_claim.v1" in kit
        assert "memory_recall.v1" in kit
        assert "llm_think.v1" in kit

    def test_constitution_template_policies(self):
        ct = yaml.safe_load(CONST_TEMPLATES.read_text(encoding="utf-8"))
        template = ct["role_base"]["reality_anchor"]
        # Four load-bearing policies per ADR-0063 D6.
        policy_ids = {p["id"] for p in template["policies"]}
        assert "forbid_action_taking" in policy_ids
        assert "forbid_ground_truth_mutation" in policy_ids
        assert "require_citation" in policy_ids
        assert "forbid_low_confidence_contradicted" in policy_ids
        # Self-referential opt-in flag: the anchor is checked
        # against ground truth on its own claims too.
        assert template.get("reality_anchor", {}).get("enabled") is True


# ===========================================================================
# Singleton enforcement at /birth
# ===========================================================================


class _StubProvider:
    """Minimal stub so /healthz doesn't try to hit Ollama."""

    name = "stub"

    async def status(self):
        return {"ok": True}

    async def complete(self, *_args, **_kwargs):
        return {"text": "stub", "tokens_used": 0, "cost_usd": 0.0}


@pytest.fixture
def anchor_env(tmp_path: Path):
    for p, name in [(TRAIT_TREE, "trait tree"),
                    (CONST_TEMPLATES, "constitution templates"),
                    (TOOL_CATALOG, "tool catalog"),
                    (GENRES, "genres")]:
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


def _birth_body(name: str = "anchor"):
    return {
        "profile": {
            "role": "reality_anchor",
            "trait_values": {},
            "domain_weight_overrides": {},
        },
        "agent_name": name,
        "agent_version": "v1",
        "owner_id": "test-operator",
    }


class TestSingleton:
    def test_first_anchor_birth_succeeds(self, anchor_env):
        client, _ = anchor_env
        resp = client.post("/birth", json=_birth_body("anchor1"))
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["role"] == "reality_anchor"
        assert body["agent_name"] == "anchor1"

    def test_second_anchor_birth_refuses_with_409(self, anchor_env):
        client, _ = anchor_env
        first = client.post("/birth", json=_birth_body("anchor1"))
        assert first.status_code == 201

        second = client.post("/birth", json=_birth_body("anchor2"))
        assert second.status_code == 409, second.text
        detail = second.json()["detail"]
        # Detail must name the existing agent so the operator
        # knows what to archive.
        assert "singleton" in detail.lower()
        # The first agent's instance_id appears in the refusal.
        first_instance_id = first.json()["instance_id"]
        assert first_instance_id in detail

    def test_archive_then_rebirth_succeeds(self, anchor_env):
        client, _ = anchor_env
        first = client.post("/birth", json=_birth_body("anchor1"))
        assert first.status_code == 201
        first_instance_id = first.json()["instance_id"]

        # Archive the first. Endpoint is POST /archive (not
        # /agents/archive — that's a 405).
        arch = client.post("/archive", json={
            "instance_id": first_instance_id,
            "reason": "test cleanup",
        })
        assert arch.status_code == 200, arch.text

        # Now a second birth succeeds — the archived one doesn't
        # count as 'active'.
        second = client.post("/birth", json=_birth_body("anchor2"))
        assert second.status_code == 201, second.text
        assert second.json()["agent_name"] == "anchor2"

    def test_other_roles_not_blocked_by_anchor(self, anchor_env):
        """An existing reality_anchor must NOT prevent spawning
        other roles. Singleton-ness is per-role."""
        client, _ = anchor_env
        # Birth the anchor.
        first = client.post("/birth", json=_birth_body("anchor1"))
        assert first.status_code == 201

        # Birth a network_watcher — should not be blocked.
        other = client.post("/birth", json={
            "profile": {
                "role": "network_watcher",
                "trait_values": {},
                "domain_weight_overrides": {},
            },
            "agent_name": "watcher1",
            "agent_version": "v1",
            "owner_id": "test-operator",
        })
        assert other.status_code == 201, other.text


class TestBirthKit:
    def test_anchor_birth_lands_verify_claim_in_resolved_kit(self, anchor_env):
        client, app = anchor_env
        resp = client.post("/birth", json=_birth_body("anchor1"))
        assert resp.status_code == 201
        instance_id = resp.json()["instance_id"]

        # Pull the constitution file off disk and verify the kit.
        registry = app.state.registry
        agent = registry.get_agent(instance_id)
        const_text = Path(agent.constitution_path).read_text(encoding="utf-8")
        const_data = yaml.safe_load(const_text)
        # tools is a list of dicts in the constitution; pull
        # the tool names.
        kit_names = [t.get("name") for t in (const_data.get("tools") or [])]
        assert "verify_claim" in kit_names, (
            f"reality_anchor's constitution missing verify_claim — "
            f"got kit: {kit_names}"
        )
