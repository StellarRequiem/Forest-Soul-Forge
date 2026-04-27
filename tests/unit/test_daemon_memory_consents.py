"""Tests for the memory_consents router — ADR-0033 A2 T16.

Exercises POST/DELETE/GET /agents/{instance_id}/memory/consents end
to end via FastAPI's TestClient. Mirrors the existing
test_daemon_writes / test_daemon_tool_dispatch patterns.

Coverage:
- POST happy path: grant → returns 200 + revoked=False + audit event
- POST refusals: self-recipient, owner mismatch, missing entry, missing
  recipient
- DELETE happy path: revoke → returns 200 + revoked=True + audit event
- DELETE 404: when no active grant exists
- GET list: returns the agent's own grants only
- Audit chain: memory_consent_granted / memory_consent_revoked events
  fire with the right shape
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forest_soul_forge.core.memory import Memory
from forest_soul_forge.daemon.app import build_app
from forest_soul_forge.daemon.config import DaemonSettings


REPO_ROOT = Path(__file__).resolve().parents[2]
TRAIT_TREE = REPO_ROOT / "config" / "trait_tree.yaml"
CONST_TEMPLATES = REPO_ROOT / "config" / "constitution_templates.yaml"
TOOL_CATALOG = REPO_ROOT / "config" / "tool_catalog.yaml"
GENRES = REPO_ROOT / "config" / "genres.yaml"


@pytest.fixture
def consent_env(tmp_path: Path):
    """Daemon with two agents born + a memory entry on agent A's store
    that's eligible for consent grants (scope='consented')."""
    for p, name in [(TRAIT_TREE, "trait tree"),
                    (CONST_TEMPLATES, "constitution templates"),
                    (TOOL_CATALOG, "tool catalog")]:
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
    )
    app = build_app(settings)
    with TestClient(app) as client:
        # Birth two agents — A and B. Both as network_watcher so the
        # birth flow is well-trodden in existing tests.
        births = []
        for name in ("alpha", "beta"):
            resp = client.post("/birth", json={
                "profile": {
                    "role": "network_watcher",
                    "trait_values": {},
                    "domain_weight_overrides": {},
                },
                "agent_name": name,
            })
            assert resp.status_code in (200, 201), resp.text
            births.append(resp.json())
        a_id = births[0]["instance_id"]
        b_id = births[1]["instance_id"]

        # Plant a consent-scoped memory entry on A's store. We reach
        # into Memory directly because there's no /memory POST endpoint
        # yet — the runtime path is via memory_write.v1 dispatch which
        # is harder to drive in a unit test.
        registry = app.state.registry
        memory = Memory(conn=registry._conn)  # noqa: SLF001
        with app.state.write_lock:
            entry = memory.append(
                instance_id=a_id, agent_dna="a" * 12,
                content="a's consented finding",
                layer="episodic", scope="consented",
            )
            registry._conn.commit()  # noqa: SLF001

        yield {
            "client":   client,
            "app":      app,
            "a_id":     a_id,
            "b_id":     b_id,
            "entry_id": entry.entry_id,
            "memory":   memory,
        }


def _audit_events(app, event_type: str | None = None) -> list[dict]:
    """Read audit.jsonl into a list of parsed event dicts. If event_type
    is given, filter."""
    chain_path = Path(app.state.audit_chain.path)
    events = []
    for line in chain_path.read_text().splitlines():
        if not line.strip():
            continue
        events.append(json.loads(line))
    if event_type:
        events = [e for e in events if e.get("event_type") == event_type]
    return events


# ===========================================================================
# POST — grant
# ===========================================================================
class TestGrantConsent:
    def test_happy_path(self, consent_env):
        e = consent_env
        resp = e["client"].post(
            f"/agents/{e['a_id']}/memory/consents",
            json={
                "entry_id":           e["entry_id"],
                "recipient_instance": e["b_id"],
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["owner_instance"] == e["a_id"]
        assert body["entry_id"] == e["entry_id"]
        assert body["recipient_instance"] == e["b_id"]
        assert body["revoked"] is False

        # Audit event landed.
        granted = _audit_events(e["app"], "memory_consent_granted")
        assert len(granted) == 1
        ev = json.loads(granted[0]["event_json"]) if "event_json" in granted[0] else granted[0]["event_data"]
        assert ev["entry_id"] == e["entry_id"]
        assert ev["recipient_instance"] == e["b_id"]

    def test_self_recipient_refused(self, consent_env):
        e = consent_env
        resp = e["client"].post(
            f"/agents/{e['a_id']}/memory/consents",
            json={
                "entry_id":           e["entry_id"],
                "recipient_instance": e["a_id"],  # self
            },
        )
        assert resp.status_code == 400, resp.text
        assert "self-consent" in resp.text.lower()

    def test_foreign_owner_refused(self, consent_env):
        # B tries to grant consent on A's entry — refused with 403.
        e = consent_env
        resp = e["client"].post(
            f"/agents/{e['b_id']}/memory/consents",
            json={
                "entry_id":           e["entry_id"],
                "recipient_instance": e["a_id"],
            },
        )
        assert resp.status_code == 403, resp.text
        assert "owner" in resp.text.lower()

    def test_missing_entry_404(self, consent_env):
        e = consent_env
        resp = e["client"].post(
            f"/agents/{e['a_id']}/memory/consents",
            json={
                "entry_id":           "nonexistent-entry-id",
                "recipient_instance": e["b_id"],
            },
        )
        assert resp.status_code == 404

    def test_missing_recipient_404(self, consent_env):
        e = consent_env
        resp = e["client"].post(
            f"/agents/{e['a_id']}/memory/consents",
            json={
                "entry_id":           e["entry_id"],
                "recipient_instance": "ghost-instance",
            },
        )
        assert resp.status_code == 404

    def test_idempotent_regrant(self, consent_env):
        e = consent_env
        for _ in range(2):
            resp = e["client"].post(
                f"/agents/{e['a_id']}/memory/consents",
                json={
                    "entry_id":           e["entry_id"],
                    "recipient_instance": e["b_id"],
                },
            )
            assert resp.status_code == 200
        # Two grants emit two audit events even though the underlying
        # row is the same — the chain records the operator's
        # intent each time.
        granted = _audit_events(e["app"], "memory_consent_granted")
        assert len(granted) == 2


# ===========================================================================
# DELETE — revoke
# ===========================================================================
class TestRevokeConsent:
    def test_happy_path(self, consent_env):
        e = consent_env
        # Grant first.
        resp = e["client"].post(
            f"/agents/{e['a_id']}/memory/consents",
            json={
                "entry_id":           e["entry_id"],
                "recipient_instance": e["b_id"],
            },
        )
        assert resp.status_code == 200

        resp = e["client"].delete(
            f"/agents/{e['a_id']}/memory/consents/{e['entry_id']}/{e['b_id']}",
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["revoked"] is True

        revoked = _audit_events(e["app"], "memory_consent_revoked")
        assert len(revoked) == 1

    def test_404_when_no_active_grant(self, consent_env):
        e = consent_env
        resp = e["client"].delete(
            f"/agents/{e['a_id']}/memory/consents/{e['entry_id']}/{e['b_id']}",
        )
        assert resp.status_code == 404

    def test_revoke_then_regrant_emits_two_events(self, consent_env):
        e = consent_env
        post_url = f"/agents/{e['a_id']}/memory/consents"
        delete_url = f"/agents/{e['a_id']}/memory/consents/{e['entry_id']}/{e['b_id']}"
        e["client"].post(post_url, json={
            "entry_id": e["entry_id"], "recipient_instance": e["b_id"],
        })
        e["client"].delete(delete_url)
        e["client"].post(post_url, json={
            "entry_id": e["entry_id"], "recipient_instance": e["b_id"],
        })

        granted = _audit_events(e["app"], "memory_consent_granted")
        revoked = _audit_events(e["app"], "memory_consent_revoked")
        assert len(granted) == 2
        assert len(revoked) == 1


# ===========================================================================
# GET — list
# ===========================================================================
class TestListConsents:
    def test_returns_owner_grants_only(self, consent_env):
        e = consent_env
        # Plant another consented entry on B's store + grant to A.
        with e["app"].state.write_lock:
            other = e["memory"].append(
                instance_id=e["b_id"], agent_dna="b" * 12,
                content="b's consented finding",
                layer="episodic", scope="consented",
            )
            e["memory"].grant_consent(
                entry_id=other.entry_id,
                recipient_instance=e["a_id"],
                granted_by="op",
            )
            e["app"].state.registry._conn.commit()  # noqa: SLF001

        # Grant from A to B on A's entry.
        e["client"].post(
            f"/agents/{e['a_id']}/memory/consents",
            json={
                "entry_id":           e["entry_id"],
                "recipient_instance": e["b_id"],
            },
        )

        # GET A's grants — should show only A's own grant, not B's.
        resp = e["client"].get(f"/agents/{e['a_id']}/memory/consents")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["consents"][0]["entry_id"] == e["entry_id"]
        assert body["consents"][0]["recipient_instance"] == e["b_id"]

    def test_empty_list_for_agent_without_grants(self, consent_env):
        e = consent_env
        resp = e["client"].get(f"/agents/{e['b_id']}/memory/consents")
        assert resp.status_code == 200
        assert resp.json() == {
            "owner_instance": e["b_id"],
            "count": 0,
            "consents": [],
        }
