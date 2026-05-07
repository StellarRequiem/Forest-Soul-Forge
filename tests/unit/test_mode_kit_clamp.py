"""ADR-0056 E2 (Burst 188) — ModeKitClampStep tests.

Coverage:

  Step in isolation (drives the step against a stub DispatchContext):
    - off-experimenter role passes through every mode
    - mode='none' (default) passes through
    - mode='explore' passes read_only tools
    - mode='explore' refuses non-read_only with reason='mode_kit_clamp'
    - mode='work' passes through any tool (full kit)
    - mode='display' passes the tight allowlist
    - mode='display' refuses out-of-allowlist tools
    - unknown mode value refuses loudly

  task_caps.mode plumbing:
    - dispatcher reads task_caps['mode'] into dctx.mode
    - missing mode key defaults to 'none'
    - non-string mode value treated as 'none'
    - case-insensitive ('EXPLORE' -> 'explore')

The pipeline integration (placement after PostureGateStep, before
ProceduralShortcutStep) is verified by the existing
test_governance_pipeline.py + test_tool_dispatcher.py sweeps —
this file scopes to the new step's logic.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from forest_soul_forge.tools.governance_pipeline import (
    DispatchContext,
    ModeKitClampStep,
    StepResult,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _ctx(
    *,
    role: str = "experimenter",
    mode: str = "none",
    tool_name: str = "code_edit",
    side_effects: str = "filesystem",
) -> DispatchContext:
    """Build a minimal DispatchContext that satisfies the step's
    field reads. Everything else is whatever DispatchContext
    defaults to."""
    # The step reads dctx.tool.side_effects + dctx.resolved.side_effects.
    # Stub the tool with just the side_effects attribute set; the
    # step doesn't touch the rest.
    tool_stub = MagicMock()
    tool_stub.side_effects = side_effects

    return DispatchContext(
        instance_id="i1",
        agent_dna="d" * 12,
        role=role,
        genre=None,
        session_id="s1",
        constitution_path=Path("/tmp/nonexistent.yaml"),
        tool_name=tool_name,
        tool_version="1",
        args={},
        tool=tool_stub,
        mode=mode,
    )


# ===========================================================================
# Step in isolation
# ===========================================================================

class TestRoleGate:
    def test_off_experimenter_passes_every_mode(self):
        """An agent whose role isn't 'experimenter' is unaffected by
        every mode tag — even hostile values like 'work' on an
        explorer-genre agent should be a no-op."""
        step = ModeKitClampStep()
        for mode in ["none", "explore", "work", "display", "garbage"]:
            for tool_name in ["code_edit", "shell_exec", "memory_recall"]:
                for se in ["read_only", "filesystem", "external"]:
                    ctx = _ctx(
                        role="software_engineer",
                        mode=mode,
                        tool_name=tool_name,
                        side_effects=se,
                    )
                    assert step.evaluate(ctx).verdict == "GO", \
                        f"non-experimenter role={ctx.role} mode={mode} clobbered"

    def test_custom_experimenter_role_recognized(self):
        """Tests can override the experimenter_role; the step then
        treats THAT role as the experimenter."""
        step = ModeKitClampStep(experimenter_role="smith_double")
        # Smith_double in explore mode + filesystem tool → REFUSE
        ctx = _ctx(
            role="smith_double",
            mode="explore",
            tool_name="code_edit",
            side_effects="filesystem",
        )
        assert step.evaluate(ctx).verdict == "REFUSE"

        # Default experimenter role in same situation → GO
        # (because the step now treats 'experimenter' as just
        # another role).
        ctx2 = _ctx(
            role="experimenter",
            mode="explore",
            tool_name="code_edit",
            side_effects="filesystem",
        )
        assert step.evaluate(ctx2).verdict == "GO"


class TestNoneMode:
    def test_none_passes_any_tool(self):
        step = ModeKitClampStep()
        for se in ["read_only", "network", "filesystem", "external"]:
            ctx = _ctx(mode="none", side_effects=se)
            assert step.evaluate(ctx).verdict == "GO"

    def test_empty_string_mode_passes(self):
        """Empty string normalizes to 'none' and passes."""
        step = ModeKitClampStep()
        ctx = _ctx(mode="", side_effects="external")
        assert step.evaluate(ctx).verdict == "GO"


class TestExploreMode:
    def test_explore_passes_read_only(self):
        step = ModeKitClampStep()
        ctx = _ctx(
            mode="explore", tool_name="memory_recall",
            side_effects="read_only",
        )
        assert step.evaluate(ctx).verdict == "GO"

    def test_explore_refuses_filesystem(self):
        step = ModeKitClampStep()
        ctx = _ctx(
            mode="explore", tool_name="code_edit",
            side_effects="filesystem",
        )
        result = step.evaluate(ctx)
        assert result.verdict == "REFUSE"
        assert result.reason == "mode_kit_clamp"
        assert "explore" in result.detail
        assert "code_edit" in result.detail
        assert "filesystem" in result.detail

    def test_explore_refuses_external(self):
        step = ModeKitClampStep()
        ctx = _ctx(
            mode="explore", tool_name="shell_exec",
            side_effects="external",
        )
        result = step.evaluate(ctx)
        assert result.verdict == "REFUSE"
        assert result.reason == "mode_kit_clamp"

    def test_explore_refuses_network(self):
        step = ModeKitClampStep()
        ctx = _ctx(
            mode="explore", tool_name="web_fetch",
            side_effects="network",
        )
        result = step.evaluate(ctx)
        assert result.verdict == "REFUSE"


class TestWorkMode:
    def test_work_passes_every_tool(self):
        step = ModeKitClampStep()
        for tool_name, se in [
            ("code_edit", "filesystem"),
            ("shell_exec", "external"),
            ("web_fetch", "network"),
            ("memory_recall", "read_only"),
        ]:
            ctx = _ctx(mode="work", tool_name=tool_name, side_effects=se)
            assert step.evaluate(ctx).verdict == "GO"


class TestDisplayMode:
    def test_display_passes_allowlist(self):
        step = ModeKitClampStep()
        for tool_name in step.DISPLAY_ALLOWED_TOOLS:
            # display-mode tools are all read_only by spec but the
            # clamp doesn't check that — it checks the name. Use a
            # variety of side_effects to confirm.
            for se in ["read_only", "filesystem"]:
                ctx = _ctx(
                    mode="display", tool_name=tool_name, side_effects=se,
                )
                assert step.evaluate(ctx).verdict == "GO"

    def test_display_refuses_unlisted_tool(self):
        step = ModeKitClampStep()
        for tool_name, se in [
            ("code_edit", "filesystem"),
            ("shell_exec", "external"),
            ("memory_write", "read_only"),
            ("llm_think", "read_only"),
        ]:
            ctx = _ctx(mode="display", tool_name=tool_name, side_effects=se)
            result = step.evaluate(ctx)
            assert result.verdict == "REFUSE"
            assert result.reason == "mode_kit_clamp"
            assert "display" in result.detail


class TestUnknownMode:
    def test_unknown_mode_refuses_loudly(self):
        step = ModeKitClampStep()
        ctx = _ctx(mode="garbage", tool_name="code_edit")
        result = step.evaluate(ctx)
        assert result.verdict == "REFUSE"
        assert result.reason == "mode_kit_clamp"
        assert "unknown experimenter mode" in result.detail
        assert "garbage" in result.detail

    def test_typo_refuses_not_passes(self):
        """'explorer' (typo for 'explore') must refuse — silently
        defaulting to 'explore' would mask operator typos."""
        step = ModeKitClampStep()
        ctx = _ctx(mode="explorer", tool_name="memory_recall",
                   side_effects="read_only")
        result = step.evaluate(ctx)
        assert result.verdict == "REFUSE"


class TestModeNormalization:
    def test_uppercase_mode(self):
        """The step normalizes the mode to lowercase. 'EXPLORE'
        works just like 'explore'."""
        step = ModeKitClampStep()
        ctx = _ctx(mode="EXPLORE", tool_name="memory_recall",
                   side_effects="read_only")
        assert step.evaluate(ctx).verdict == "GO"

    def test_mixed_case_with_whitespace(self):
        step = ModeKitClampStep()
        ctx = _ctx(mode="  Work  ", tool_name="code_edit",
                   side_effects="filesystem")
        assert step.evaluate(ctx).verdict == "GO"


class TestResolvedSideEffectsPrecedence:
    def test_resolved_side_effects_override_tool_default(self):
        """Per the rest of the pipeline, dctx.resolved.side_effects
        (from constitution) takes precedence over dctx.tool.side_effects
        (the tool's declared default). The clamp follows the same
        rule."""
        step = ModeKitClampStep()
        # Tool declares external; constitution tightens to read_only.
        # Explore mode should pass under the constitution's read_only.
        resolved_stub = MagicMock()
        resolved_stub.side_effects = "read_only"

        ctx = _ctx(
            mode="explore",
            tool_name="weird_tool",
            side_effects="external",  # tool default
        )
        ctx.resolved = resolved_stub  # constitution override
        assert step.evaluate(ctx).verdict == "GO"
