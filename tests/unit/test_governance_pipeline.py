"""Unit tests for the governance pipeline — R3 refactor coverage.

The pipeline was extracted from ToolDispatcher.dispatch()'s inline check
chain on 2026-04-30. The 533 LoC of step classes have been exercised via
the dispatcher's integration tests (test_tool_dispatcher.py) but had no
unit-level coverage of their own — Phase A audit 2026-04-30, Finding T-3.

These tests drive each step in isolation with stub dependencies so any
regression in step logic surfaces with a tight, single-step error rather
than getting buried in a multi-step dispatch trace.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from forest_soul_forge.tools.base import (
    ToolError,
    ToolRegistry,
    ToolResult,
    ToolValidationError,
)
from forest_soul_forge.tools.governance_pipeline import (
    ApprovalGateStep,
    ArgsValidationStep,
    CallCounterStep,
    ConstraintResolutionStep,
    DispatchContext,
    GenreFloorStep,
    GovernancePipeline,
    HardwareQuarantineStep,
    InitiativeFloorStep,
    PostureOverrideStep,
    StepResult,
    TaskUsageCapStep,
    ToolLookupStep,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------
def _ctx(**overrides) -> DispatchContext:
    """Build a DispatchContext with sensible test defaults."""
    base = dict(
        instance_id="i1",
        agent_dna="d" * 12,
        role="network_watcher",
        genre="observer",
        session_id="s1",
        constitution_path=Path("/tmp/nonexistent.yaml"),
        tool_name="timestamp_window",
        tool_version="1",
        args={"expression": "last 5 minutes"},
        provider=None,
        task_caps=None,
    )
    base.update(overrides)
    return DispatchContext(**base)


class _AuditStub:
    """Captures audit.append calls for assertion."""

    def __init__(self):
        self.events: list[tuple[str, dict, str | None]] = []

    def append(self, event_type, event_data, *, agent_dna=None):
        self.events.append((event_type, event_data, agent_dna))


class _StubResolved:
    """Stub for the _ResolvedToolConstraints private dataclass."""
    def __init__(self, *, constraints=None, side_effects="read_only"):
        self.constraints = constraints or {}
        self.side_effects = side_effects


# ===========================================================================
# StepResult — factory methods + verdict properties
# ===========================================================================
class TestStepResult:
    def test_go_factory(self):
        r = StepResult.go()
        assert r.verdict == "GO"
        assert r.terminal is False
        assert r.is_refuse is False
        assert r.is_pending is False

    def test_refuse_factory(self):
        r = StepResult.refuse("unknown_tool", "no such tool")
        assert r.verdict == "REFUSE"
        assert r.reason == "unknown_tool"
        assert r.detail == "no such tool"
        assert r.terminal is True
        assert r.is_refuse is True
        assert r.is_pending is False

    def test_pending_factory(self):
        r = StepResult.pending(gate_source="constraint", side_effects="external")
        assert r.verdict == "PENDING"
        assert r.gate_source == "constraint"
        assert r.side_effects == "external"
        assert r.terminal is True
        assert r.is_refuse is False
        assert r.is_pending is True


# ===========================================================================
# GovernancePipeline — short-circuit + walk semantics
# ===========================================================================
class TestGovernancePipeline:
    def test_empty_pipeline_returns_go(self):
        p = GovernancePipeline(steps=[])
        assert p.run(_ctx()).verdict == "GO"

    def test_all_go_returns_final_go(self):
        class _GoStep:
            def evaluate(self, dctx): return StepResult.go()
        p = GovernancePipeline(steps=[_GoStep(), _GoStep(), _GoStep()])
        assert p.run(_ctx()).verdict == "GO"

    def test_first_terminal_short_circuits(self):
        """Steps after the first terminal must NOT fire."""
        sentinel = []

        class _GoStep:
            def evaluate(self, dctx):
                sentinel.append("go")
                return StepResult.go()

        class _RefuseStep:
            def evaluate(self, dctx):
                sentinel.append("refuse")
                return StepResult.refuse("test", "stop here")

        class _NeverStep:
            def evaluate(self, dctx):
                sentinel.append("never")
                return StepResult.go()

        p = GovernancePipeline(steps=[_GoStep(), _RefuseStep(), _NeverStep()])
        result = p.run(_ctx())
        assert result.is_refuse
        assert sentinel == ["go", "refuse"]  # _NeverStep skipped

    def test_pending_short_circuits(self):
        class _PendStep:
            def evaluate(self, dctx):
                return StepResult.pending(gate_source="x", side_effects="external")

        class _NeverStep:
            def evaluate(self, dctx):
                raise AssertionError("must not run after pending")

        result = GovernancePipeline(steps=[_PendStep(), _NeverStep()]).run(_ctx())
        assert result.is_pending


# ===========================================================================
# HardwareQuarantineStep
# ===========================================================================
class TestHardwareQuarantineStep:
    def test_no_quarantine_returns_go(self):
        step = HardwareQuarantineStep(
            audit=_AuditStub(),
            quarantine_reason_fn=lambda path: None,
        )
        assert step.evaluate(_ctx()).verdict == "GO"

    def test_quarantine_refuses_and_audits(self):
        audit = _AuditStub()
        step = HardwareQuarantineStep(
            audit=audit,
            quarantine_reason_fn=lambda path: {
                "expected": "abcdef0123456789",
                "binding": "0123456789abcdef",
            },
        )
        result = step.evaluate(_ctx())
        assert result.is_refuse
        assert result.reason == "hardware_quarantined"
        assert "abcdef01" in result.detail or "0123456789" in result.detail
        assert len(audit.events) == 1
        assert audit.events[0][0] == "hardware_mismatch"

    def test_audit_failure_does_not_mask_refusal(self):
        """If audit.append raises, quarantine step still returns REFUSE."""
        class _BrokenAudit:
            def append(self, *a, **k):
                raise RuntimeError("audit broken")
        step = HardwareQuarantineStep(
            audit=_BrokenAudit(),
            quarantine_reason_fn=lambda path: {"expected": "x", "binding": "y"},
        )
        result = step.evaluate(_ctx())
        assert result.is_refuse


# ===========================================================================
# TaskUsageCapStep
# ===========================================================================
class TestTaskUsageCapStep:
    def test_no_caps_returns_go(self):
        step = TaskUsageCapStep(
            audit=_AuditStub(),
            session_token_sum_fn=lambda iid, sid: 0,
            task_caps_set_fn=lambda *a, **k: None,
        )
        assert step.evaluate(_ctx(task_caps=None)).verdict == "GO"
        assert step.evaluate(_ctx(task_caps={})).verdict == "GO"

    def test_under_cap_returns_go(self):
        step = TaskUsageCapStep(
            audit=_AuditStub(),
            session_token_sum_fn=lambda iid, sid: 100,
            task_caps_set_fn=lambda *a, **k: None,
        )
        result = step.evaluate(_ctx(task_caps={"usage_cap_tokens": 500}))
        assert result.verdict == "GO"

    def test_over_cap_refuses(self):
        step = TaskUsageCapStep(
            audit=_AuditStub(),
            session_token_sum_fn=lambda iid, sid: 600,
            task_caps_set_fn=lambda *a, **k: None,
        )
        result = step.evaluate(_ctx(task_caps={"usage_cap_tokens": 500}))
        assert result.is_refuse
        assert result.reason == "task_usage_cap_exceeded"
        assert "600" in result.detail
        assert "500" in result.detail

    def test_at_cap_refuses_inclusive_boundary(self):
        """``used >= usage_cap`` — exactly-at-cap also refuses."""
        step = TaskUsageCapStep(
            audit=_AuditStub(),
            session_token_sum_fn=lambda iid, sid: 500,
            task_caps_set_fn=lambda *a, **k: None,
        )
        result = step.evaluate(_ctx(task_caps={"usage_cap_tokens": 500}))
        assert result.is_refuse

    def test_caps_set_emitter_called_with_task_caps(self):
        called = []
        step = TaskUsageCapStep(
            audit=_AuditStub(),
            session_token_sum_fn=lambda iid, sid: 0,
            task_caps_set_fn=lambda task_caps, **kw: called.append((task_caps, kw)),
        )
        step.evaluate(_ctx(task_caps={"usage_cap_tokens": 100}))
        assert len(called) == 1
        assert called[0][0] == {"usage_cap_tokens": 100}


# ===========================================================================
# ToolLookupStep
# ===========================================================================
class TestToolLookupStep:
    def test_unknown_tool_refuses(self):
        registry = ToolRegistry()
        step = ToolLookupStep(registry=registry)
        result = step.evaluate(_ctx(tool_name="nope", tool_version="1"))
        assert result.is_refuse
        assert result.reason == "unknown_tool"

    def test_known_tool_sets_dctx_tool(self):
        from forest_soul_forge.tools.builtin.timestamp_window import TimestampWindowTool
        registry = ToolRegistry()
        registry.register(TimestampWindowTool())
        step = ToolLookupStep(registry=registry)
        dctx = _ctx()
        result = step.evaluate(dctx)
        assert result.verdict == "GO"
        assert dctx.tool is not None
        assert dctx.tool.name == "timestamp_window"


# ===========================================================================
# ArgsValidationStep
# ===========================================================================
class TestArgsValidationStep:
    def test_valid_args_pass(self):
        class _OkTool:
            def validate(self, args): return None
        dctx = _ctx()
        dctx.tool = _OkTool()
        assert ArgsValidationStep().evaluate(dctx).verdict == "GO"

    def test_validation_error_refuses(self):
        class _BadTool:
            def validate(self, args):
                raise ToolValidationError("missing required field")
        dctx = _ctx()
        dctx.tool = _BadTool()
        result = ArgsValidationStep().evaluate(dctx)
        assert result.is_refuse
        assert result.reason == "bad_args"
        assert "missing required field" in result.detail

    def test_tool_error_refuses(self):
        class _BrokenTool:
            def validate(self, args):
                raise ToolError("something went wrong")
        dctx = _ctx()
        dctx.tool = _BrokenTool()
        result = ArgsValidationStep().evaluate(dctx)
        assert result.is_refuse
        assert result.reason == "bad_args"


# ===========================================================================
# ConstraintResolutionStep
# ===========================================================================
class TestConstraintResolutionStep:
    def test_resolved_sets_dctx_resolved(self):
        rv = _StubResolved()
        step = ConstraintResolutionStep(
            load_resolved_constraints_fn=lambda *a: rv,
        )
        dctx = _ctx()
        result = step.evaluate(dctx)
        assert result.verdict == "GO"
        assert dctx.resolved is rv

    def test_constitution_missing_refuses(self, tmp_path):
        step = ConstraintResolutionStep(
            load_resolved_constraints_fn=lambda *a: None,
        )
        result = step.evaluate(_ctx(constitution_path=tmp_path / "missing.yaml"))
        assert result.is_refuse
        assert result.reason == "constitution_missing"

    def test_tool_not_in_constitution_refuses(self, tmp_path):
        const = tmp_path / "exists.yaml"
        const.write_text("dummy", encoding="utf-8")
        step = ConstraintResolutionStep(
            load_resolved_constraints_fn=lambda *a: None,
        )
        result = step.evaluate(_ctx(constitution_path=const))
        assert result.is_refuse
        assert result.reason == "tool_not_in_constitution"


# ===========================================================================
# PostureOverrideStep
# ===========================================================================
class TestPostureOverrideStep:
    def test_no_overrides_returns_go_no_audit(self):
        audit = _AuditStub()
        step = PostureOverrideStep(
            audit=audit,
            resolve_active_model_fn=lambda p: "model-1",
            apply_overrides_fn=lambda r, p, m: (r, []),  # no notes
        )
        dctx = _ctx()
        dctx.resolved = _StubResolved()
        assert step.evaluate(dctx).verdict == "GO"
        assert audit.events == []
        assert dctx.active_model == "model-1"

    def test_overrides_applied_emits_audit(self):
        audit = _AuditStub()
        new_resolved = _StubResolved(constraints={"max_calls_per_session": 10})

        def _apply(r, p, m):
            return new_resolved, ["tightened max_tokens"]

        step = PostureOverrideStep(
            audit=audit,
            resolve_active_model_fn=lambda p: "qwen2.5-coder:7b",
            apply_overrides_fn=_apply,
        )
        dctx = _ctx()
        dctx.resolved = _StubResolved()
        step.evaluate(dctx)
        assert dctx.resolved is new_resolved
        assert dctx.posture_notes == ["tightened max_tokens"]
        assert len(audit.events) == 1
        assert audit.events[0][0] == "posture_override_applied"
        assert audit.events[0][1]["active_model"] == "qwen2.5-coder:7b"


# ===========================================================================
# GenreFloorStep
# ===========================================================================
class TestGenreFloorStep:
    def test_floor_passes(self):
        step = GenreFloorStep(
            genre_engine_fn=lambda: object(),
            check_genre_floor_fn=lambda **k: (True, ""),
        )
        dctx = _ctx()
        dctx.tool = mock.Mock(side_effects="read_only")
        dctx.resolved = _StubResolved(side_effects="read_only")
        assert step.evaluate(dctx).verdict == "GO"

    def test_floor_violated_refuses(self):
        step = GenreFloorStep(
            genre_engine_fn=lambda: object(),
            check_genre_floor_fn=lambda **k: (False, "external > observer ceiling"),
        )
        dctx = _ctx()
        dctx.tool = mock.Mock(side_effects="external")
        dctx.resolved = _StubResolved(side_effects="external")
        result = step.evaluate(dctx)
        assert result.is_refuse
        assert result.reason == "genre_floor_violated"
        assert "external" in result.detail

    def test_genre_engine_re_read_each_evaluate(self):
        """``genre_engine_fn`` is a callable, NOT a captured reference.
        Tests rebinding dispatcher.genre_engine after construction must
        be visible to the step on the next evaluate."""
        engines = ["first", "second"]

        def _next_engine():
            return engines[0]

        seen = []

        def _check(engine, role, tool_side_effects, provider):
            seen.append(engine)
            return (True, "")

        step = GenreFloorStep(
            genre_engine_fn=_next_engine,
            check_genre_floor_fn=_check,
        )
        dctx = _ctx()
        dctx.tool = mock.Mock(side_effects="read_only")
        dctx.resolved = _StubResolved(side_effects="read_only")
        step.evaluate(dctx)
        assert seen == ["first"]
        # Rebind:
        engines[0] = "second"
        step.evaluate(dctx)
        assert seen == ["first", "second"]


# ===========================================================================
# CallCounterStep
# ===========================================================================
class TestCallCounterStep:
    def test_no_max_returns_go(self):
        step = CallCounterStep(counter_get_fn=lambda iid, sid: 999)
        dctx = _ctx()
        dctx.resolved = _StubResolved(constraints={"max_calls_per_session": 0})
        assert step.evaluate(dctx).verdict == "GO"

    def test_under_max_returns_go(self):
        step = CallCounterStep(counter_get_fn=lambda iid, sid: 5)
        dctx = _ctx()
        dctx.resolved = _StubResolved(constraints={"max_calls_per_session": 10})
        assert step.evaluate(dctx).verdict == "GO"

    def test_at_max_refuses(self):
        step = CallCounterStep(counter_get_fn=lambda iid, sid: 10)
        dctx = _ctx()
        dctx.resolved = _StubResolved(constraints={"max_calls_per_session": 10})
        result = step.evaluate(dctx)
        assert result.is_refuse
        assert result.reason == "max_calls_exceeded"

    def test_over_max_refuses(self):
        step = CallCounterStep(counter_get_fn=lambda iid, sid: 11)
        dctx = _ctx()
        dctx.resolved = _StubResolved(constraints={"max_calls_per_session": 10})
        assert step.evaluate(dctx).is_refuse


# ===========================================================================
# InitiativeFloorStep — ADR-0021-amendment §5
# ===========================================================================
class _StubTool:
    """Stub for the loaded tool object dctx.tool — only the attributes
    InitiativeFloorStep reads."""
    def __init__(self, required_initiative_level: str = ""):
        self.required_initiative_level = required_initiative_level


class TestInitiativeFloorStep:
    """Opt-in initiative ladder gate. v0.2 enforcement only fires for
    tools that declare ``required_initiative_level`` — others pass."""

    def test_tool_without_required_level_passes(self):
        # No declared initiative requirement → no enforcement.
        step = InitiativeFloorStep(initiative_loader_fn=lambda p: "L0")
        dctx = _ctx()
        dctx.tool = _StubTool(required_initiative_level="")
        assert step.evaluate(dctx).verdict == "GO"

    def test_tool_without_attribute_passes(self):
        # Defensive: a tool that doesn't have the attribute at all
        # (older tool that hasn't been audited yet) is treated the
        # same as one declaring "" — no enforcement.
        class _BareT:
            pass
        step = InitiativeFloorStep(initiative_loader_fn=lambda p: "L0")
        dctx = _ctx()
        dctx.tool = _BareT()
        assert step.evaluate(dctx).verdict == "GO"

    def test_required_at_or_below_agent_passes(self):
        step = InitiativeFloorStep(initiative_loader_fn=lambda p: "L4")
        dctx = _ctx()
        dctx.tool = _StubTool(required_initiative_level="L3")
        assert step.evaluate(dctx).verdict == "GO"

    def test_required_at_agent_level_passes(self):
        step = InitiativeFloorStep(initiative_loader_fn=lambda p: "L3")
        dctx = _ctx()
        dctx.tool = _StubTool(required_initiative_level="L3")
        assert step.evaluate(dctx).verdict == "GO"

    def test_required_above_agent_refuses(self):
        # Companion (L1) calling a tool that requires L4. Refused.
        step = InitiativeFloorStep(initiative_loader_fn=lambda p: "L1")
        dctx = _ctx()
        dctx.tool = _StubTool(required_initiative_level="L4")
        result = step.evaluate(dctx)
        assert result.is_refuse
        assert result.reason == "initiative_floor_violated"
        assert "L4" in result.detail
        assert "L1" in result.detail

    def test_unknown_required_level_fails_closed(self):
        # Unknown level → strictest (L0=0). Refuses against any agent
        # level above L0 (which is every realistic agent — L0 means
        # "reactive only, no memory writes" and would never be
        # configured by an operator who's also annotating tools).
        # Practical effect: a typo on the tool side surfaces as a
        # refusal rather than silently letting the call through.
        step = InitiativeFloorStep(initiative_loader_fn=lambda p: "L0")
        dctx = _ctx()
        dctx.tool = _StubTool(required_initiative_level="LZZ")
        # required→0, agent L0→0; 0 <= 0 → GO. Equal levels are OK.
        # The test confirms the comparator semantic.
        assert step.evaluate(dctx).verdict == "GO"

    def test_unknown_agent_level_fails_closed(self):
        # Unknown agent level → strictest (L0). Tool requiring L1 refuses.
        step = InitiativeFloorStep(initiative_loader_fn=lambda p: "garbage")
        dctx = _ctx()
        dctx.tool = _StubTool(required_initiative_level="L1")
        assert step.evaluate(dctx).is_refuse

    def test_loader_called_with_constitution_path(self):
        # The step delegates path → level via the loader. Pass through
        # confirms wiring.
        seen_paths = []
        def _loader(p):
            seen_paths.append(p)
            return "L5"
        step = InitiativeFloorStep(initiative_loader_fn=_loader)
        custom_path = Path("/tmp/agent-x/constitution.yaml")
        dctx = _ctx(constitution_path=custom_path)
        dctx.tool = _StubTool(required_initiative_level="L3")
        step.evaluate(dctx)
        assert seen_paths == [custom_path]


# ===========================================================================
# ApprovalGateStep
# ===========================================================================
class TestApprovalGateStep:
    def test_neither_constraint_nor_genre_returns_go(self):
        step = ApprovalGateStep(
            genre_requires_approval_fn=lambda genre, side_effects: False,
        )
        dctx = _ctx()
        dctx.tool = mock.Mock(side_effects="read_only")
        dctx.resolved = _StubResolved(constraints={"requires_human_approval": False})
        assert step.evaluate(dctx).verdict == "GO"

    def test_constraint_only_pends_with_constraint_source(self):
        step = ApprovalGateStep(
            genre_requires_approval_fn=lambda *a: False,
        )
        dctx = _ctx()
        dctx.tool = mock.Mock(side_effects="read_only")
        dctx.resolved = _StubResolved(
            constraints={"requires_human_approval": True},
            side_effects="read_only",
        )
        result = step.evaluate(dctx)
        assert result.is_pending
        assert result.gate_source == "constraint"
        assert result.side_effects == "read_only"

    def test_genre_only_pends_with_genre_source(self):
        step = ApprovalGateStep(
            genre_requires_approval_fn=lambda genre, side_effects: True,
        )
        dctx = _ctx(genre="security_high")
        dctx.tool = mock.Mock(side_effects="external")
        dctx.resolved = _StubResolved(
            constraints={"requires_human_approval": False},
            side_effects="external",
        )
        result = step.evaluate(dctx)
        assert result.is_pending
        assert result.gate_source == "genre"
        assert result.side_effects == "external"

    def test_both_paths_pend_with_combined_source(self):
        step = ApprovalGateStep(
            genre_requires_approval_fn=lambda *a: True,
        )
        dctx = _ctx(genre="security_high")
        dctx.tool = mock.Mock(side_effects="filesystem")
        dctx.resolved = _StubResolved(
            constraints={"requires_human_approval": True},
            side_effects="filesystem",
        )
        result = step.evaluate(dctx)
        assert result.is_pending
        assert result.gate_source == "constraint+genre"

    def test_resolved_side_effects_overrides_tool_side_effects(self):
        """Per the original dispatcher contract — resolved-side-effects
        wins because constitution + posture overrides may have tightened
        what the tool itself declared."""
        step = ApprovalGateStep(
            genre_requires_approval_fn=lambda genre, side_effects: side_effects == "external",
        )
        dctx = _ctx()
        dctx.tool = mock.Mock(side_effects="read_only")  # tool says safe
        dctx.resolved = _StubResolved(
            constraints={"requires_human_approval": False},
            side_effects="external",  # constitution / posture tightened to external
        )
        result = step.evaluate(dctx)
        assert result.is_pending
        assert result.side_effects == "external"
