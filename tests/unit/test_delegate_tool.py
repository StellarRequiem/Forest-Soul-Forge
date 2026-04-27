"""Unit tests for delegate.v1 + the delegator factory — ADR-0033 A3.

Covers:
- TestDelegatorRefusals    — every gate raises DelegateError before
                              skill runs (so refusals don't pollute the
                              audit chain)
- TestDelegatorAuditEmission — successful delegations emit one
                              agent_delegated event with the right shape
                              (override flag included)
- TestDelegateToolValidate — schema + arg refusals
- TestDelegateToolExecute  — wires through ctx.delegate, refuses when
                              not wired, maps DelegateError to
                              ToolValidationError
"""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest

from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.registry import Registry
from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.delegate import DelegateTool
from forest_soul_forge.tools.delegator import DelegateError, build_delegator_factory


def _run(coro):
    return asyncio.run(coro)


def _seed(conn):
    """Three-agent topology: A → B (parent → child); C unrelated."""
    for aid in ("A", "B", "C"):
        conn.execute(
            "INSERT INTO agents (instance_id, dna, dna_full, role, agent_name, "
            "soul_path, constitution_path, constitution_hash, created_at, "
            "status, sibling_index) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (aid, aid * 12, aid * 64, "observer", aid,
             f"souls/{aid}.md", f"constitutions/{aid}.yaml", "0" * 64,
             "2026-04-27T00:00:00Z", "alive", 1),
        )
    for row in [("A","A",0), ("B","B",0), ("B","A",1), ("C","C",0)]:
        conn.execute(
            "INSERT INTO agent_ancestry(instance_id, ancestor_id, depth) VALUES (?, ?, ?)",
            row,
        )
    conn.commit()


class _StubDispatcher:
    """Records dispatch calls; doesn't execute. Sufficient for the
    refusal-path tests since they fire BEFORE the skill runs."""
    async def dispatch(self, **kwargs):  # pragma: no cover — only for type
        return None


@pytest.fixture
def env(tmp_path: Path):
    reg = Registry.bootstrap(tmp_path / "reg.sqlite")
    audit = AuditChain(tmp_path / "audit.jsonl")
    write_lock = threading.Lock()
    _seed(reg._conn)  # noqa: SLF001
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    factory = build_delegator_factory(
        registry=reg, audit_chain=audit, dispatcher=_StubDispatcher(),
        skill_install_dir=skills_dir, write_lock=write_lock,
    )
    yield {
        "registry":   reg,
        "audit":      audit,
        "audit_path": tmp_path / "audit.jsonl",
        "factory":    factory,
        "skills_dir": skills_dir,
    }
    reg.close()


def _audit_events(audit_path, event_type=None):
    events = [
        json.loads(line) for line in audit_path.read_text().splitlines()
        if line.strip()
    ]
    if event_type:
        events = [e for e in events if e.get("event_type") == event_type]
    return events


# ===========================================================================
# Delegator refusals (no audit emission on refusal)
# ===========================================================================
class TestDelegatorRefusals:
    def test_self_target_refused(self, env):
        delegate = env["factory"]("A", "A" * 12)
        with pytest.raises(DelegateError, match="differ"):
            _run(delegate(
                target_instance_id="A", skill_name="ping",
                skill_version="1", inputs={}, reason="self",
            ))

    def test_missing_target_refused(self, env):
        delegate = env["factory"]("A", "A" * 12)
        with pytest.raises(DelegateError, match="not found"):
            _run(delegate(
                target_instance_id="ghost", skill_name="ping",
                skill_version="1", inputs={}, reason="ghost",
            ))

    def test_out_of_lineage_refused(self, env):
        # A's chain = {A, B}; C is unrelated.
        delegate = env["factory"]("A", "A" * 12)
        with pytest.raises(DelegateError, match="lineage"):
            _run(delegate(
                target_instance_id="C", skill_name="ping",
                skill_version="1", inputs={}, reason="cross-tier",
            ))

    def test_missing_skill_refused(self, env):
        delegate = env["factory"]("A", "A" * 12)
        with pytest.raises(DelegateError, match="not installed"):
            _run(delegate(
                target_instance_id="B", skill_name="ghost-skill",
                skill_version="1", inputs={}, reason="missing",
            ))

    def test_refusals_dont_emit_audit_events(self, env):
        # All four refusals above should leave the audit chain empty
        # of agent_delegated events. The chain has the genesis entry
        # but nothing beyond it.
        delegate = env["factory"]("A", "A" * 12)
        for target, skill in [
            ("A", "ping"),         # self
            ("ghost", "ping"),     # missing target
            ("C", "ping"),         # out of lineage
            ("B", "ghost-skill"),  # missing skill
        ]:
            try:
                _run(delegate(
                    target_instance_id=target, skill_name=skill,
                    skill_version="1", inputs={}, reason="refusal-probe",
                ))
            except DelegateError:
                pass
        events = _audit_events(env["audit_path"], "agent_delegated")
        assert events == [], (
            f"refusals leaked into audit chain: {events}"
        )


# ===========================================================================
# DelegateTool validate
# ===========================================================================
class TestDelegateToolValidate:
    def test_missing_required_fields(self):
        tool = DelegateTool()
        with pytest.raises(ToolValidationError, match="target_instance_id"):
            tool.validate({})
        with pytest.raises(ToolValidationError, match="skill_name"):
            tool.validate({"target_instance_id": "B"})
        with pytest.raises(ToolValidationError, match="skill_version"):
            tool.validate({"target_instance_id": "B", "skill_name": "p"})
        with pytest.raises(ToolValidationError, match="reason"):
            tool.validate({
                "target_instance_id": "B", "skill_name": "p", "skill_version": "1",
            })

    def test_empty_string_refused(self):
        with pytest.raises(ToolValidationError, match="reason"):
            DelegateTool().validate({
                "target_instance_id": "B", "skill_name": "p",
                "skill_version": "1", "reason": "",
            })

    def test_oversized_reason_refused(self):
        with pytest.raises(ToolValidationError, match="exceeds"):
            DelegateTool().validate({
                "target_instance_id": "B", "skill_name": "p",
                "skill_version": "1", "reason": "x" * 1000,
            })

    def test_inputs_must_be_dict(self):
        with pytest.raises(ToolValidationError, match="inputs"):
            DelegateTool().validate({
                "target_instance_id": "B", "skill_name": "p",
                "skill_version": "1", "reason": "r",
                "inputs": "not-a-dict",
            })

    def test_allow_out_of_lineage_must_be_bool(self):
        with pytest.raises(ToolValidationError, match="allow_out_of_lineage"):
            DelegateTool().validate({
                "target_instance_id": "B", "skill_name": "p",
                "skill_version": "1", "reason": "r",
                "allow_out_of_lineage": "yes",
            })

    def test_session_id_must_be_string(self):
        with pytest.raises(ToolValidationError, match="session_id"):
            DelegateTool().validate({
                "target_instance_id": "B", "skill_name": "p",
                "skill_version": "1", "reason": "r",
                "session_id": 42,
            })


# ===========================================================================
# DelegateTool execute
# ===========================================================================
class TestDelegateToolExecute:
    def test_refuses_when_no_delegator_wired(self):
        ctx = ToolContext(
            instance_id="A", agent_dna="A" * 12, role="observer",
            genre=None, session_id="s", delegate=None,
        )
        with pytest.raises(ToolValidationError, match="no delegator wired"):
            _run(DelegateTool().execute({
                "target_instance_id": "B", "skill_name": "p",
                "skill_version": "1", "reason": "test",
            }, ctx))

    def test_propagates_delegate_error_as_validation_error(self, env):
        # Bind ctx.delegate to a real delegator and trigger a lineage
        # refusal — the tool should map DelegateError → ToolValidationError.
        ctx = ToolContext(
            instance_id="A", agent_dna="A" * 12, role="observer",
            genre=None, session_id="s",
            delegate=env["factory"]("A", "A" * 12),
        )
        with pytest.raises(ToolValidationError, match="delegate refused"):
            _run(DelegateTool().execute({
                "target_instance_id": "C",   # out of A's lineage
                "skill_name": "ping",
                "skill_version": "1",
                "reason": "bypass-attempt",
            }, ctx))


# ===========================================================================
# Audit emission ordering (the override path)
# ===========================================================================
class TestDelegatorAuditEmission:
    def test_override_flag_recorded(self, env, tmp_path):
        # Plant a minimal valid manifest so the delegator gets past
        # the manifest-load gate. We deliberately use a manifest with
        # one step that references a tool the stub dispatcher can
        # accept silently — the smoke test just verifies that the
        # audit event lands BEFORE the skill runs, regardless of
        # whether the skill itself succeeds.
        skill_dir = env["skills_dir"] / "noop.v1"
        skill_dir.mkdir()
        # Use a minimal step that calls timestamp_window.v1 (a real
        # built-in). The stub dispatcher returns None so the runtime
        # will mark it failed — that's fine; we're testing audit
        # emission ordering, not skill success.
        manifest = (
            "schema_version: 1\n"
            "name: noop\n"
            "version: '1'\n"
            "description: 'minimal manifest for audit emission test'\n"
            "inputs: {type: object, properties: {}, required: []}\n"
            "requires: [timestamp_window.v1]\n"
            "steps:\n"
            "  - id: now\n"
            "    tool: timestamp_window.v1\n"
            "    with: {}\n"
            "output:\n"
            "  ts: '${now}'\n"
        )
        (skill_dir / "skill.yaml").write_text(manifest)

        delegate = env["factory"]("A", "A" * 12)
        # allow_out_of_lineage=True bypasses the lineage gate; audit
        # should record the override flag.
        try:
            _run(delegate(
                target_instance_id="C",
                skill_name="noop", skill_version="1",
                inputs={}, reason="override probe",
                allow_out_of_lineage=True,
            ))
        except Exception:
            # Skill execution may fail (stub dispatcher) — we only
            # care that the audit event landed BEFORE the failure.
            pass

        events = _audit_events(env["audit_path"], "agent_delegated")
        assert len(events) == 1, f"expected 1 agent_delegated, got {len(events)}"
        body = events[0]["event_data"]
        assert body["caller_instance"] == "A"
        assert body["target_instance"] == "C"
        assert body["skill_name"] == "noop"
        assert body["skill_version"] == "1"
        assert body["reason"] == "override probe"
        assert body["allow_out_of_lineage"] is True

    def test_audit_event_is_registered(self):
        # ADR-0033 §"Audit + memory integration": agent_delegated must
        # be in KNOWN_EVENT_TYPES so the chain verifier doesn't
        # flag it as unknown.
        from forest_soul_forge.core.audit_chain import KNOWN_EVENT_TYPES
        assert "agent_delegated" in KNOWN_EVENT_TYPES
