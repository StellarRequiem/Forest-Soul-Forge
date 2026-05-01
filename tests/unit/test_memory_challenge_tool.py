"""Tests for memory_challenge.v1 (ADR-0027-amendment §7.4).

The tool stamps last_challenged_at on a memory entry without writing
a competing entry. Memory.mark_challenged is the underlying primitive;
memory_recall.v1's staleness flag surfaces the result.

Coverage:
- TestValidate         — argument validation (entry_id, challenger_id, note)
- TestExecute          — happy path, missing memory, missing entry,
                         visibility gate, idempotent re-challenge
- TestRegistration     — tool registers via register_builtins
- TestStalenessSurface — challenged entries flagged via memory_recall
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from forest_soul_forge.core.memory import Memory
from forest_soul_forge.registry import Registry
from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.memory_challenge import (
    MemoryChallengeError,
    MemoryChallengeTool,
)
from forest_soul_forge.tools.builtin.memory_recall import MemoryRecallTool
from tests.unit.conftest import seed_stub_agent


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def env(tmp_path):
    """Memory + ToolContext bound to a fresh registry."""
    reg = Registry.bootstrap(tmp_path / "reg.sqlite")
    seed_stub_agent(reg, "agent_a")
    seed_stub_agent(reg, "other_agent")
    memory = Memory(conn=reg._conn)  # noqa: SLF001
    ctx = ToolContext(
        instance_id="agent_a", agent_dna="d" * 12,
        role="observer", genre="observer", session_id="s1",
        constraints={}, memory=memory,
    )
    yield {"memory": memory, "ctx": ctx, "registry": reg}
    reg.close()


class TestValidate:
    def test_missing_entry_id_rejected(self):
        with pytest.raises(ToolValidationError, match="entry_id"):
            MemoryChallengeTool().validate({"challenger_id": "op"})

    def test_empty_entry_id_rejected(self):
        with pytest.raises(ToolValidationError, match="entry_id"):
            MemoryChallengeTool().validate(
                {"entry_id": "  ", "challenger_id": "op"}
            )

    def test_non_string_entry_id_rejected(self):
        with pytest.raises(ToolValidationError, match="entry_id"):
            MemoryChallengeTool().validate(
                {"entry_id": 42, "challenger_id": "op"}
            )

    def test_missing_challenger_id_rejected(self):
        with pytest.raises(ToolValidationError, match="challenger_id"):
            MemoryChallengeTool().validate({"entry_id": "e1"})

    def test_empty_challenger_id_rejected(self):
        with pytest.raises(ToolValidationError, match="challenger_id"):
            MemoryChallengeTool().validate(
                {"entry_id": "e1", "challenger_id": ""}
            )

    def test_note_too_long_rejected(self):
        with pytest.raises(ToolValidationError, match="too long"):
            MemoryChallengeTool().validate({
                "entry_id": "e1",
                "challenger_id": "op",
                "note": "x" * 501,
            })

    def test_note_at_limit_accepted(self):
        # 500 chars is exactly at the boundary; accepted.
        MemoryChallengeTool().validate({
            "entry_id": "e1",
            "challenger_id": "op",
            "note": "x" * 500,
        })

    def test_note_omitted_accepted(self):
        # Note is optional; omitting is fine.
        MemoryChallengeTool().validate(
            {"entry_id": "e1", "challenger_id": "op"}
        )


class TestExecute:
    def test_challenge_stamps_last_challenged_at(self, env):
        e = env["memory"].append(
            instance_id="agent_a", agent_dna="d" * 12,
            content="possibly stale", layer="semantic",
            claim_type="preference",
        )
        # Pre-challenge: last_challenged_at is NULL.
        before = env["memory"].get(e.entry_id)
        assert before.last_challenged_at is None

        out = _run(MemoryChallengeTool().execute(
            {"entry_id": e.entry_id, "challenger_id": "operator_alex",
             "note": "user disputed in latest session"},
            env["ctx"],
        ))
        # Output shape.
        assert out.output["challenged"] is True
        assert out.output["entry_id"] == e.entry_id
        assert out.output["challenger_id"] == "operator_alex"
        assert out.output["last_challenged_at"] is not None
        # DB row reflects the stamp.
        after = env["memory"].get(e.entry_id)
        assert after.last_challenged_at == out.output["last_challenged_at"]
        # Content / claim_type unchanged — challenge is orthogonal.
        assert after.content == before.content
        assert after.claim_type == before.claim_type

    def test_challenge_audit_event_metadata(self, env):
        e = env["memory"].append(
            instance_id="agent_a", agent_dna="d" * 12,
            content="thing", layer="episodic",
        )
        out = _run(MemoryChallengeTool().execute(
            {"entry_id": e.entry_id, "challenger_id": "operator_alex"},
            env["ctx"],
        ))
        # Runtime keys off audit_event_type to emit memory_challenged.
        assert out.metadata["audit_event_type"] == "memory_challenged"
        assert out.metadata["challenger_id"] == "operator_alex"
        assert out.metadata["note_present"] is False
        assert out.metadata["challenge_note"] is None

    def test_note_lands_in_metadata_and_summary(self, env):
        e = env["memory"].append(
            instance_id="agent_a", agent_dna="d" * 12,
            content="thing", layer="episodic",
        )
        out = _run(MemoryChallengeTool().execute(
            {"entry_id": e.entry_id, "challenger_id": "op",
             "note": "third-party source disagrees"},
            env["ctx"],
        ))
        assert out.metadata["note_present"] is True
        assert out.metadata["challenge_note"] == "third-party source disagrees"
        assert "third-party source disagrees" in out.side_effect_summary

    def test_missing_entry_raises(self, env):
        with pytest.raises(MemoryChallengeError, match="not found"):
            _run(MemoryChallengeTool().execute(
                {"entry_id": "nonexistent", "challenger_id": "op"},
                env["ctx"],
            ))

    def test_missing_memory_raises(self, env):
        ctx = ToolContext(
            instance_id="agent_a", agent_dna="d" * 12,
            role="observer", genre="observer", session_id="s1",
            constraints={}, memory=None,
        )
        with pytest.raises(MemoryChallengeError, match="not wired"):
            _run(MemoryChallengeTool().execute(
                {"entry_id": "e1", "challenger_id": "op"},
                ctx,
            ))

    def test_private_entry_owned_by_other_agent_refused(self, env):
        # other_agent's private entry. agent_a tries to challenge.
        # Visibility gate refuses (same as memory_verify.v1).
        e = env["memory"].append(
            instance_id="other_agent", agent_dna="d" * 12,
            content="other's private thought", layer="episodic",
            scope="private",
        )
        with pytest.raises(MemoryChallengeError, match="private to a different"):
            _run(MemoryChallengeTool().execute(
                {"entry_id": e.entry_id, "challenger_id": "op"},
                env["ctx"],
            ))

    def test_idempotent_rechallenge(self, env):
        # Re-challenging is allowed (operator may scrutinize same entry
        # twice). Second timestamp overwrites first; both events
        # would be in the audit chain.
        e = env["memory"].append(
            instance_id="agent_a", agent_dna="d" * 12,
            content="thing", layer="episodic",
        )
        first = _run(MemoryChallengeTool().execute(
            {"entry_id": e.entry_id, "challenger_id": "op"},
            env["ctx"],
        ))
        # Sleep to ensure a different timestamp on the second call.
        # _now_iso has 1-second granularity so we need a 1-sec wait.
        import time
        time.sleep(1.1)
        second = _run(MemoryChallengeTool().execute(
            {"entry_id": e.entry_id, "challenger_id": "op"},
            env["ctx"],
        ))
        # Both succeeded; second timestamp >= first.
        assert second.output["last_challenged_at"] >= first.output["last_challenged_at"]
        # DB reflects the most recent.
        row = env["memory"].get(e.entry_id)
        assert row.last_challenged_at == second.output["last_challenged_at"]


class TestRegistration:
    def test_memory_challenge_registered_at_lifespan(self):
        from forest_soul_forge.tools import ToolRegistry
        from forest_soul_forge.tools.builtin import register_builtins
        reg = ToolRegistry()
        register_builtins(reg)
        assert reg.has("memory_challenge", "1")


class TestStalenessSurface:
    """End-to-end: challenge an entry, then recall with staleness
    threshold, and see is_stale flip behavior. This is the user-facing
    proof that the new tool integrates with the recall surface."""

    def test_challenged_entry_appears_fresh_immediately_after(self, env):
        # Challenge stamps NOW; immediately after, the entry is fresh
        # against any non-trivial threshold.
        e = env["memory"].append(
            instance_id="agent_a", agent_dna="d" * 12,
            content="under scrutiny", layer="semantic",
            claim_type="preference",
        )
        # Backdate the entry far in the past.
        env["memory"].conn.execute(
            "UPDATE memory_entries SET created_at='2020-01-01T00:00:00Z' "
            "WHERE entry_id=?;",
            (e.entry_id,),
        )
        # Pre-challenge: stale at 30-day threshold (created 2020).
        out = _run(MemoryRecallTool().execute(
            {"staleness_threshold_days": 30}, env["ctx"],
        ))
        surfaced = next(
            x for x in out.output["entries"] if x["entry_id"] == e.entry_id
        )
        assert surfaced["is_stale"] is True

        # Challenge stamps last_challenged_at to NOW.
        _run(MemoryChallengeTool().execute(
            {"entry_id": e.entry_id, "challenger_id": "op"},
            env["ctx"],
        ))

        # Post-challenge: NOT stale (last_challenged_at is fresh).
        out = _run(MemoryRecallTool().execute(
            {"staleness_threshold_days": 30}, env["ctx"],
        ))
        surfaced = next(
            x for x in out.output["entries"] if x["entry_id"] == e.entry_id
        )
        assert surfaced["is_stale"] is False
        assert surfaced["last_challenged_at"] is not None
