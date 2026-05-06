"""ADR-0054 T5 (Burst 182) — memory_tag_outcome.v1 tests.

Coverage:

  Validation:
    - shortcut_id required + must be non-empty string
    - outcome required + must be one of {good, bad, neutral}
    - note (optional) must be a string when provided
    - note length capped at MAX_NOTE_LEN (300)

  Execution:
    - happy path: outcome=good → strengthen by 1; counters update
    - happy path: outcome=bad  → weaken by 1; counters update
    - happy path: outcome=neutral → no counter change but tool returns OK
    - output shape: shortcut_id, outcome, new_success_count,
      new_failure_count, new_reinforcement_score, soft_deleted
    - soft_deleted=True when reinforcement_score < 0
    - soft_deleted=False otherwise
    - refuses when ctx.procedural_shortcuts is None
      (pre-T6 daemons / unwired test contexts)
    - refuses when shortcut_id doesn't exist
    - refuses when shortcut_id belongs to a different agent
      (cross-agent tagging is a privilege-escalation surface)
"""
from __future__ import annotations

import asyncio
import itertools
from pathlib import Path

import numpy as np
import pytest

from forest_soul_forge.registry import Registry
from forest_soul_forge.registry.tables.procedural_shortcuts import (
    ProceduralShortcutsTable,
)
from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.memory_tag_outcome import (
    MAX_NOTE_LEN,
    MemoryTagOutcomeError,
    MemoryTagOutcomeTool,
    VALID_OUTCOMES,
)
from tests.unit.conftest import seed_stub_agent


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# DB fixture — real ProceduralShortcutsTable on a tmp sqlite for execute tests
# ---------------------------------------------------------------------------

@pytest.fixture
def shortcuts_table(tmp_path: Path):
    """Real on-disk ProceduralShortcutsTable + a seeded agent row so
    FK constraints don't reject test inserts."""
    reg = Registry.bootstrap(tmp_path / "tag-outcome.db")
    seed_stub_agent(reg, "i1")
    seed_stub_agent(reg, "i2")  # for cross-agent test

    table = ProceduralShortcutsTable(reg._conn)
    counter = itertools.count(1)

    def _put(instance_id="i1", shortcut_id=None, success=0, failure=0):
        # Auto-mint unique shortcut_ids when callers don't override
        # so multiple put() calls in one test don't collide on the
        # PRIMARY KEY constraint.
        sid = shortcut_id or f"sc-{next(counter)}"
        emb = np.array([0.6, 0.8], dtype=np.float32)
        table.put(
            shortcut_id=sid,
            instance_id=instance_id,
            situation_text="seed",
            situation_embedding=emb,
            action_kind="response",
            action_payload={"response": "yo"},
            learned_from_seq=1,
        )
        # Bypass strengthen/weaken to seed arbitrary starting counts
        # for tests that need it.
        if success:
            table.strengthen(sid, by=success)
        if failure:
            table.weaken(sid, by=failure)
        return sid

    yield table, _put


def _ctx(*, instance_id="i1", procedural_shortcuts=None):
    return ToolContext(
        instance_id=instance_id,
        agent_dna="d" * 12,
        role="assistant",
        genre="companion",
        session_id="s1",
        procedural_shortcuts=procedural_shortcuts,
    )


# ===========================================================================
# Validation
# ===========================================================================

class TestValidation:
    def test_shortcut_id_required(self):
        tool = MemoryTagOutcomeTool()
        with pytest.raises(ToolValidationError):
            tool.validate({"outcome": "good"})

    def test_shortcut_id_must_be_non_empty_string(self):
        tool = MemoryTagOutcomeTool()
        for bad in ["", "   ", None, 42, []]:
            with pytest.raises(ToolValidationError):
                tool.validate({"shortcut_id": bad, "outcome": "good"})

    def test_outcome_required(self):
        tool = MemoryTagOutcomeTool()
        with pytest.raises(ToolValidationError):
            tool.validate({"shortcut_id": "sc-1"})

    def test_outcome_must_be_valid_enum(self):
        tool = MemoryTagOutcomeTool()
        for bad in ["yes", "thumbs_up", "GOOD", None, 1, ""]:
            with pytest.raises(ToolValidationError):
                tool.validate({"shortcut_id": "sc-1", "outcome": bad})

    def test_outcome_accepts_each_valid_value(self):
        tool = MemoryTagOutcomeTool()
        for ok in VALID_OUTCOMES:
            tool.validate({"shortcut_id": "sc-1", "outcome": ok})

    def test_note_must_be_string_when_provided(self):
        tool = MemoryTagOutcomeTool()
        with pytest.raises(ToolValidationError):
            tool.validate({"shortcut_id": "sc-1", "outcome": "good", "note": 42})

    def test_note_length_cap(self):
        tool = MemoryTagOutcomeTool()
        too_long = "x" * (MAX_NOTE_LEN + 1)
        with pytest.raises(ToolValidationError):
            tool.validate({
                "shortcut_id": "sc-1", "outcome": "good", "note": too_long,
            })

    def test_note_at_cap_is_ok(self):
        tool = MemoryTagOutcomeTool()
        ok = "x" * MAX_NOTE_LEN
        tool.validate({"shortcut_id": "sc-1", "outcome": "good", "note": ok})


# ===========================================================================
# Execution
# ===========================================================================

class TestExecute:
    def test_good_outcome_strengthens(self, shortcuts_table):
        table, put = shortcuts_table
        sid = put(success=1, failure=0)  # starts at 1/0
        result = _run(MemoryTagOutcomeTool().execute(
            {"shortcut_id": sid, "outcome": "good"},
            _ctx(procedural_shortcuts=table),
        ))
        assert result.output["outcome"] == "good"
        assert result.output["new_success_count"] == 2
        assert result.output["new_failure_count"] == 0
        assert result.output["new_reinforcement_score"] == 2
        assert result.output["soft_deleted"] is False

    def test_bad_outcome_weakens(self, shortcuts_table):
        table, put = shortcuts_table
        sid = put(success=2, failure=1)
        result = _run(MemoryTagOutcomeTool().execute(
            {"shortcut_id": sid, "outcome": "bad"},
            _ctx(procedural_shortcuts=table),
        ))
        assert result.output["new_success_count"] == 2
        assert result.output["new_failure_count"] == 2
        assert result.output["new_reinforcement_score"] == 0
        # score==0 is NOT soft-deleted (only <0 is)
        assert result.output["soft_deleted"] is False

    def test_neutral_outcome_no_counter_change(self, shortcuts_table):
        table, put = shortcuts_table
        sid = put(success=3, failure=1)
        result = _run(MemoryTagOutcomeTool().execute(
            {"shortcut_id": sid, "outcome": "neutral"},
            _ctx(procedural_shortcuts=table),
        ))
        assert result.output["new_success_count"] == 3
        assert result.output["new_failure_count"] == 1
        assert result.output["new_reinforcement_score"] == 2
        assert result.output["outcome"] == "neutral"

    def test_soft_deleted_when_reinforcement_negative(self, shortcuts_table):
        table, put = shortcuts_table
        sid = put(success=0, failure=1)  # starting score = -1 already
        result = _run(MemoryTagOutcomeTool().execute(
            {"shortcut_id": sid, "outcome": "bad"},  # → success=0, failure=2
            _ctx(procedural_shortcuts=table),
        ))
        assert result.output["new_reinforcement_score"] == -2
        assert result.output["soft_deleted"] is True

    def test_refuses_when_substrate_unwired(self, shortcuts_table):
        """Pre-T6 daemons (or test contexts that didn't pass the
        table) leave ctx.procedural_shortcuts=None. Tool refuses
        cleanly rather than crashing."""
        with pytest.raises(MemoryTagOutcomeError) as exc:
            _run(MemoryTagOutcomeTool().execute(
                {"shortcut_id": "sc-x", "outcome": "good"},
                _ctx(procedural_shortcuts=None),
            ))
        assert "not wired" in str(exc.value)

    def test_refuses_unknown_shortcut_id(self, shortcuts_table):
        table, _ = shortcuts_table
        with pytest.raises(MemoryTagOutcomeError) as exc:
            _run(MemoryTagOutcomeTool().execute(
                {"shortcut_id": "nope-no-such-id", "outcome": "good"},
                _ctx(procedural_shortcuts=table),
            ))
        assert "not found" in str(exc.value)

    def test_refuses_cross_agent_tag(self, shortcuts_table):
        """A row owned by 'i2' must NOT be taggable by ctx.instance_id='i1'.
        Cross-agent tagging is a privilege-escalation surface; refused
        structurally, not via runtime hope."""
        table, put = shortcuts_table
        sid = put(instance_id="i2")
        with pytest.raises(MemoryTagOutcomeError) as exc:
            _run(MemoryTagOutcomeTool().execute(
                {"shortcut_id": sid, "outcome": "good"},
                _ctx(instance_id="i1", procedural_shortcuts=table),
            ))
        assert "different agent" in str(exc.value)

    def test_output_metadata_includes_note(self, shortcuts_table):
        table, put = shortcuts_table
        sid = put()
        result = _run(MemoryTagOutcomeTool().execute(
            {"shortcut_id": sid, "outcome": "good", "note": "right answer"},
            _ctx(procedural_shortcuts=table),
        ))
        assert result.metadata["note"] == "right answer"
        assert result.metadata["note_present"] is True

    def test_side_effect_summary_format(self, shortcuts_table):
        table, put = shortcuts_table
        sid = put(success=0, failure=2)  # already score=-2; bad → -3
        result = _run(MemoryTagOutcomeTool().execute(
            {"shortcut_id": sid, "outcome": "bad"},
            _ctx(procedural_shortcuts=table),
        ))
        assert "tag_outcome" in result.side_effect_summary
        assert "soft_deleted" in result.side_effect_summary

    def test_multiple_strengthen_calls_accumulate(self, shortcuts_table):
        table, put = shortcuts_table
        sid = put()  # starts 0/0
        for n in range(1, 4):
            result = _run(MemoryTagOutcomeTool().execute(
                {"shortcut_id": sid, "outcome": "good"},
                _ctx(procedural_shortcuts=table),
            ))
            assert result.output["new_success_count"] == n
        assert result.output["new_reinforcement_score"] == 3


# ===========================================================================
# Tool metadata
# ===========================================================================

class TestToolMetadata:
    def test_name_and_version(self):
        tool = MemoryTagOutcomeTool()
        assert tool.name == "memory_tag_outcome"
        assert tool.version == "1"

    def test_side_effects_is_read_only(self):
        """Per ADR-0054 T5: read_only because mutating per-instance
        counters is the agent's own state per ADR-0001 D2."""
        assert MemoryTagOutcomeTool().side_effects == "read_only"

    def test_required_initiative_level(self):
        """L2 — operator-initiated by design (chat-tab thumbs widget)."""
        assert MemoryTagOutcomeTool().required_initiative_level == "L2"
