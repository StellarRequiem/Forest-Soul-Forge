"""Unit tests for the Skill Forge runtime — ADR-0031 T2.

Covers:
  TestHappyPath           — single-step + multi-step DAG, output
                            assembly, audit-event ordering.
  TestPredicateGating     — when / unless control flow, skip events.
  TestForEach             — iteration + ``each`` binding scope, accum
                            shape, items-not-list rejection.
  TestStepFailure         — tool refused / failed / pending each
                            terminate the skill cleanly with the right
                            failure_reason + skill_completed entry.
  TestExpressionFailure   — predicate / arg eval errors caught.

Tests inject a fake ``dispatch_tool`` so the runtime is exercised
without spinning up the full ToolDispatcher.
"""
from __future__ import annotations

import asyncio
import textwrap
from dataclasses import dataclass

import pytest

from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.forge.skill_manifest import parse_manifest
from forest_soul_forge.forge.skill_runtime import (
    EVENT_SKILL_COMPLETED,
    EVENT_SKILL_INVOKED,
    EVENT_SKILL_STEP_COMPLETED,
    EVENT_SKILL_STEP_FAILED,
    EVENT_SKILL_STEP_SKIPPED,
    EVENT_SKILL_STEP_STARTED,
    SkillFailed,
    SkillRuntime,
    SkillSucceeded,
)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Fakes mirroring the dispatcher's outcome dataclasses by class name
# ---------------------------------------------------------------------------
@dataclass
class _DispatchSucceeded:
    tool_key: str
    audit_seq: int
    result: object


@dataclass
class _DispatchFailed:
    tool_key: str
    audit_seq: int
    exception_type: str


@dataclass
class _DispatchRefused:
    tool_key: str
    audit_seq: int
    reason: str
    detail: str


@dataclass
class _DispatchPendingApproval:
    tool_key: str
    audit_seq: int
    ticket_id: str


@dataclass
class _Result:
    output: dict


# Nudge the runtime's class-name match: it inspects type(outcome).__name__,
# so we need our fakes to expose those names. The simplest path:
_DispatchSucceeded.__name__ = "DispatchSucceeded"
_DispatchFailed.__name__ = "DispatchFailed"
_DispatchRefused.__name__ = "DispatchRefused"
_DispatchPendingApproval.__name__ = "DispatchPendingApproval"


def _make_dispatch_callable(behaviors):
    """Build a dispatch_tool callable that returns canned outcomes per
    tool name. ``behaviors`` is a dict tool_name → callable(args) →
    outcome, or tool_name → outcome (treated as a constant)."""
    seq = [100]  # mutable counter for synthetic audit_seq

    async def dispatch_tool(*, tool_name, tool_version, args, **rest):
        seq[0] += 1
        spec = behaviors.get(tool_name)
        if callable(spec):
            return spec(args, audit_seq=seq[0])
        if spec is None:
            # Default: succeed with empty output.
            return _DispatchSucceeded(
                tool_key=f"{tool_name}.v{tool_version}",
                audit_seq=seq[0],
                result=_Result(output={}),
            )
        return spec

    return dispatch_tool


def _make_runtime(tmp_path, behaviors=None):
    chain = AuditChain(tmp_path / "audit.jsonl")
    return SkillRuntime(
        audit=chain,
        dispatch_tool=_make_dispatch_callable(behaviors or {}),
    ), chain


def _parse(yaml_text):
    return parse_manifest(textwrap.dedent(yaml_text).strip())


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
class TestHappyPath:
    _SKILL = """
    schema_version: 1
    name: simple
    description: One step.
    requires: [echo.v1]
    inputs:
      type: object
      required: [name]
      properties: {name: {type: string}}
    steps:
      - id: hello
        tool: echo.v1
        args:
          msg: ${inputs.name}
    output:
      greeting: ${hello.message}
    """

    def test_single_step_succeeds(self, tmp_path):
        skill = _parse(self._SKILL)
        runtime, chain = _make_runtime(tmp_path, behaviors={
            "echo": lambda args, audit_seq: _DispatchSucceeded(
                tool_key="echo.v1", audit_seq=audit_seq,
                result=_Result(output={"message": f"hi {args['msg']}"}),
            ),
        })
        result = _run(runtime.run(
            skill=skill,
            instance_id="i1", agent_dna="d" * 12,
            role="echo_role", genre=None, session_id="s1",
            inputs={"name": "alex"},
        ))
        assert isinstance(result, SkillSucceeded)
        assert result.output == {"greeting": "hi alex"}
        assert result.steps_executed == 1
        assert result.steps_skipped == 0

    def test_audit_ordering(self, tmp_path):
        skill = _parse(self._SKILL)
        runtime, chain = _make_runtime(tmp_path, behaviors={
            "echo": lambda args, audit_seq: _DispatchSucceeded(
                tool_key="echo.v1", audit_seq=audit_seq,
                result=_Result(output={"message": "ok"}),
            ),
        })
        _run(runtime.run(
            skill=skill, instance_id="i1", agent_dna="d" * 12,
            role="r", genre=None, session_id="s1",
            inputs={"name": "alex"},
        ))
        types = [e.event_type for e in chain.read_all()]
        # genesis + skill_invoked + step_started + step_completed +
        # skill_completed
        assert types[-4:] == [
            EVENT_SKILL_INVOKED,
            EVENT_SKILL_STEP_STARTED,
            EVENT_SKILL_STEP_COMPLETED,
            EVENT_SKILL_COMPLETED,
        ]

    def test_multi_step_with_data_flow(self, tmp_path):
        skill = _parse("""
        schema_version: 1
        name: chain
        description: Two steps.
        requires: [a.v1, b.v1]
        inputs: {type: object}
        steps:
          - id: first
            tool: a.v1
            args: {}
          - id: second
            tool: b.v1
            args:
              from: ${first.value}
        output:
          end: ${second.result}
        """)
        runtime, _ = _make_runtime(tmp_path, behaviors={
            "a": lambda args, audit_seq: _DispatchSucceeded(
                tool_key="a.v1", audit_seq=audit_seq,
                result=_Result(output={"value": 42}),
            ),
            "b": lambda args, audit_seq: _DispatchSucceeded(
                tool_key="b.v1", audit_seq=audit_seq,
                result=_Result(output={"result": args["from"] + 1}),
            ),
        })
        result = _run(runtime.run(
            skill=skill, instance_id="i1", agent_dna="d" * 12,
            role="r", genre=None, session_id="s1",
            inputs={},
        ))
        assert isinstance(result, SkillSucceeded)
        assert result.output == {"end": 43}


# ---------------------------------------------------------------------------
# Predicate gating
# ---------------------------------------------------------------------------
class TestPredicateGating:
    def test_when_false_skips_step(self, tmp_path):
        skill = _parse("""
        schema_version: 1
        name: cond
        description: gate
        requires: [a.v1]
        inputs:
          type: object
          required: [run_it]
          properties: {run_it: {type: boolean}}
        steps:
          - id: maybe
            tool: a.v1
            when: ${inputs.run_it}
            args: {}
        output:
          ran: ${default(maybe.value, 'skipped')}
        """)
        runtime, chain = _make_runtime(tmp_path)
        # Skipped when run_it=False.
        result = _run(runtime.run(
            skill=skill, instance_id="i1", agent_dna="d" * 12,
            role="r", genre=None, session_id="s1",
            inputs={"run_it": False},
        ))
        assert isinstance(result, SkillSucceeded)
        assert result.steps_executed == 0
        assert result.steps_skipped == 1
        types = [e.event_type for e in chain.read_all()]
        assert EVENT_SKILL_STEP_SKIPPED in types

    def test_unless_true_skips_step(self, tmp_path):
        skill = _parse("""
        schema_version: 1
        name: cond
        description: gate
        requires: [a.v1]
        inputs:
          type: object
          required: [skip_it]
          properties: {skip_it: {type: boolean}}
        steps:
          - id: maybe
            tool: a.v1
            unless: ${inputs.skip_it}
            args: {}
        output: {}
        """)
        runtime, _ = _make_runtime(tmp_path)
        result = _run(runtime.run(
            skill=skill, instance_id="i1", agent_dna="d" * 12,
            role="r", genre=None, session_id="s1",
            inputs={"skip_it": True},
        ))
        assert result.steps_executed == 0
        assert result.steps_skipped == 1


# ---------------------------------------------------------------------------
# for_each
# ---------------------------------------------------------------------------
class TestForEach:
    def test_for_each_iterates_inner_steps(self, tmp_path):
        skill = _parse("""
        schema_version: 1
        name: loop
        description: per-item
        requires: [list.v1, lookup.v1]
        inputs: {type: object}
        steps:
          - id: items
            tool: list.v1
            args: {}
          - id: per
            for_each: ${items.values}
            steps:
              - id: lookup
                tool: lookup.v1
                args:
                  v: ${each}
        output:
          all: ${per.lookup}
        """)
        runtime, _ = _make_runtime(tmp_path, behaviors={
            "list": lambda args, audit_seq: _DispatchSucceeded(
                tool_key="list.v1", audit_seq=audit_seq,
                result=_Result(output={"values": [1, 2, 3]}),
            ),
            "lookup": lambda args, audit_seq: _DispatchSucceeded(
                tool_key="lookup.v1", audit_seq=audit_seq,
                result=_Result(output={"doubled": args["v"] * 2}),
            ),
        })
        result = _run(runtime.run(
            skill=skill, instance_id="i1", agent_dna="d" * 12,
            role="r", genre=None, session_id="s1",
            inputs={},
        ))
        assert isinstance(result, SkillSucceeded)
        assert result.output["all"] == [
            {"doubled": 2}, {"doubled": 4}, {"doubled": 6},
        ]
        # 1 outer step + 3 inner iterations.
        assert result.steps_executed == 4

    def test_for_each_each_only_in_inner_scope(self, tmp_path):
        # Reference to ${each} outside for_each fails MANIFEST-time
        # (parse_manifest catches it). This test confirms the runtime
        # doesn't accidentally allow it via lookahead.
        from forest_soul_forge.forge.skill_manifest import ManifestError
        with pytest.raises(ManifestError, match="each"):
            _parse("""
            schema_version: 1
            name: leak
            description: x
            requires: [t.v1]
            steps:
              - id: a
                tool: t.v1
                args:
                  v: ${each}
            """)

    def test_for_each_non_list_items_fails(self, tmp_path):
        skill = _parse("""
        schema_version: 1
        name: bad
        description: items wrong type
        requires: [list.v1, inner.v1]
        inputs: {type: object}
        steps:
          - id: items
            tool: list.v1
            args: {}
          - id: per
            for_each: ${items.value}
            steps:
              - id: inner
                tool: inner.v1
                args: {}
        output: {}
        """)
        runtime, _ = _make_runtime(tmp_path, behaviors={
            "list": lambda args, audit_seq: _DispatchSucceeded(
                tool_key="list.v1", audit_seq=audit_seq,
                # Returns a string instead of a list — runtime must reject.
                result=_Result(output={"value": "not-a-list"}),
            ),
        })
        result = _run(runtime.run(
            skill=skill, instance_id="i1", agent_dna="d" * 12,
            role="r", genre=None, session_id="s1",
            inputs={},
        ))
        assert isinstance(result, SkillFailed)
        assert result.failure_reason == "expression_error"
        assert "list-like" in result.detail


# ---------------------------------------------------------------------------
# Step failures (dispatcher returns non-success)
# ---------------------------------------------------------------------------
class TestStepFailure:
    _SKILL = """
    schema_version: 1
    name: one
    description: single step
    requires: [danger.v1]
    inputs: {type: object}
    steps:
      - id: only
        tool: danger.v1
        args: {}
    output: {}
    """

    def test_tool_failed_propagates(self, tmp_path):
        skill = _parse(self._SKILL)
        runtime, chain = _make_runtime(tmp_path, behaviors={
            "danger": lambda args, audit_seq: _DispatchFailed(
                tool_key="danger.v1", audit_seq=audit_seq,
                exception_type="RuntimeError",
            ),
        })
        result = _run(runtime.run(
            skill=skill, instance_id="i1", agent_dna="d" * 12,
            role="r", genre=None, session_id="s1",
            inputs={},
        ))
        assert isinstance(result, SkillFailed)
        assert result.failure_reason == "tool_failed"
        assert result.failed_step_id == "only"
        types = [e.event_type for e in chain.read_all()]
        assert EVENT_SKILL_STEP_FAILED in types
        assert EVENT_SKILL_COMPLETED in types

    def test_tool_refused_propagates(self, tmp_path):
        skill = _parse(self._SKILL)
        runtime, _ = _make_runtime(tmp_path, behaviors={
            "danger": lambda args, audit_seq: _DispatchRefused(
                tool_key="danger.v1", audit_seq=audit_seq,
                reason="genre_floor_violated",
                detail="side_effects exceeds tier",
            ),
        })
        result = _run(runtime.run(
            skill=skill, instance_id="i1", agent_dna="d" * 12,
            role="r", genre=None, session_id="s1",
            inputs={},
        ))
        assert isinstance(result, SkillFailed)
        assert result.failure_reason == "tool_refused"
        assert "genre_floor_violated" in result.detail

    def test_tool_pending_approval_pauses_skill(self, tmp_path):
        skill = _parse(self._SKILL)
        runtime, _ = _make_runtime(tmp_path, behaviors={
            "danger": lambda args, audit_seq: _DispatchPendingApproval(
                tool_key="danger.v1", audit_seq=audit_seq,
                ticket_id="pending-x",
            ),
        })
        result = _run(runtime.run(
            skill=skill, instance_id="i1", agent_dna="d" * 12,
            role="r", genre=None, session_id="s1",
            inputs={},
        ))
        assert isinstance(result, SkillFailed)
        assert result.failure_reason == "tool_pending_approval"
        assert "pending-x" in result.detail


# ---------------------------------------------------------------------------
# Expression failures
# ---------------------------------------------------------------------------
class TestExpressionFailure:
    def test_arg_resolution_error_kills_step(self, tmp_path):
        # No matching input — arg eval fails when manifest tries
        # to resolve ${inputs.missing}.
        skill = _parse("""
        schema_version: 1
        name: bad
        description: x
        requires: [a.v1]
        inputs:
          type: object
          properties: {present: {type: string}}
        steps:
          - id: a
            tool: a.v1
            args:
              v: ${inputs.missing}
        output: {}
        """)
        runtime, _ = _make_runtime(tmp_path)
        result = _run(runtime.run(
            skill=skill, instance_id="i1", agent_dna="d" * 12,
            role="r", genre=None, session_id="s1",
            inputs={"present": "x"},
        ))
        assert isinstance(result, SkillFailed)
        assert result.failure_reason == "expression_error"
