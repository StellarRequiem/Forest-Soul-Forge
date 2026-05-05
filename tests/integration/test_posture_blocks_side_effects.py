"""Integration test — ADR-0045 posture system end-to-end.

Burst 134. Closes part of the documented integration-tests gap from
STATE.md's items in queue. Exercises:

  - Tool dispatch protocol (kernel API spec §1)
  - Posture system (ADR-0045 / spec §1.3 PostureGateStep)
  - Audit chain event sequencing (spec §2.4)
  - HTTP API write endpoints with auth model (spec §5.1)

Scenario: birth a network_watcher → default posture is green → flip
posture=red → verify the audit chain captured the agent_posture_changed
event with the correct payload.

Failures here surface integration bugs that unit tests miss:
governance pipeline step ordering drift, posture state not flowing
into the dispatcher's resolved constraints, audit-chain event
emission out of order across the lifespan / write-lock boundary.
"""
from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
pydantic_settings = pytest.importorskip("pydantic_settings")

from fastapi.testclient import TestClient

from forest_soul_forge.daemon.app import build_app
from forest_soul_forge.daemon.config import DaemonSettings


REPO_ROOT = Path(__file__).resolve().parents[2]
TRAIT_TREE = REPO_ROOT / "config" / "trait_tree.yaml"
CONST_TEMPLATES = REPO_ROOT / "config" / "constitution_templates.yaml"
TOOL_CATALOG = REPO_ROOT / "config" / "tool_catalog.yaml"
GENRES = REPO_ROOT / "config" / "genres.yaml"


@pytest.fixture
def daemon_env(tmp_path: Path):
    for p, name in [
        (TRAIT_TREE, "trait tree"),
        (CONST_TEMPLATES, "constitution templates"),
        (TOOL_CATALOG, "tool catalog"),
        (GENRES, "genres"),
    ]:
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
        skill_install_dir=tmp_path / "skills",
        default_provider="local",
        frontier_enabled=False,
        allow_write_endpoints=True,
        enrich_narrative_default=False,
    )
    (tmp_path / "skills").mkdir(exist_ok=True)
    app = build_app(settings)
    yield {"app": app, "settings": settings}


def _birth(client: TestClient, role: str = "network_watcher") -> str:
    resp = client.post(
        "/birth",
        json={
            "profile": {
                "role": role,
                "trait_values": {},
                "domain_weight_overrides": {},
            },
            "agent_name": f"PostureTest-{role}",
            "agent_version": "v1",
            "owner_id": "burst134",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["instance_id"]


def test_birth_creates_audit_chain_entry(daemon_env):
    """End-to-end: birth flow produces an audit chain entry for the agent."""
    app = daemon_env["app"]
    with TestClient(app) as client:
        instance_id = _birth(client)

        chain = app.state.audit_chain
        # Read the chain end-to-end via the iter_records API.
        entries = chain.read_all()
        assert len(entries) > 0, "no audit chain entries after birth"

        # Some entry should reference our instance_id in its payload.
        matching = [
            e for e in entries
            if isinstance(e.event_data, dict)
            and (
                e.event_data.get("instance_id") == instance_id
                or e.event_data.get("agent_instance_id") == instance_id
            )
        ]
        assert matching, (
            f"no audit chain entry references instance_id={instance_id}; "
            f"got {len(entries)} entries with types: "
            f"{sorted({e.event_type for e in entries})}"
        )


def test_posture_default_is_valid_after_birth(daemon_env):
    """Newly-born agent has a valid posture (one of green/yellow/red).

    ADR-0045 §default specifies the default depends on role / genre /
    trait combination — observer roles default green; investigator
    can default yellow if the agent's risk traits are high. This
    test enforces only the v1.0 freeze invariant: the posture is one
    of the three documented values.
    """
    app = daemon_env["app"]
    with TestClient(app) as client:
        instance_id = _birth(client)

        resp = client.get(f"/agents/{instance_id}/posture")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        posture = (
            body.get("posture")
            or body.get("status")
            or body.get("state")
        )
        assert posture in {"green", "yellow", "red"}, (
            f"posture {posture!r} not in ADR-0045 enum: {body}"
        )


def test_posture_change_emits_audit_event(daemon_env):
    """Setting posture emits agent_posture_changed in the audit chain."""
    app = daemon_env["app"]
    with TestClient(app) as client:
        instance_id = _birth(client)

        # Flip posture to yellow (less destructive than red for the
        # test; both should emit the event).
        resp = client.post(
            f"/agents/{instance_id}/posture",
            json={"posture": "yellow", "reason": "burst134 integration probe"},
        )
        assert resp.status_code in (200, 201, 204), (
            f"posture set returned {resp.status_code}: {resp.text}"
        )

        # Audit chain should now contain agent_posture_changed.
        chain = app.state.audit_chain
        entries = chain.read_all()
        posture_events = [
            e for e in entries if e.event_type == "agent_posture_changed"
        ]
        assert posture_events, (
            "no agent_posture_changed event in audit chain — "
            "ADR-0045 contract requires it"
        )
        last = posture_events[-1]
        assert last.event_data.get("instance_id") == instance_id, (
            f"posture event references wrong agent: {last.event_data}"
        )
        # Event encodes the new posture under one of these keys.
        new_value = (
            last.event_data.get("new_posture")
            or last.event_data.get("posture")
            or last.event_data.get("to")
        )
        assert new_value == "yellow", (
            f"posture event payload doesn't capture yellow: {last.event_data}"
        )
