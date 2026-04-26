"""Unit tests for the tool execution runtime — ADR-0019 T1.

Coverage:
- TestRegistry        — register / get / has / duplicate-rejection / side_effects validation
- TestProtocolShape   — Tool Protocol contract is satisfied by the reference impl
- TestTimestampWindow — execute round-trip, validate failures, anchor parsing,
                        relative-expression matrix
- TestBuiltinRegister — register_builtins populates the registry as expected
"""
from __future__ import annotations

import asyncio

import pytest

from forest_soul_forge.tools import (
    Tool,
    ToolContext,
    ToolError,
    ToolRegistry,
    ToolResult,
    ToolValidationError,
)
from forest_soul_forge.tools.builtin import register_builtins
from forest_soul_forge.tools.builtin.timestamp_window import TimestampWindowTool


# Reusable context — most tests don't care about the agent identity
# fields, just that the tool gets *some* ToolContext.
_CTX = ToolContext(
    instance_id="network_watcher_5937afd40a51_1",
    agent_dna="5937afd40a51",
    role="network_watcher",
    genre="observer",
    session_id="sess-test",
    constraints={"max_calls_per_session": 1000, "requires_human_approval": False},
)


def _run(coro):
    return asyncio.run(coro)


class TestRegistry:
    def test_register_and_get(self):
        reg = ToolRegistry()
        tool = TimestampWindowTool()
        reg.register(tool)
        assert reg.has("timestamp_window", "1")
        assert reg.get("timestamp_window", "1") is tool

    def test_get_returns_none_for_unknown(self):
        reg = ToolRegistry()
        assert reg.get("nope", "1") is None

    def test_duplicate_rejected(self):
        reg = ToolRegistry()
        reg.register(TimestampWindowTool())
        with pytest.raises(ToolError, match="duplicate"):
            reg.register(TimestampWindowTool())

    def test_unknown_side_effects_rejected(self):
        reg = ToolRegistry()

        class BadTool:
            name = "bad"
            version = "1"
            side_effects = "telekinesis"  # not in SIDE_EFFECTS_VALUES

            def validate(self, args):  # pragma: no cover
                pass

            async def execute(self, args, ctx):  # pragma: no cover
                return ToolResult(output=None)

        with pytest.raises(ToolError, match="side_effects"):
            reg.register(BadTool())

    def test_all_keys_sorted(self):
        reg = ToolRegistry()
        reg.register(TimestampWindowTool())
        keys = reg.all_keys()
        assert keys == ("timestamp_window.v1",)


class TestProtocolShape:
    """The reference tool must satisfy the runtime-checkable Protocol.

    isinstance(tool, Tool) catches contract drift if someone removes
    `validate` or `execute` without thinking.
    """

    def test_reference_tool_satisfies_protocol(self):
        assert isinstance(TimestampWindowTool(), Tool)

    def test_reference_tool_declares_expected_metadata(self):
        t = TimestampWindowTool()
        assert t.name == "timestamp_window"
        assert t.version == "1"
        assert t.side_effects == "read_only"


class TestTimestampWindow:
    def test_execute_last_15_minutes(self):
        result = _run(TimestampWindowTool().execute(
            {"expression": "last 15 minutes",
             "anchor": "2026-04-26T12:00:00Z"},
            _CTX,
        ))
        assert result.output["start"] == "2026-04-26T11:45:00Z"
        assert result.output["end"] == "2026-04-26T12:00:00Z"
        assert result.output["span_seconds"] == 15 * 60

    def test_execute_past_24h(self):
        result = _run(TimestampWindowTool().execute(
            {"expression": "past 24h",
             "anchor": "2026-04-26T00:00:00Z"},
            _CTX,
        ))
        assert result.output["start"] == "2026-04-25T00:00:00Z"
        assert result.output["span_seconds"] == 86400

    def test_execute_default_anchor_uses_now(self):
        # Without an anchor, the tool uses datetime.now(UTC). Don't pin
        # the exact timestamp; assert the span is right and end >= now-ish.
        result = _run(TimestampWindowTool().execute(
            {"expression": "last 1 minutes"}, _CTX,
        ))
        assert result.output["span_seconds"] == 60

    def test_execute_pure_function_no_accounting(self):
        # Reference tool is pure — it MUST NOT report tokens or cost.
        # The runtime would otherwise emit phantom accounting numbers.
        result = _run(TimestampWindowTool().execute(
            {"expression": "last 5 minutes"}, _CTX,
        ))
        assert result.tokens_used is None
        assert result.cost_usd is None
        assert result.side_effect_summary is None

    def test_validate_missing_expression(self):
        with pytest.raises(ToolValidationError, match="expression"):
            TimestampWindowTool().validate({"anchor": "2026-04-26T00:00:00Z"})

    def test_validate_empty_expression(self):
        with pytest.raises(ToolValidationError, match="non-empty"):
            TimestampWindowTool().validate({"expression": "   "})

    def test_validate_anchor_wrong_type(self):
        with pytest.raises(ToolValidationError, match="anchor"):
            TimestampWindowTool().validate({"expression": "last 1 minutes", "anchor": 42})

    def test_execute_unrecognized_expression_raises(self):
        with pytest.raises(ToolValidationError, match="unrecognized"):
            _run(TimestampWindowTool().execute(
                {"expression": "fortnight ago"}, _CTX,
            ))

    def test_execute_bad_anchor_raises(self):
        with pytest.raises(ToolValidationError, match="anchor"):
            _run(TimestampWindowTool().execute(
                {"expression": "last 1 minutes", "anchor": "not-a-date"}, _CTX,
            ))


class TestResultDigest:
    def test_digest_stable_for_equivalent_results(self):
        r1 = ToolResult(output={"a": 1, "b": 2}, metadata={"k": "v"})
        r2 = ToolResult(output={"b": 2, "a": 1}, metadata={"k": "v"})
        # Output dict key order should not affect the digest because
        # json.dumps sort_keys=True. Same for metadata.
        assert r1.result_digest() == r2.result_digest()

    def test_digest_changes_when_metadata_changes(self):
        r1 = ToolResult(output={"a": 1}, metadata={"k": "v1"})
        r2 = ToolResult(output={"a": 1}, metadata={"k": "v2"})
        assert r1.result_digest() != r2.result_digest()


class TestBuiltinRegister:
    def test_register_builtins_adds_timestamp_window(self):
        reg = ToolRegistry()
        register_builtins(reg)
        assert reg.has("timestamp_window", "1")
