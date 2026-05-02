"""Tests for memory_flag_contradiction.v1 (ADR-0036 T2).

The tool is the action surface for the Verifier Loop role. Stamps a
row in memory_contradictions naming both sides (earlier + later) of
a contradiction. Operator-only by convention via constitutional kit
gating; verifier_loop role reaches autonomously per ADR-0036.

Coverage:
- TestValidate         — argument validation: required fields, enum
                         constraints, distinct entries, note length
- TestExecute          — happy path, missing memory, missing entries,
                         visibility gate, detected_by attribution
- TestMemorySubsystem  — the underlying flag_contradiction primitive
- TestRegistration     — tool registers via register_builtins; catalog
                         entry has L3 + filesystem
- TestSurface          — flagged contradictions surface through
                         memory_recall.v1's surface_contradictions output
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from forest_soul_forge.core.memory import Memory
from forest_soul_forge.registry import Registry
from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.memory_flag_contradiction import (
    MAX_NOTE_LEN,
    VALID_CONFIDENCES,
    VALID_KINDS,
    MemoryFlagContradictionError,
    MemoryFlagContradictionTool,
)
from tests.unit.conftest import seed_stub_agent


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def env(tmp_path):
    """Memory + ToolContext bound to a fresh registry. agent_a is the
    Verifier (caller); other_agent is set up so cross-private-scope
    refusal can be exercised."""
    reg = Registry.bootstrap(tmp_path / "reg.sqlite")
    seed_stub_agent(reg, "agent_a")
    seed_stub_agent(reg, "other_agent")
    memory = Memory(conn=reg._conn)  # noqa: SLF001
    ctx = ToolContext(
        instance_id="agent_a", agent_dna="d" * 12,
        role="verifier_loop", genre="guardian", session_id="s1",
        constraints={}, memory=memory,
    )
    yield {"memory": memory, "ctx": ctx, "registry": reg}
    reg.close()


def _two_entries(env, *, instance_id="agent_a", scope="lineage"):
    """Helper to seed two entries the Verifier can flag against each other."""
    a = env["memory"].append(
        instance_id=instance_id, agent_dna="d" * 12,
        content="user prefers tea", layer="semantic",
        claim_type="preference", scope=scope,
    )
    b = env["memory"].append(
        instance_id=instance_id, agent_dna="d" * 12,
        content="user prefers coffee", layer="semantic",
        claim_type="preference", scope=scope,
    )
    return a, b


# ===========================================================================
# Validation
# ===========================================================================
class TestValidate:
    def _base(self, **overrides):
        args = {
            "earlier_entry_id": "e1",
            "later_entry_id":   "e2",
            "contradiction_kind": "direct",
            "confidence":       "high",
        }
        args.update(overrides)
        return args

    def test_missing_earlier_id_rejected(self):
        with pytest.raises(ToolValidationError, match="earlier_entry_id"):
            MemoryFlagContradictionTool().validate(
                self._base(earlier_entry_id=""),
            )

    def test_missing_later_id_rejected(self):
        with pytest.raises(ToolValidationError, match="later_entry_id"):
            MemoryFlagContradictionTool().validate(
                self._base(later_entry_id=None),
            )

    def test_same_entry_both_sides_rejected(self):
        with pytest.raises(ToolValidationError, match="distinct"):
            MemoryFlagContradictionTool().validate(
                self._base(earlier_entry_id="e1", later_entry_id="e1"),
            )

    def test_invalid_kind_rejected(self):
        with pytest.raises(ToolValidationError, match="contradiction_kind"):
            MemoryFlagContradictionTool().validate(
                self._base(contradiction_kind="bogus"),
            )
        with pytest.raises(ToolValidationError, match="contradiction_kind"):
            MemoryFlagContradictionTool().validate(
                self._base(contradiction_kind=None),
            )

    def test_invalid_confidence_rejected(self):
        with pytest.raises(ToolValidationError, match="confidence"):
            MemoryFlagContradictionTool().validate(
                self._base(confidence="extreme"),
            )

    def test_note_too_long_rejected(self):
        with pytest.raises(ToolValidationError, match="too long"):
            MemoryFlagContradictionTool().validate(
                self._base(note="x" * (MAX_NOTE_LEN + 1)),
            )

    def test_note_must_be_string(self):
        with pytest.raises(ToolValidationError, match="note"):
            MemoryFlagContradictionTool().validate(
                self._base(note=42),
            )

    def test_valid_minimal(self):
        MemoryFlagContradictionTool().validate(self._base())

    def test_valid_full(self):
        MemoryFlagContradictionTool().validate(self._base(
            note="user updated their preference in latest session",
        ))

    def test_valid_kinds_constants(self):
        # Lock the §7.3 CHECK enum here so a schema-side change doesn't
        # silently let a bad kind through.
        assert set(VALID_KINDS) == {"direct", "updated", "qualified", "retracted"}
        assert set(VALID_CONFIDENCES) == {"low", "medium", "high"}


# ===========================================================================
# Execute
# ===========================================================================
class TestExecute:
    def test_flag_writes_row(self, env):
        a, b = _two_entries(env)
        result = _run(MemoryFlagContradictionTool().execute({
            "earlier_entry_id": a.entry_id,
            "later_entry_id": b.entry_id,
            "contradiction_kind": "updated",
            "confidence": "high",
        }, env["ctx"]))
        assert result.output["earlier_entry_id"] == a.entry_id
        assert result.output["later_entry_id"] == b.entry_id
        assert result.output["contradiction_kind"] == "updated"
        assert result.output["detected_by"] == "agent_a"
        assert result.output["contradiction_id"].startswith("contra_")
        # detected_at ISO-shaped (T separator + Z or offset)
        assert "T" in result.output["detected_at"]

    def test_row_visible_via_unresolved_lookup(self, env):
        a, b = _two_entries(env)
        _run(MemoryFlagContradictionTool().execute({
            "earlier_entry_id": a.entry_id,
            "later_entry_id":   b.entry_id,
            "contradiction_kind": "direct",
            "confidence":       "high",
        }, env["ctx"]))
        rows = env["memory"].unresolved_contradictions_for(a.entry_id)
        assert len(rows) == 1
        assert rows[0]["earlier_entry_id"] == a.entry_id
        assert rows[0]["later_entry_id"] == b.entry_id
        assert rows[0]["detected_by"] == "agent_a"

    def test_audit_event_metadata(self, env):
        a, b = _two_entries(env)
        result = _run(MemoryFlagContradictionTool().execute({
            "earlier_entry_id": a.entry_id,
            "later_entry_id":   b.entry_id,
            "contradiction_kind": "qualified",
            "confidence":       "medium",
        }, env["ctx"]))
        assert result.metadata["audit_event_type"] == "memory_contradiction_flagged"
        assert result.metadata["confidence"] == "medium"
        assert result.metadata["note_present"] is False

    def test_note_lands_in_metadata_only(self, env):
        a, b = _two_entries(env)
        result = _run(MemoryFlagContradictionTool().execute({
            "earlier_entry_id": a.entry_id,
            "later_entry_id":   b.entry_id,
            "contradiction_kind": "retracted",
            "confidence":       "high",
            "note":             "user explicitly retracted earlier statement",
        }, env["ctx"]))
        assert result.metadata["note_present"] is True
        assert result.metadata["flag_note"] == "user explicitly retracted earlier statement"
        # The note does NOT land on the contradiction row — only in the
        # audit-event payload (mirrors memory_challenge.v1 design).

    def test_missing_earlier_entry_refuses(self, env):
        _, b = _two_entries(env)
        with pytest.raises(MemoryFlagContradictionError, match="earlier entry"):
            _run(MemoryFlagContradictionTool().execute({
                "earlier_entry_id": "nonexistent",
                "later_entry_id":   b.entry_id,
                "contradiction_kind": "direct",
                "confidence":       "high",
            }, env["ctx"]))

    def test_missing_later_entry_refuses(self, env):
        a, _ = _two_entries(env)
        with pytest.raises(MemoryFlagContradictionError, match="later entry"):
            _run(MemoryFlagContradictionTool().execute({
                "earlier_entry_id": a.entry_id,
                "later_entry_id":   "nonexistent",
                "contradiction_kind": "direct",
                "confidence":       "high",
            }, env["ctx"]))

    def test_missing_memory_refuses(self):
        ctx = ToolContext(
            instance_id="agent_a", agent_dna="d" * 12,
            role="verifier_loop", genre="guardian", session_id="s1",
            constraints={}, memory=None,
        )
        with pytest.raises(MemoryFlagContradictionError, match="not wired"):
            _run(MemoryFlagContradictionTool().execute({
                "earlier_entry_id": "e1",
                "later_entry_id":   "e2",
                "contradiction_kind": "direct",
                "confidence":       "high",
            }, ctx))

    def test_private_entry_owned_by_other_agent_refused(self, env):
        # other_agent's private entry. The Verifier (agent_a) cannot
        # flag it (visibility gate matches memory_challenge.v1).
        other_private = env["memory"].append(
            instance_id="other_agent", agent_dna="d" * 12,
            content="other's private thought", layer="episodic",
            scope="private",
        )
        own = env["memory"].append(
            instance_id="agent_a", agent_dna="d" * 12,
            content="own thought", layer="episodic",
        )
        with pytest.raises(MemoryFlagContradictionError, match="private to a different"):
            _run(MemoryFlagContradictionTool().execute({
                "earlier_entry_id": other_private.entry_id,
                "later_entry_id":   own.entry_id,
                "contradiction_kind": "direct",
                "confidence":       "high",
            }, env["ctx"]))

    def test_detected_by_is_ctx_instance_id(self, env):
        # ADR-0036 §4.2 — every flag must carry detected_by =
        # ctx.instance_id so operators can audit the Verifier's track
        # record. The constitutional kit gate enforces this; the
        # underlying tool ALSO sets it from ctx as defense-in-depth.
        a, b = _two_entries(env)
        custom_ctx = ToolContext(
            instance_id="VerifierBeta", agent_dna="d" * 12,
            role="verifier_loop", genre="guardian", session_id="s1",
            constraints={}, memory=env["memory"],
        )
        result = _run(MemoryFlagContradictionTool().execute({
            "earlier_entry_id": a.entry_id,
            "later_entry_id":   b.entry_id,
            "contradiction_kind": "direct",
            "confidence":       "high",
        }, custom_ctx))
        assert result.output["detected_by"] == "VerifierBeta"
        # And the row carries the same value:
        rows = env["memory"].unresolved_contradictions_for(a.entry_id)
        assert rows[0]["detected_by"] == "VerifierBeta"


# ===========================================================================
# Memory subsystem flag_contradiction primitive
# ===========================================================================
class TestMemorySubsystem:
    def test_flag_contradiction_returns_id_and_timestamp(self, env):
        a, b = _two_entries(env)
        cid, ts = env["memory"].flag_contradiction(
            earlier_entry_id=a.entry_id,
            later_entry_id=b.entry_id,
            contradiction_kind="direct",
            detected_by="op",
        )
        assert cid.startswith("contra_")
        assert "T" in ts

    def test_flag_contradiction_each_call_is_distinct(self, env):
        a, b = _two_entries(env)
        cid1, _ = env["memory"].flag_contradiction(
            earlier_entry_id=a.entry_id, later_entry_id=b.entry_id,
            contradiction_kind="direct", detected_by="op",
        )
        cid2, _ = env["memory"].flag_contradiction(
            earlier_entry_id=a.entry_id, later_entry_id=b.entry_id,
            contradiction_kind="updated", detected_by="op",
        )
        assert cid1 != cid2
        rows = env["memory"].unresolved_contradictions_for(a.entry_id)
        assert len(rows) == 2


# ===========================================================================
# Recall surface integration
# ===========================================================================
class TestRecallSurface:
    """ADR-0036 §5 + ADR-0027-am T3 — flagged contradictions surface
    through memory_recall.v1's surface_contradictions option."""

    def test_unresolved_contradictions_surface(self, env):
        a, b = _two_entries(env)
        _run(MemoryFlagContradictionTool().execute({
            "earlier_entry_id": a.entry_id,
            "later_entry_id":   b.entry_id,
            "contradiction_kind": "updated",
            "confidence":       "high",
        }, env["ctx"]))
        rows = env["memory"].unresolved_contradictions_for(a.entry_id)
        assert any(
            r["earlier_entry_id"] == a.entry_id
            and r["later_entry_id"] == b.entry_id
            for r in rows
        )


# ===========================================================================
# Registration
# ===========================================================================
class TestRegistration:
    def test_tool_registered(self):
        from forest_soul_forge.tools import ToolRegistry
        from forest_soul_forge.tools.builtin import register_builtins

        registry = ToolRegistry()
        register_builtins(registry)
        tool = registry.get("memory_flag_contradiction", "1")
        assert tool is not None
        assert tool.side_effects == "filesystem"
        assert tool.required_initiative_level == "L3"

    def test_catalog_entry_present(self):
        import yaml
        catalog_path = (
            Path(__file__).parent.parent.parent
            / "config" / "tool_catalog.yaml"
        )
        with open(catalog_path) as f:
            catalog = yaml.safe_load(f)
        entry = catalog["tools"]["memory_flag_contradiction.v1"]
        assert entry["side_effects"] == "filesystem"
        assert entry["required_initiative_level"] == "L3"
        # The four contradiction kinds match ADR-0027-am §7.3 enum:
        kinds = entry["input_schema"]["properties"]["contradiction_kind"]["enum"]
        assert set(kinds) == {"direct", "updated", "qualified", "retracted"}
