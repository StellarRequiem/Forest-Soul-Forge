"""Unit tests for the tool execution runtime — ADR-0019 T2.

Coverage:

- ``TestDispatchSucceeded``      — happy path, audit emission, counter inc
- ``TestDispatchRefused``        — every refusal reason in the matrix
- ``TestDispatchPendingApproval``— approval gate routes correctly
- ``TestDispatchFailed``         — tool errors → tool_call_failed event
- ``TestRegistryCounter``        — Registry.{get,increment}_tool_call_count

The dispatcher is built with in-memory fakes for the audit chain and
the per-session counter so the tests don't pull in the full daemon
lifespan.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.registry import Registry
from forest_soul_forge.tools.base import (
    ToolError,
    ToolRegistry,
)
from forest_soul_forge.tools.builtin.timestamp_window import TimestampWindowTool
from forest_soul_forge.tools.dispatcher import (
    EVENT_APPROVED,
    EVENT_DISPATCHED,
    EVENT_FAILED,
    EVENT_PENDING_APPROVAL,
    EVENT_REFUSED,
    EVENT_REJECTED,
    EVENT_SUCCEEDED,
    DispatchFailed,
    DispatchPendingApproval,
    DispatchRefused,
    DispatchSucceeded,
    ToolDispatcher,
)


# ---------------------------------------------------------------------------
# Fixtures + fakes
# ---------------------------------------------------------------------------
def _run(coro):
    return asyncio.run(coro)


def _write_constitution(
    path: Path,
    *,
    tool_name: str = "timestamp_window",
    tool_version: str = "1",
    side_effects: str = "read_only",
    requires_approval: bool = False,
    max_calls: int = 1000,
) -> None:
    """Drop a constitution.yaml stub at ``path`` whose tools[] block
    matches the dispatcher's lookup. Only the fields the dispatcher
    reads are populated."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        f"schema_version: 1\n"
        f"agent:\n"
        f"  role: network_watcher\n"
        f"tools:\n"
        f"  - name: {tool_name}\n"
        f"    version: '{tool_version}'\n"
        f"    side_effects: {side_effects}\n"
        f"    constraints:\n"
        f"      max_calls_per_session: {max_calls}\n"
        f"      requires_human_approval: {str(requires_approval).lower()}\n"
        f"    applied_rules: []\n"
    )
    path.write_text(body, encoding="utf-8")


@pytest.fixture
def dispatcher_env(tmp_path):
    """Build a dispatcher wired against a real AuditChain + an
    in-memory counter dict. Returns the parts the tests need to
    inspect."""
    chain_path = tmp_path / "audit/chain.jsonl"
    chain = AuditChain(chain_path)

    counters: dict[tuple[str, str], int] = {}

    def get_count(instance_id: str, session_id: str) -> int:
        return counters.get((instance_id, session_id), 0)

    def inc_count(instance_id: str, session_id: str, when: str) -> int:
        key = (instance_id, session_id)
        counters[key] = counters.get(key, 0) + 1
        return counters[key]

    registry = ToolRegistry()
    registry.register(TimestampWindowTool())

    dispatcher = ToolDispatcher(
        registry=registry,
        audit=chain,
        counter_get=get_count,
        counter_inc=inc_count,
    )
    return {
        "dispatcher": dispatcher,
        "chain": chain,
        "registry": registry,
        "counters": counters,
        "tmp_path": tmp_path,
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
class TestDispatchSucceeded:
    def test_happy_path_returns_succeeded(self, dispatcher_env):
        constitution = dispatcher_env["tmp_path"] / "constitution.yaml"
        _write_constitution(constitution)

        outcome = _run(dispatcher_env["dispatcher"].dispatch(
            instance_id="i1",
            agent_dna="d" * 12,
            role="network_watcher",
            genre="observer",
            session_id="s1",
            constitution_path=constitution,
            tool_name="timestamp_window",
            tool_version="1",
            args={"expression": "last 5 minutes",
                  "anchor": "2026-04-26T12:00:00Z"},
        ))
        assert isinstance(outcome, DispatchSucceeded)
        assert outcome.tool_key == "timestamp_window.v1"
        assert outcome.call_count_after == 1
        assert outcome.result.output["span_seconds"] == 300

    def test_emits_dispatched_then_succeeded_in_order(self, dispatcher_env):
        constitution = dispatcher_env["tmp_path"] / "constitution.yaml"
        _write_constitution(constitution)

        _run(dispatcher_env["dispatcher"].dispatch(
            instance_id="i1", agent_dna="d" * 12, role="network_watcher",
            genre="observer", session_id="s1",
            constitution_path=constitution,
            tool_name="timestamp_window", tool_version="1",
            args={"expression": "last 1 minutes"},
        ))
        # Genesis + dispatched + succeeded = 3 entries
        all_events = dispatcher_env["chain"].read_all()
        types = [e.event_type for e in all_events]
        assert types[-2:] == [EVENT_DISPATCHED, EVENT_SUCCEEDED]

    def test_counter_increments_on_each_dispatch(self, dispatcher_env):
        constitution = dispatcher_env["tmp_path"] / "constitution.yaml"
        _write_constitution(constitution)

        for n in range(1, 4):
            outcome = _run(dispatcher_env["dispatcher"].dispatch(
                instance_id="i1", agent_dna="d" * 12, role="network_watcher",
                genre="observer", session_id="s1",
                constitution_path=constitution,
                tool_name="timestamp_window", tool_version="1",
                args={"expression": "last 1 minutes"},
            ))
            assert isinstance(outcome, DispatchSucceeded)
            assert outcome.call_count_after == n

    def test_succeeded_event_carries_result_digest(self, dispatcher_env):
        constitution = dispatcher_env["tmp_path"] / "constitution.yaml"
        _write_constitution(constitution)

        outcome = _run(dispatcher_env["dispatcher"].dispatch(
            instance_id="i1", agent_dna="d" * 12, role="network_watcher",
            genre="observer", session_id="s1",
            constitution_path=constitution,
            tool_name="timestamp_window", tool_version="1",
            args={"expression": "last 1 minutes"},
        ))
        succeeded = [
            e for e in dispatcher_env["chain"].read_all()
            if e.event_type == EVENT_SUCCEEDED
        ][-1]
        assert succeeded.event_data["result_digest"] == outcome.result.result_digest()


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------
class TestDispatchRefused:
    def test_unknown_tool(self, dispatcher_env):
        constitution = dispatcher_env["tmp_path"] / "constitution.yaml"
        _write_constitution(constitution)

        outcome = _run(dispatcher_env["dispatcher"].dispatch(
            instance_id="i1", agent_dna="d" * 12, role="network_watcher",
            genre="observer", session_id="s1",
            constitution_path=constitution,
            tool_name="not_real", tool_version="1",
            args={},
        ))
        assert isinstance(outcome, DispatchRefused)
        assert outcome.reason == "unknown_tool"

    def test_bad_args(self, dispatcher_env):
        constitution = dispatcher_env["tmp_path"] / "constitution.yaml"
        _write_constitution(constitution)

        outcome = _run(dispatcher_env["dispatcher"].dispatch(
            instance_id="i1", agent_dna="d" * 12, role="network_watcher",
            genre="observer", session_id="s1",
            constitution_path=constitution,
            tool_name="timestamp_window", tool_version="1",
            # Missing required 'expression' arg.
            args={"anchor": "2026-04-26T12:00:00Z"},
        ))
        assert isinstance(outcome, DispatchRefused)
        assert outcome.reason == "bad_args"
        assert "expression" in outcome.detail

    def test_constitution_missing(self, dispatcher_env):
        outcome = _run(dispatcher_env["dispatcher"].dispatch(
            instance_id="i1", agent_dna="d" * 12, role="network_watcher",
            genre="observer", session_id="s1",
            constitution_path=dispatcher_env["tmp_path"] / "nope.yaml",
            tool_name="timestamp_window", tool_version="1",
            args={"expression": "last 1 minutes"},
        ))
        assert isinstance(outcome, DispatchRefused)
        assert outcome.reason == "constitution_missing"

    def test_tool_not_in_constitution(self, dispatcher_env):
        # Constitution exists but lists a different tool.
        constitution = dispatcher_env["tmp_path"] / "constitution.yaml"
        _write_constitution(constitution, tool_name="other_tool")

        outcome = _run(dispatcher_env["dispatcher"].dispatch(
            instance_id="i1", agent_dna="d" * 12, role="network_watcher",
            genre="observer", session_id="s1",
            constitution_path=constitution,
            tool_name="timestamp_window", tool_version="1",
            args={"expression": "last 1 minutes"},
        ))
        assert isinstance(outcome, DispatchRefused)
        assert outcome.reason == "tool_not_in_constitution"

    def test_max_calls_exceeded(self, dispatcher_env):
        constitution = dispatcher_env["tmp_path"] / "constitution.yaml"
        _write_constitution(constitution, max_calls=2)

        # Call twice up to the cap, third call refused.
        for _ in range(2):
            outcome = _run(dispatcher_env["dispatcher"].dispatch(
                instance_id="i1", agent_dna="d" * 12, role="network_watcher",
                genre="observer", session_id="s1",
                constitution_path=constitution,
                tool_name="timestamp_window", tool_version="1",
                args={"expression": "last 1 minutes"},
            ))
            assert isinstance(outcome, DispatchSucceeded)

        outcome = _run(dispatcher_env["dispatcher"].dispatch(
            instance_id="i1", agent_dna="d" * 12, role="network_watcher",
            genre="observer", session_id="s1",
            constitution_path=constitution,
            tool_name="timestamp_window", tool_version="1",
            args={"expression": "last 1 minutes"},
        ))
        assert isinstance(outcome, DispatchRefused)
        assert outcome.reason == "max_calls_exceeded"

    def test_refusal_emits_audit_entry(self, dispatcher_env):
        constitution = dispatcher_env["tmp_path"] / "constitution.yaml"
        _write_constitution(constitution)

        _run(dispatcher_env["dispatcher"].dispatch(
            instance_id="i1", agent_dna="d" * 12, role="network_watcher",
            genre="observer", session_id="s1",
            constitution_path=constitution,
            tool_name="not_real", tool_version="1",
            args={},
        ))
        refusals = [
            e for e in dispatcher_env["chain"].read_all()
            if e.event_type == EVENT_REFUSED
        ]
        assert len(refusals) == 1
        assert refusals[0].event_data["reason"] == "unknown_tool"

    def test_bad_args_does_not_increment_counter(self, dispatcher_env):
        """Validation refusal must not burn budget — a typo from the LLM
        shouldn't count against ``max_calls_per_session``."""
        constitution = dispatcher_env["tmp_path"] / "constitution.yaml"
        _write_constitution(constitution)

        _run(dispatcher_env["dispatcher"].dispatch(
            instance_id="i1", agent_dna="d" * 12, role="network_watcher",
            genre="observer", session_id="s1",
            constitution_path=constitution,
            tool_name="timestamp_window", tool_version="1",
            args={},  # bad — missing required expression
        ))
        assert dispatcher_env["counters"].get(("i1", "s1"), 0) == 0


# ---------------------------------------------------------------------------
# Approval gate
# ---------------------------------------------------------------------------
class TestDispatchPendingApproval:
    def test_approval_required_returns_pending(self, dispatcher_env):
        constitution = dispatcher_env["tmp_path"] / "constitution.yaml"
        _write_constitution(constitution, requires_approval=True)

        outcome = _run(dispatcher_env["dispatcher"].dispatch(
            instance_id="i1", agent_dna="d" * 12, role="network_watcher",
            genre="observer", session_id="s1",
            constitution_path=constitution,
            tool_name="timestamp_window", tool_version="1",
            args={"expression": "last 1 minutes"},
        ))
        assert isinstance(outcome, DispatchPendingApproval)
        assert outcome.ticket_id.startswith("pending-i1-s1-")

    def test_approval_emits_pending_event(self, dispatcher_env):
        constitution = dispatcher_env["tmp_path"] / "constitution.yaml"
        _write_constitution(constitution, requires_approval=True)

        _run(dispatcher_env["dispatcher"].dispatch(
            instance_id="i1", agent_dna="d" * 12, role="network_watcher",
            genre="observer", session_id="s1",
            constitution_path=constitution,
            tool_name="timestamp_window", tool_version="1",
            args={"expression": "last 1 minutes"},
        ))
        pending = [
            e for e in dispatcher_env["chain"].read_all()
            if e.event_type == EVENT_PENDING_APPROVAL
        ]
        assert len(pending) == 1

    def test_approval_does_not_increment_counter(self, dispatcher_env):
        """A queued approval shouldn't burn the call budget — the call
        hasn't actually executed yet. T3 increments on resume."""
        constitution = dispatcher_env["tmp_path"] / "constitution.yaml"
        _write_constitution(constitution, requires_approval=True)

        _run(dispatcher_env["dispatcher"].dispatch(
            instance_id="i1", agent_dna="d" * 12, role="network_watcher",
            genre="observer", session_id="s1",
            constitution_path=constitution,
            tool_name="timestamp_window", tool_version="1",
            args={"expression": "last 1 minutes"},
        ))
        assert dispatcher_env["counters"].get(("i1", "s1"), 0) == 0


# ---------------------------------------------------------------------------
# Execute-time failure
# ---------------------------------------------------------------------------
class _RaisingTool:
    """Test fixture: a tool that always raises mid-execute."""

    name = "raises"
    version = "1"
    side_effects = "read_only"

    def validate(self, args):
        return None

    async def execute(self, args, ctx):
        raise ToolError("intentional failure for test")


class _UnexpectedRaisingTool:
    """Test fixture: raises a non-ToolError so we exercise the
    'unexpected_exception' branch."""

    name = "blows_up"
    version = "1"
    side_effects = "read_only"

    def validate(self, args):
        return None

    async def execute(self, args, ctx):
        raise RuntimeError("oh no")


class TestDispatchFailed:
    def test_tool_error_returns_failed(self, dispatcher_env):
        dispatcher_env["registry"].register(_RaisingTool())
        constitution = dispatcher_env["tmp_path"] / "constitution.yaml"
        _write_constitution(constitution, tool_name="raises")

        outcome = _run(dispatcher_env["dispatcher"].dispatch(
            instance_id="i1", agent_dna="d" * 12, role="network_watcher",
            genre="observer", session_id="s1",
            constitution_path=constitution,
            tool_name="raises", tool_version="1",
            args={},
        ))
        assert isinstance(outcome, DispatchFailed)
        assert outcome.exception_type == "ToolError"

    def test_unexpected_exception_marked_unexpected(self, dispatcher_env):
        dispatcher_env["registry"].register(_UnexpectedRaisingTool())
        constitution = dispatcher_env["tmp_path"] / "constitution.yaml"
        _write_constitution(constitution, tool_name="blows_up")

        _run(dispatcher_env["dispatcher"].dispatch(
            instance_id="i1", agent_dna="d" * 12, role="network_watcher",
            genre="observer", session_id="s1",
            constitution_path=constitution,
            tool_name="blows_up", tool_version="1",
            args={},
        ))
        failed = [
            e for e in dispatcher_env["chain"].read_all()
            if e.event_type == EVENT_FAILED
        ][-1]
        assert failed.event_data["unexpected"] is True

    def test_failed_dispatch_still_increments_counter(self, dispatcher_env):
        """A crashing tool burns a call slot — otherwise an adversarial
        tool could DoS the budget by always raising.
        """
        dispatcher_env["registry"].register(_RaisingTool())
        constitution = dispatcher_env["tmp_path"] / "constitution.yaml"
        _write_constitution(constitution, tool_name="raises")

        _run(dispatcher_env["dispatcher"].dispatch(
            instance_id="i1", agent_dna="d" * 12, role="network_watcher",
            genre="observer", session_id="s1",
            constitution_path=constitution,
            tool_name="raises", tool_version="1",
            args={},
        ))
        assert dispatcher_env["counters"][("i1", "s1")] == 1


# ---------------------------------------------------------------------------
# Registry counter accessors (T2a)
# ---------------------------------------------------------------------------
class TestRegistryCounter:
    def test_counter_starts_at_zero(self, tmp_path):
        with Registry.bootstrap(tmp_path / "reg.sqlite") as r:
            assert r.get_tool_call_count("i1", "s1") == 0

    def test_increment_creates_row(self, tmp_path):
        with Registry.bootstrap(tmp_path / "reg.sqlite") as r:
            n = r.increment_tool_call_count("i1", "s1", "2026-04-26T00:00:00Z")
            assert n == 1
            assert r.get_tool_call_count("i1", "s1") == 1

    def test_increment_is_per_session(self, tmp_path):
        with Registry.bootstrap(tmp_path / "reg.sqlite") as r:
            r.increment_tool_call_count("i1", "s1", "2026-04-26T00:00:00Z")
            r.increment_tool_call_count("i1", "s1", "2026-04-26T00:00:01Z")
            r.increment_tool_call_count("i1", "s2", "2026-04-26T00:00:02Z")
            assert r.get_tool_call_count("i1", "s1") == 2
            assert r.get_tool_call_count("i1", "s2") == 1


# ---------------------------------------------------------------------------
# T4 — per-call accounting (tool_calls table + dispatcher mirror)
# ---------------------------------------------------------------------------
class TestRegistryToolCalls:
    def test_record_persists_row(self, tmp_path):
        with Registry.bootstrap(tmp_path / "reg.sqlite") as r:
            r.record_tool_call(
                audit_seq=42, instance_id="i1", session_id="s1",
                tool_key="timestamp_window.v1", status="succeeded",
                tokens_used=None, cost_usd=None,
                side_effect_summary=None,
                finished_at="2026-04-27T00:00:00Z",
            )
            agg = r.aggregate_tool_calls("i1")
            assert agg["total_invocations"] == 1
            assert agg["failed_invocations"] == 0
            assert agg["total_tokens_used"] is None
            assert agg["total_cost_usd"] is None
            assert agg["last_active_at"] == "2026-04-27T00:00:00Z"

    def test_aggregate_sums_tokens_and_cost(self, tmp_path):
        with Registry.bootstrap(tmp_path / "reg.sqlite") as r:
            r.record_tool_call(
                audit_seq=1, instance_id="i1", session_id="s1",
                tool_key="summarize.v1", status="succeeded",
                tokens_used=500, cost_usd=0.005,
                side_effect_summary=None,
                finished_at="2026-04-27T00:00:00Z",
            )
            r.record_tool_call(
                audit_seq=2, instance_id="i1", session_id="s1",
                tool_key="summarize.v1", status="succeeded",
                tokens_used=1500, cost_usd=0.015,
                side_effect_summary=None,
                finished_at="2026-04-27T00:00:01Z",
            )
            agg = r.aggregate_tool_calls("i1")
            assert agg["total_tokens_used"] == 2000
            assert abs(agg["total_cost_usd"] - 0.020) < 1e-9

    def test_aggregate_distinguishes_none_from_zero(self, tmp_path):
        """None totals (no LLM-wrapping tool ever ran) must not collapse
        to 0, which would look like 'ran with zero tokens'."""
        with Registry.bootstrap(tmp_path / "reg.sqlite") as r:
            # Pure-function tool: no tokens, no cost.
            r.record_tool_call(
                audit_seq=1, instance_id="i1", session_id="s1",
                tool_key="timestamp_window.v1", status="succeeded",
                tokens_used=None, cost_usd=None,
                side_effect_summary=None,
                finished_at="2026-04-27T00:00:00Z",
            )
            agg = r.aggregate_tool_calls("i1")
            assert agg["total_tokens_used"] is None
            assert agg["total_cost_usd"] is None

    def test_aggregate_is_per_instance(self, tmp_path):
        with Registry.bootstrap(tmp_path / "reg.sqlite") as r:
            r.record_tool_call(
                audit_seq=1, instance_id="i1", session_id="s1",
                tool_key="t.v1", status="succeeded",
                tokens_used=None, cost_usd=None,
                side_effect_summary=None,
                finished_at="2026-04-27T00:00:00Z",
            )
            r.record_tool_call(
                audit_seq=2, instance_id="i2", session_id="s1",
                tool_key="t.v1", status="succeeded",
                tokens_used=None, cost_usd=None,
                side_effect_summary=None,
                finished_at="2026-04-27T00:00:00Z",
            )
            assert r.aggregate_tool_calls("i1")["total_invocations"] == 1
            assert r.aggregate_tool_calls("i2")["total_invocations"] == 1


class TestDispatcherRecording:
    """Dispatcher writes one tool_calls row per terminating event."""

    def test_succeeded_call_recorded(self, dispatcher_env):
        recorded: list[dict] = []
        dispatcher_env["dispatcher"].record_call = lambda **kw: recorded.append(kw)

        constitution = dispatcher_env["tmp_path"] / "constitution.yaml"
        _write_constitution(constitution)
        _run(dispatcher_env["dispatcher"].dispatch(
            instance_id="i1", agent_dna="d" * 12, role="network_watcher",
            genre="observer", session_id="s1",
            constitution_path=constitution,
            tool_name="timestamp_window", tool_version="1",
            args={"expression": "last 1 minutes"},
        ))
        assert len(recorded) == 1
        assert recorded[0]["status"] == "succeeded"
        assert recorded[0]["tool_key"] == "timestamp_window.v1"
        # Pure-function tool reports None tokens/cost.
        assert recorded[0]["tokens_used"] is None
        assert recorded[0]["cost_usd"] is None

    def test_failed_call_recorded(self, dispatcher_env):
        recorded: list[dict] = []
        dispatcher_env["dispatcher"].record_call = lambda **kw: recorded.append(kw)
        dispatcher_env["registry"].register(_RaisingTool())
        constitution = dispatcher_env["tmp_path"] / "constitution.yaml"
        _write_constitution(constitution, tool_name="raises")
        _run(dispatcher_env["dispatcher"].dispatch(
            instance_id="i1", agent_dna="d" * 12, role="network_watcher",
            genre="observer", session_id="s1",
            constitution_path=constitution,
            tool_name="raises", tool_version="1",
            args={},
        ))
        assert len(recorded) == 1
        assert recorded[0]["status"] == "failed"

    def test_refusal_not_recorded(self, dispatcher_env):
        """Refusals never reach execute, so they don't get a tool_calls
        row — only the audit chain. Otherwise a typo from an LLM would
        inflate the call count visible on the character sheet."""
        recorded: list[dict] = []
        dispatcher_env["dispatcher"].record_call = lambda **kw: recorded.append(kw)
        constitution = dispatcher_env["tmp_path"] / "constitution.yaml"
        _write_constitution(constitution)
        _run(dispatcher_env["dispatcher"].dispatch(
            instance_id="i1", agent_dna="d" * 12, role="network_watcher",
            genre="observer", session_id="s1",
            constitution_path=constitution,
            tool_name="not_real", tool_version="1",
            args={},
        ))
        assert recorded == []

    def test_record_call_none_does_not_break_dispatch(self, dispatcher_env):
        """The default fixture sets record_call=None; succeeded dispatch
        should still complete cleanly."""
        constitution = dispatcher_env["tmp_path"] / "constitution.yaml"
        _write_constitution(constitution)
        outcome = _run(dispatcher_env["dispatcher"].dispatch(
            instance_id="i1", agent_dna="d" * 12, role="network_watcher",
            genre="observer", session_id="s1",
            constitution_path=constitution,
            tool_name="timestamp_window", tool_version="1",
            args={"expression": "last 1 minutes"},
        ))
        assert isinstance(outcome, DispatchSucceeded)


# ---------------------------------------------------------------------------
# T3 — approval queue persistence + resume path
# ---------------------------------------------------------------------------
class TestPendingPersistence:
    """The dispatcher persists a pending row when the approval gate fires."""

    def test_pending_writer_called_on_gate(self, dispatcher_env):
        written: list[dict] = []
        dispatcher_env["dispatcher"].pending_writer = lambda **kw: written.append(kw)
        constitution = dispatcher_env["tmp_path"] / "constitution.yaml"
        _write_constitution(constitution, requires_approval=True)

        outcome = _run(dispatcher_env["dispatcher"].dispatch(
            instance_id="i1", agent_dna="d" * 12, role="network_watcher",
            genre="observer", session_id="s1",
            constitution_path=constitution,
            tool_name="timestamp_window", tool_version="1",
            args={"expression": "last 1 minutes"},
        ))
        assert isinstance(outcome, DispatchPendingApproval)
        assert len(written) == 1
        row = written[0]
        assert row["ticket_id"] == outcome.ticket_id
        assert row["instance_id"] == "i1"
        assert row["session_id"] == "s1"
        assert row["tool_key"] == "timestamp_window.v1"
        # args_json round-trips: stored canonical so it can be replayed.
        import json
        assert json.loads(row["args_json"]) == {"expression": "last 1 minutes"}

    def test_pending_writer_none_does_not_break_gate(self, dispatcher_env):
        """pending_writer=None still mints ticket_id, just doesn't persist."""
        constitution = dispatcher_env["tmp_path"] / "constitution.yaml"
        _write_constitution(constitution, requires_approval=True)
        outcome = _run(dispatcher_env["dispatcher"].dispatch(
            instance_id="i1", agent_dna="d" * 12, role="network_watcher",
            genre="observer", session_id="s1",
            constitution_path=constitution,
            tool_name="timestamp_window", tool_version="1",
            args={"expression": "last 1 minutes"},
        ))
        assert isinstance(outcome, DispatchPendingApproval)
        assert outcome.ticket_id


class TestResumeApproved:
    """resume_approved replays a gated tool with the approval bypass."""

    def test_resume_succeeds_and_increments_counter(self, dispatcher_env):
        constitution = dispatcher_env["tmp_path"] / "constitution.yaml"
        _write_constitution(constitution, requires_approval=True)
        # Initial dispatch — gates.
        gated = _run(dispatcher_env["dispatcher"].dispatch(
            instance_id="i1", agent_dna="d" * 12, role="network_watcher",
            genre="observer", session_id="s1",
            constitution_path=constitution,
            tool_name="timestamp_window", tool_version="1",
            args={"expression": "last 1 minutes"},
        ))
        assert isinstance(gated, DispatchPendingApproval)
        assert dispatcher_env["counters"].get(("i1", "s1"), 0) == 0  # not burned

        # Resume — runs the tool past the approval gate.
        resumed = _run(dispatcher_env["dispatcher"].resume_approved(
            ticket_id=gated.ticket_id,
            operator_id="alex",
            instance_id="i1", agent_dna="d" * 12,
            role="network_watcher", genre="observer", session_id="s1",
            constitution_path=constitution,
            tool_name="timestamp_window", tool_version="1",
            args={"expression": "last 1 minutes"},
        ))
        assert isinstance(resumed, DispatchSucceeded)
        # Counter increments now.
        assert dispatcher_env["counters"][("i1", "s1")] == 1

    def test_resume_emits_dispatched_with_resumed_from_ticket(self, dispatcher_env):
        constitution = dispatcher_env["tmp_path"] / "constitution.yaml"
        _write_constitution(constitution, requires_approval=True)
        gated = _run(dispatcher_env["dispatcher"].dispatch(
            instance_id="i1", agent_dna="d" * 12, role="network_watcher",
            genre="observer", session_id="s1",
            constitution_path=constitution,
            tool_name="timestamp_window", tool_version="1",
            args={"expression": "last 1 minutes"},
        ))
        _run(dispatcher_env["dispatcher"].resume_approved(
            ticket_id=gated.ticket_id, operator_id="alex",
            instance_id="i1", agent_dna="d" * 12,
            role="network_watcher", genre="observer", session_id="s1",
            constitution_path=constitution,
            tool_name="timestamp_window", tool_version="1",
            args={"expression": "last 1 minutes"},
        ))
        dispatched = [
            e for e in dispatcher_env["chain"].read_all()
            if e.event_type == EVENT_DISPATCHED
        ][-1]
        assert dispatched.event_data["resumed_from_ticket"] == gated.ticket_id
        assert dispatched.event_data["approved_by"] == "alex"

    def test_resume_unknown_tool_refuses(self, dispatcher_env):
        # Persist a pending row for a tool name that doesn't exist in
        # the registry (simulates the tool getting hot-unloaded between
        # queue and approve).
        constitution = dispatcher_env["tmp_path"] / "constitution.yaml"
        _write_constitution(constitution, requires_approval=True)
        outcome = _run(dispatcher_env["dispatcher"].resume_approved(
            ticket_id="pending-i1-s1-99",
            operator_id="alex",
            instance_id="i1", agent_dna="d" * 12,
            role="network_watcher", genre="observer", session_id="s1",
            constitution_path=constitution,
            tool_name="ghost_tool", tool_version="1",
            args={},
        ))
        assert isinstance(outcome, DispatchRefused)
        assert outcome.reason == "unknown_tool"


class TestApprovalEvents:
    def test_emit_approved_appends_chain_entry(self, dispatcher_env):
        seq = dispatcher_env["dispatcher"].emit_approved_event(
            ticket_id="pending-x",
            instance_id="i1", agent_dna="d" * 12, session_id="s1",
            tool_key="timestamp_window.v1", operator_id="alex",
        )
        assert isinstance(seq, int)
        approved = [
            e for e in dispatcher_env["chain"].read_all()
            if e.event_type == EVENT_APPROVED
        ]
        assert len(approved) == 1
        assert approved[0].event_data["ticket_id"] == "pending-x"
        assert approved[0].event_data["operator_id"] == "alex"

    def test_emit_rejected_appends_chain_entry_with_reason(self, dispatcher_env):
        dispatcher_env["dispatcher"].emit_rejected_event(
            ticket_id="pending-x",
            instance_id="i1", agent_dna="d" * 12, session_id="s1",
            tool_key="timestamp_window.v1", operator_id="alex",
            reason="not now",
        )
        rejected = [
            e for e in dispatcher_env["chain"].read_all()
            if e.event_type == EVENT_REJECTED
        ]
        assert len(rejected) == 1
        assert rejected[0].event_data["reason"] == "not now"


class TestRegistryApprovalQueue:
    def test_record_and_get(self, tmp_path):
        with Registry.bootstrap(tmp_path / "reg.sqlite") as r:
            r.record_pending_approval(
                ticket_id="t1", instance_id="i1", session_id="s1",
                tool_key="timestamp_window.v1",
                args_json='{"expression":"last 1 minutes"}',
                side_effects="read_only",
                pending_audit_seq=42,
                created_at="2026-04-27T00:00:00Z",
            )
            row = r.get_pending_approval("t1")
            assert row is not None
            assert row["status"] == "pending"
            assert row["instance_id"] == "i1"

    def test_list_filters_by_status(self, tmp_path):
        with Registry.bootstrap(tmp_path / "reg.sqlite") as r:
            r.record_pending_approval(
                ticket_id="t1", instance_id="i1", session_id="s1",
                tool_key="t.v1", args_json="{}", side_effects="read_only",
                pending_audit_seq=1, created_at="2026-04-27T00:00:00Z",
            )
            r.record_pending_approval(
                ticket_id="t2", instance_id="i1", session_id="s1",
                tool_key="t.v1", args_json="{}", side_effects="read_only",
                pending_audit_seq=2, created_at="2026-04-27T00:00:01Z",
            )
            r.mark_approval_decided(
                "t1", status="approved", decided_audit_seq=99,
                decided_by="alex", decision_reason=None,
                decided_at="2026-04-27T00:01:00Z",
            )
            pending = r.list_pending_approvals("i1", status="pending")
            assert [p["ticket_id"] for p in pending] == ["t2"]
            full = r.list_pending_approvals("i1", status=None)
            assert [p["ticket_id"] for p in full] == ["t1", "t2"]

    def test_double_decide_returns_false(self, tmp_path):
        with Registry.bootstrap(tmp_path / "reg.sqlite") as r:
            r.record_pending_approval(
                ticket_id="t1", instance_id="i1", session_id="s1",
                tool_key="t.v1", args_json="{}", side_effects="read_only",
                pending_audit_seq=1, created_at="2026-04-27T00:00:00Z",
            )
            ok1 = r.mark_approval_decided(
                "t1", status="approved", decided_audit_seq=99,
                decided_by="alex", decision_reason=None,
                decided_at="2026-04-27T00:01:00Z",
            )
            ok2 = r.mark_approval_decided(
                "t1", status="rejected", decided_audit_seq=100,
                decided_by="alex", decision_reason="changed mind",
                decided_at="2026-04-27T00:02:00Z",
            )
            assert ok1 is True
            assert ok2 is False  # already decided
