# Frozen copy of Smith's cycle 1.3 deliverable — the file
# Smith proposed but never read back. Used by cycle 1.5
# dispatch as in-prompt context so Smith can revise it.
#
# Original session: smith-cycle-1-plan-v3 (claude-sonnet-4-6,
# 36s, 9144 chars). The version below has the kwargs bug
# (`agent_id`/`metadata` for create_conversation) preserved
# verbatim — the v5 prompt asks Smith to fix that one helper
# only, leaving everything else identical.

# tests/unit/test_last_shortcut_route.py
"""
Unit tests for GET /conversations/{id}/last-shortcut (ADR-0056 cycle 1, Target B revised).
No mocks. Real DaemonSettings, real Registry, real AuditChain.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forest_soul_forge.daemon.app import build_app
from forest_soul_forge.daemon.config import DaemonSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TOKEN = "test-token-1234"
_AUTH = {"X-FSF-Token": _TOKEN}


def _build_client(tmp_path: Path) -> TestClient:
    settings = DaemonSettings(
        registry_db_path=tmp_path / "registry.sqlite",
        artifacts_dir=tmp_path / "souls",
        audit_chain_path=tmp_path / "audit.jsonl",
        api_token=_TOKEN,
        allow_write_endpoints=False,
    )
    app = build_app(settings)
    return TestClient(app)


def _seed_conversation(client: TestClient, conversation_id: str) -> None:
    """
    Insert a conversation directly via registry on app.state so we don't need
    write endpoints enabled. Falls back to POST if registry is accessible.
    """
    registry = client.app.state.registry
    registry.conversations.create_conversation(
        conversation_id=conversation_id,
        agent_id="agent-test-001",
        metadata={},
    )


def _append_shortcut_event(
    client: TestClient,
    conversation_id: str,
    *,
    shortcut_id: str | None = None,
    similarity: float = 0.95,
    action_kind: str = "replay",
    instance_id: str | None = None,
    seq_override: int | None = None,
) -> dict:
    """Write a tool_call_shortcut entry directly to the audit chain."""
    audit = client.app.state.audit_chain
    shortcut_id = shortcut_id or str(uuid.uuid4())
    instance_id = instance_id or str(uuid.uuid4())
    event_data = {
        "session_id": f"conv-{conversation_id}",
        "shortcut_id": shortcut_id,
        "shortcut_similarity": similarity,
        "shortcut_action_kind": action_kind,
        "instance_id": instance_id,
    }
    entry = audit.append(
        event_type="tool_call_shortcut",
        event_data=event_data,
    )
    return {
        "shortcut_id": shortcut_id,
        "shortcut_similarity": similarity,
        "shortcut_action_kind": action_kind,
        "instance_id": instance_id,
        "audit_seq": entry.seq,
        "timestamp": entry.timestamp,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLastShortcutRoute:

    def test_200_returns_correct_shape_and_values(self, tmp_path):
        """
        Conversation exists, audit chain has one matching tool_call_shortcut.
        Endpoint must return dict with all six real keys and correct values.
        """
        conv_id = "convo-abc-001"
        with _build_client(tmp_path) as client:
            _seed_conversation(client, conv_id)
            expected = _append_shortcut_event(client, conv_id, similarity=0.87, action_kind="skip")

            resp = client.get(f"/conversations/{conv_id}/last-shortcut", headers=_AUTH)

        assert resp.status_code == 200
        body = resp.json()

        # All six keys present
        for key in ("shortcut_id", "shortcut_similarity", "shortcut_action_kind",
                    "audit_seq", "timestamp", "instance_id"):
            assert key in body, f"missing key: {key}"

        assert body["shortcut_id"] == expected["shortcut_id"]
        assert body["shortcut_similarity"] == pytest.approx(0.87)
        assert body["shortcut_action_kind"] == "skip"
        assert body["instance_id"] == expected["instance_id"]
        assert body["audit_seq"] == expected["audit_seq"]
        assert body["timestamp"] == expected["timestamp"]

        # No extra phantom keys from old imagined schema
        for bad_key in ("summary", "token_count", "model", "conversation_id", "created_at"):
            assert bad_key not in body

    def test_404_conversation_not_found(self, tmp_path):
        """
        conversation_id absent from registry -> 404 with 'not found' in detail.
        """
        with _build_client(tmp_path) as client:
            resp = client.get("/conversations/does-not-exist/last-shortcut", headers=_AUTH)

        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]

    def test_404_no_matching_shortcut_events(self, tmp_path):
        """
        Conversation exists, audit chain has events but none match this conversation's
        session_id -> 404 with 'no shortcut events' in detail.
        """
        conv_id = "convo-xyz-002"
        other_id = "convo-other-999"
        with _build_client(tmp_path) as client:
            _seed_conversation(client, conv_id)
            # Append shortcut for a DIFFERENT conversation - should not match
            _append_shortcut_event(client, other_id, similarity=0.99)
            # Also append a non-shortcut event for our conversation
            client.app.state.audit_chain.append(
                event_type="agent_response",
                event_data={"session_id": f"conv-{conv_id}", "text": "hello"},
            )

            resp = client.get(f"/conversations/{conv_id}/last-shortcut", headers=_AUTH)

        assert resp.status_code == 404
        assert "no shortcut events" in resp.json()["detail"]

    def test_most_recent_wins_with_multiple_matching_events(self, tmp_path):
        """
        Multiple tool_call_shortcut events for same conversation exist.
        Endpoint walks reversed(tail(200)), so the LAST appended entry is returned.
        """
        conv_id = "convo-multi-003"
        with _build_client(tmp_path) as client:
            _seed_conversation(client, conv_id)

            _append_shortcut_event(client, conv_id, similarity=0.70, action_kind="replay",
                                   shortcut_id="old-shortcut")
            # Small sleep to ensure distinct timestamps if audit uses wall-clock seq
            time.sleep(0.01)
            latest = _append_shortcut_event(client, conv_id, similarity=0.99, action_kind="skip",
                                            shortcut_id="new-shortcut")

            resp = client.get(f"/conversations/{conv_id}/last-shortcut", headers=_AUTH)

        assert resp.status_code == 200
        body = resp.json()
        assert body["shortcut_id"] == "new-shortcut"
        assert body["shortcut_similarity"] == pytest.approx(0.99)
        assert body["shortcut_action_kind"] == "skip"
        assert body["audit_seq"] == latest["audit_seq"]
