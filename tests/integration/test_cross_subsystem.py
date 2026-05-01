"""Cross-subsystem integration trio — closes the v0.2 coverage gap.

Three integration tests that exercise paths the unit suites don't:

  1. ``test_dispatch_memory_delegate_round_trip`` — birth parent +
     spawn child, parent writes lineage memory, parent invokes
     ``delegate.v1`` to run a child skill that reads via
     ``memory_disclose.v1``. Verifies that memory.lineage scope +
     delegate's lineage gate + the audit chain ordering all work
     together. Single test, three subsystems.

  2. ``test_approval_queue_resume_and_audit_order`` — gates a tool
     behind ``requires_human_approval``, fires it (HTTP 202 with
     ticket), approves via the pending_calls endpoint, asserts
     resume succeeds AND the audit chain emits the canonical
     pending → approved → dispatched → succeeded sequence with
     coherent seq numbering.

  3. ``test_conversation_turn_to_audit_chain_coherence`` — Y2 path:
     birth a single-agent room, fire a turn with auto_respond=True,
     assert llm_think.v1 dispatched and the audit chain has
     conversation_turn (operator) → tool_call_dispatched →
     tool_call_succeeded → conversation_turn (agent) in order.

These three tests follow the same fixture pattern as
``test_full_forge_loop`` — TestClient + tmp_path + DaemonSettings +
``_FakeProvider``. They aren't fast (each spins a real daemon app)
but they're cheap relative to the bug surface they cover.
"""
from __future__ import annotations

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
# Fake provider — answers llm_think calls with a deterministic short string.
# Conversation tests rely on this so dispatcher → llm_think → audit chain
# is exercised end-to-end without a real model.
# ---------------------------------------------------------------------------
@dataclass
class _FakeProvider:
    name: str = "local"

    @property
    def models(self) -> dict:
        return {k: "stub:latest" for k in TaskKind}

    async def complete(self, prompt: str, **kwargs) -> str:
        # Deterministic short reply — keeps token estimates stable.
        return "ack"

    async def healthcheck(self):
        return ProviderHealth(
            name=self.name, status=ProviderStatus.OK, base_url="http://stub",
            models=self.models, details={"loaded": [], "missing": []},
            error=None,
        )


def _settings(tmp_path: Path, **overrides) -> DaemonSettings:
    """Build a DaemonSettings pointed at tmp_path. Caller can override
    any field (skill_install_dir, default_provider, etc.)."""
    base = dict(
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
    base.update(overrides)
    return DaemonSettings(**base)


def _wire_provider(app, provider) -> None:
    app.state.providers = ProviderRegistry(
        providers={"local": provider, "frontier": provider},
        default="local",
    )


def _check_configs():
    for p, name in [(TRAIT_TREE, "trait tree"),
                    (CONST_TEMPLATES, "constitution templates"),
                    (TOOL_CATALOG, "tool catalog")]:
        if not p.exists():
            pytest.skip(f"{name} missing at {p}")


# ---------------------------------------------------------------------------
# 1. dispatcher + memory + delegate round-trip
# ---------------------------------------------------------------------------
_PARENT_WRITE_SKILL = textwrap.dedent("""
schema_version: 1
name: stash_lineage_note
version: '1'
description: Parent stashes a memory at lineage scope.
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
      scope: lineage
output:
  entry_id: ${stash.entry_id}
""").strip()


_CHILD_DISCLOSE_SKILL = textwrap.dedent("""
schema_version: 1
name: read_parent_lineage_note
version: '1'
description: Child recalls episodic lineage memory.
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
      mode: lineage
      limit: 5
output:
  count:   ${hits.count}
  entries: ${hits.entries}
""").strip()


_PARENT_DELEGATE_SKILL = textwrap.dedent("""
schema_version: 1
name: parent_invokes_child
version: '1'
description: Parent delegates to child, child reads back lineage memory.
requires: [delegate.v1]
inputs:
  type: object
  required: [target, query]
  properties:
    target: {type: string}
    query:  {type: string}
steps:
  - id: relay
    tool: delegate.v1
    args:
      target_instance_id: ${inputs.target}
      skill_name:    read_parent_lineage_note
      skill_version: '1'
      reason: 'lineage memory disclosure round-trip integration test'
      inputs:
        query: ${inputs.query}
output:
  status: ${relay.status}
  output: ${relay.output}
""").strip()


def test_dispatch_memory_delegate_round_trip(tmp_path: Path):
    """Parent writes lineage memory; parent delegates to child;
    child reads it back via memory_recall in lineage mode.

    Asserts:
      - delegate succeeds with target=child
      - child's recall finds the parent's note
      - audit chain shows agent_delegated event linking the two
    """
    _check_configs()
    skill_install_dir = tmp_path / "installed"
    skill_install_dir.mkdir()
    (skill_install_dir / "stash_lineage_note.v1.yaml").write_text(
        _PARENT_WRITE_SKILL, encoding="utf-8",
    )
    (skill_install_dir / "read_parent_lineage_note.v1.yaml").write_text(
        _CHILD_DISCLOSE_SKILL, encoding="utf-8",
    )
    (skill_install_dir / "parent_invokes_child.v1.yaml").write_text(
        _PARENT_DELEGATE_SKILL, encoding="utf-8",
    )

    settings = _settings(tmp_path, skill_install_dir=skill_install_dir)
    app = build_app(settings)
    provider = _FakeProvider()

    UNIQUE = "lineage-token-marrow-9923"

    with TestClient(app) as client:
        _wire_provider(app, provider)

        # Birth parent — system_architect (researcher genre; kit ships
        # memory_write + memory_recall + delegate.v1, which we need for
        # the round-trip).
        resp = client.post("/birth", json={
            "profile": {
                "role": "system_architect",
                "trait_values": {},
                "domain_weight_overrides": {},
            },
            "agent_name": "ParentArchitect",
            "agent_version": "v1",
            "owner_id": "integ",
        })
        assert resp.status_code == 201, resp.text
        parent_id = resp.json()["instance_id"]

        # Spawn child of the same archetype so genre-spawn-compat doesn't
        # come into play — same role spawning itself is always allowed.
        resp = client.post("/spawn", json={
            "parent_instance_id": parent_id,
            "profile": {
                "role": "system_architect",
                "trait_values": {},
                "domain_weight_overrides": {},
            },
            "agent_name": "ChildArchitect",
            "agent_version": "v1",
            "owner_id": "integ",
        })
        assert resp.status_code == 201, resp.text
        child_id = resp.json()["instance_id"]

        # Parent writes a lineage-scoped memory.
        resp = client.post(
            f"/agents/{parent_id}/skills/run",
            json={
                "skill_name":    "stash_lineage_note",
                "skill_version": "1",
                "session_id":    "integ-1",
                "inputs":        {"body": UNIQUE},
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "succeeded", body
        assert body["output"]["entry_id"]

        # Parent delegates to child; child runs read_parent_lineage_note.
        resp = client.post(
            f"/agents/{parent_id}/skills/run",
            json={
                "skill_name":    "parent_invokes_child",
                "skill_version": "1",
                "session_id":    "integ-1",
                "inputs":        {"target": child_id, "query": "marrow"},
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "succeeded", body
        relay = body["output"]
        assert relay["status"] == "succeeded", relay
        sub_output = relay["output"]
        assert sub_output["count"] >= 1, sub_output
        contents = [e.get("content", "") for e in sub_output["entries"]]
        assert any(UNIQUE in c for c in contents), (
            f"child's lineage recall didn't find parent's note. Got: {contents}"
        )

        # Audit chain proves the delegation step.
        chain = app.state.audit_chain
        types = [e.event_type for e in chain.read_all()]
        assert "agent_delegated" in types, (
            f"agent_delegated missing from chain; got {types[-15:]}"
        )
        # The delegation precedes the child's skill_invoked.
        last_delegated = max(i for i, t in enumerate(types) if t == "agent_delegated")
        # After the delegation, the child's skill must have started.
        assert "skill_invoked" in types[last_delegated:], (
            "child skill_invoked missing after delegation"
        )


# ---------------------------------------------------------------------------
# 2. approval-queue resume + audit-chain order
# ---------------------------------------------------------------------------
def _force_approval_required(app, instance_id: str) -> None:
    """Flip the agent's on-disk constitution so timestamp_window.v1
    requires human approval.

    Block-scoped replacement — the constitution lists multiple tools
    each with their own ``requires_human_approval`` flag. Walking back
    from ``name: timestamp_window`` to the nearest preceding flag is
    the safe way to target the right block. Earlier ``replace(...,
    count=1)`` worked only when timestamp_window happened to be first
    in the kit; the 2026-04-30 C-1 dissection re-ordered the kit and
    broke that assumption.
    """
    registry = app.state.registry
    agent = registry.get_agent(instance_id)
    const_path = Path(agent.constitution_path)
    text = const_path.read_text(encoding="utf-8")
    marker_pos = text.find("name: timestamp_window")
    if marker_pos < 0:
        raise RuntimeError(
            "test fixture: timestamp_window not in agent's constitution"
        )
    prefix = text[:marker_pos]
    rha_pos = prefix.rfind("requires_human_approval: false")
    if rha_pos < 0:
        raise RuntimeError(
            "test fixture: timestamp_window has no requires_human_approval flag"
        )
    new_text = (
        text[:rha_pos]
        + "requires_human_approval: true"
        + text[rha_pos + len("requires_human_approval: false"):]
    )
    const_path.write_text(new_text, encoding="utf-8")


def test_approval_queue_resume_and_audit_order(tmp_path: Path):
    """Pending-call lifecycle: dispatch (gated) → approve → resume.

    Asserts:
      - first dispatch returns 202 with a ticket_id
      - approve resumes successfully
      - audit chain has tool_call_pending_approval → tool_call_approved
        → tool_call_dispatched → tool_call_succeeded in order
        (and call_count_after == 1, proving the counter only ticks
        once for the whole pending+resume lifecycle)
    """
    _check_configs()
    settings = _settings(tmp_path)
    app = build_app(settings)
    provider = _FakeProvider()

    with TestClient(app) as client:
        _wire_provider(app, provider)

        # Birth — post C-1 dissection (2026-04-30) network_watcher's
        # standard kit is just traffic_flow_local + timestamp_window
        # (both read_only, fits observer-genre ceiling). dns_lookup is
        # in the catalog but kept OUT of the standard kit so the
        # genre-tier check passes. Nothing to tools_remove anymore.
        resp = client.post("/birth", json={
            "profile": {
                "role": "network_watcher",
                "trait_values": {},
                "domain_weight_overrides": {},
            },
            "agent_name": "ApprovalGuineapig",
            "agent_version": "v1",
            "owner_id": "integ",
        })
        assert resp.status_code == 201, resp.text
        instance_id = resp.json()["instance_id"]

        _force_approval_required(app, instance_id)

        # Snapshot chain length BEFORE the dispatch so we can scope our
        # asserts to events emitted by this lifecycle.
        chain = app.state.audit_chain
        before_len = len(chain.read_all())

        # Dispatch — must gate.
        resp = client.post(
            f"/agents/{instance_id}/tools/call",
            json={
                "tool_name":    "timestamp_window",
                "tool_version": "1",
                "session_id":   "approval-1",
                "args":         {"expression": "last 5 minutes"},
            },
        )
        assert resp.status_code == 202, resp.text
        ticket_id = resp.json()["ticket_id"]

        # Approve.
        resp = client.post(
            f"/pending_calls/{ticket_id}/approve",
            json={"operator_id": "integ-operator"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "succeeded"
        assert body["call_count_after"] == 1, (
            "counter must increment exactly once across pending+resume; "
            f"got {body['call_count_after']}"
        )

        # Audit chain — extract our slice and assert the canonical order.
        all_events = chain.read_all()
        new_events = all_events[before_len:]
        types = [e.event_type for e in new_events]
        # Filter to just the approval lifecycle types — there may be
        # adjacent unrelated entries (e.g. registry mark_decided) but
        # we don't care about those, only the ordered subset.
        relevant = {
            "tool_call_pending_approval",
            "tool_call_approved",
            "tool_call_dispatched",
            "tool_call_succeeded",
        }
        ordered = [t for t in types if t in relevant]
        expected = [
            "tool_call_pending_approval",
            "tool_call_approved",
            "tool_call_dispatched",
            "tool_call_succeeded",
        ]
        assert ordered == expected, (
            f"approval lifecycle out of order.\n"
            f"  expected: {expected}\n"
            f"  got:      {ordered}\n"
            f"  full slice: {types}"
        )

        # And the seq numbers strictly increase (already guaranteed by
        # the chain itself, but spot-check for confidence).
        seqs = [e.seq for e in new_events if e.event_type in relevant]
        assert seqs == sorted(seqs), f"seq out of order: {seqs}"


# ---------------------------------------------------------------------------
# 3. conversation_turn → llm_think → audit chain coherence
# ---------------------------------------------------------------------------
def test_conversation_turn_to_audit_chain_coherence(tmp_path: Path):
    """Y2 path: 1 agent participant + auto_respond=True.

    Asserts:
      - operator's turn appended (returns turn_id + body_hash)
      - agent's response turn appended (chain_depth == 1)
      - audit chain has, in order: conversation_turn (operator) →
        tool_call_dispatched (llm_think) → tool_call_succeeded →
        conversation_turn (agent)
    """
    _check_configs()
    settings = _settings(tmp_path)
    app = build_app(settings)
    provider = _FakeProvider()

    with TestClient(app) as client:
        _wire_provider(app, provider)

        # Birth one agent — system_architect role kit ships llm_think.v1
        # (researcher genre, SW-track triune). The Y2 auto_respond path
        # dispatches llm_think against the participant.
        resp = client.post("/birth", json={
            "profile": {
                "role": "system_architect",
                "trait_values": {},
                "domain_weight_overrides": {},
            },
            "agent_name": "ConversationParticipant",
            "agent_version": "v1",
            "owner_id": "integ",
        })
        assert resp.status_code == 201, resp.text
        instance_id = resp.json()["instance_id"]

        # Sanity: role kit must include llm_think for this test to do
        # what it claims. Skip rather than false-fail if the kit drifts.
        registry = app.state.registry
        agent = registry.get_agent(instance_id)
        const_path = Path(agent.constitution_path)
        const_text = const_path.read_text(encoding="utf-8")
        if "llm_think" not in const_text:
            pytest.skip(
                "system_architect role kit doesn't include llm_think.v1; "
                "this test relies on the kit listing it. Update the kit or "
                "patch this fixture."
            )

        # Open conversation room.
        resp = client.post(
            "/conversations",
            json={
                "domain":           "integration",
                "operator_id":      "integ-operator",
                "retention_policy": "full_7d",
            },
        )
        assert resp.status_code == 201, resp.text
        cid = resp.json()["conversation_id"]

        # Add the agent as the sole participant.
        resp = client.post(
            f"/conversations/{cid}/participants",
            json={"instance_id": instance_id},
        )
        assert resp.status_code == 201, resp.text

        # Snapshot audit chain before the turn dispatch so our assertions
        # scope to just-this-turn events.
        chain = app.state.audit_chain
        before_len = len(chain.read_all())

        # Operator appends a turn with auto_respond=True. Single-agent
        # path → resolver picks the only participant; llm_think dispatched.
        resp = client.post(
            f"/conversations/{cid}/turns",
            json={
                "speaker":      "integ-operator",
                "body":         "ping",
                "auto_respond": True,
                "max_response_tokens": 64,
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["operator_turn"]["body_hash"]
        assert body["chain_depth"] == 1, body
        assert body["agent_dispatch_failed"] is False, body
        assert body["agent_turn"] is not None, body
        assert len(body["agent_turn_chain"]) == 1, body

        # Audit chain coherence — extract the slice for this turn.
        new_events = chain.read_all()[before_len:]
        types = [e.event_type for e in new_events]
        # The relevant ordered subset:
        relevant = {
            "conversation_turn",
            "tool_call_dispatched",
            "tool_call_succeeded",
        }
        ordered = [t for t in types if t in relevant]
        # Expected canonical order for a 1-agent auto_respond turn:
        # operator's conversation_turn → llm_think dispatched →
        # llm_think succeeded → agent's conversation_turn.
        expected_prefix = [
            "conversation_turn",       # operator
            "tool_call_dispatched",    # llm_think
            "tool_call_succeeded",     # llm_think
            "conversation_turn",       # agent's reply
        ]
        assert ordered[: len(expected_prefix)] == expected_prefix, (
            f"conversation+llm_think order wrong.\n"
            f"  expected prefix: {expected_prefix}\n"
            f"  got:             {ordered}\n"
            f"  full slice:      {types}"
        )

        # And the conversation_turn count: exactly two.
        conv_turns = [t for t in types if t == "conversation_turn"]
        assert len(conv_turns) == 2, (
            f"expected 2 conversation_turn events (operator + agent); "
            f"got {len(conv_turns)} in {types}"
        )
