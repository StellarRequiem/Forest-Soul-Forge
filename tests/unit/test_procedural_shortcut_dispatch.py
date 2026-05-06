"""ADR-0054 T3 (Burst 180) — ProceduralShortcutStep + dispatcher tests.

Coverage:

  StepResult — new SHORTCUT verdict
    - shortcut() factory builds the right shape
    - is_shortcut / terminal properties

  ProceduralShortcutStep — sync verdict converter
    - dctx.shortcut_match=None → GO
    - dctx.shortcut_match=(candidate, score) → SHORTCUT
    - SHORTCUT verdict carries the candidate + similarity
    - malformed shortcut_match → GO (defensive, no crash)

  Dispatcher pre-resolution (_resolve_shortcut_match)
    - substrate unwired (table=None)         → None
    - master switch off                      → None
    - wrong tool_name                        → None
    - wrong task_kind                        → None
    - empty / non-string prompt              → None
    - provider lacks embed()                 → None
    - posture=red                            → None
    - posture=yellow                         → None
    - search returns empty                   → None
    - embed_situation raises EmbeddingError  → None
    - happy path returns (candidate, score)  → tuple

  Dispatcher SHORTCUT branch (_shortcut_substitute)
    - action_kind=response → DispatchSucceeded with synthetic result
    - tool_call_dispatched + tool_call_succeeded both emit
    - shortcut_applied=True metadata in BOTH events
    - record_match() fires on the table
    - tokens_used=0 on the synthetic result
    - action_kind=tool_call/no_op → DispatchFailed with
      ShortcutUnsupportedKind
    - counter still increments (a shortcut costs a slot)

All tests use stub providers + fakes so no Ollama / no real DB.
"""
from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass

import numpy as np
import pytest

from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.core.memory.procedural_embedding import EmbeddingError
from forest_soul_forge.tools.base import ToolRegistry
from forest_soul_forge.tools.builtin.llm_think import LlmThinkTool
from forest_soul_forge.tools.dispatcher import (
    EVENT_DISPATCHED,
    EVENT_FAILED,
    EVENT_SUCCEEDED,
    DispatchFailed,
    DispatchSucceeded,
    ToolDispatcher,
)
from forest_soul_forge.tools.governance_pipeline import (
    DispatchContext,
    ProceduralShortcutStep,
    StepResult,
)


# ---------------------------------------------------------------------------
# StepResult.shortcut + ProceduralShortcutStep — pure unit tests
# ---------------------------------------------------------------------------

class TestStepResultShortcut:
    def test_shortcut_factory_builds_shortcut_verdict(self):
        sentinel = object()
        r = StepResult.shortcut(sentinel, 0.97)
        assert r.verdict == "SHORTCUT"
        assert r.shortcut_candidate is sentinel
        assert r.shortcut_similarity == 0.97

    def test_shortcut_is_terminal(self):
        r = StepResult.shortcut(object(), 0.95)
        assert r.terminal is True

    def test_shortcut_is_shortcut_property(self):
        r = StepResult.shortcut(object(), 0.95)
        assert r.is_shortcut is True
        assert r.is_refuse is False
        assert r.is_pending is False

    def test_go_is_not_shortcut(self):
        assert StepResult.go().is_shortcut is False

    def test_refuse_is_not_shortcut(self):
        assert StepResult.refuse("x", "y").is_shortcut is False

    def test_pending_is_not_shortcut(self):
        assert StepResult.pending("constraint", "external").is_shortcut is False


class TestProceduralShortcutStep:
    def _ctx(self, shortcut_match=None) -> DispatchContext:
        from pathlib import Path
        return DispatchContext(
            instance_id="i1",
            agent_dna="d" * 12,
            role="assistant",
            genre="companion",
            session_id="s1",
            constitution_path=Path("/tmp/nonexistent.yaml"),
            tool_name="llm_think",
            tool_version="1",
            args={"prompt": "hi"},
            shortcut_match=shortcut_match,
        )

    def test_no_match_returns_go(self):
        step = ProceduralShortcutStep()
        result = step.evaluate(self._ctx(shortcut_match=None))
        assert result.verdict == "GO"

    def test_match_returns_shortcut(self):
        candidate = object()
        step = ProceduralShortcutStep()
        result = step.evaluate(self._ctx(shortcut_match=(candidate, 0.99)))
        assert result.verdict == "SHORTCUT"
        assert result.shortcut_candidate is candidate
        assert result.shortcut_similarity == pytest.approx(0.99)

    def test_malformed_match_falls_through_to_go(self):
        """A future caller-bug stuffing a non-tuple shape into
        shortcut_match must not crash dispatch."""
        step = ProceduralShortcutStep()
        # not iterable → fall through
        result = step.evaluate(self._ctx(shortcut_match=42))
        assert result.verdict == "GO"


# ---------------------------------------------------------------------------
# Dispatcher pre-resolution: _resolve_shortcut_match eligibility chain
# ---------------------------------------------------------------------------

@dataclass
class _FakeShortcut:
    """Stand-in for ProceduralShortcut. Only the fields the dispatcher
    reads are set."""
    shortcut_id: str
    action_kind: str
    action_payload: dict


class _FakeShortcutsTable:
    """Records calls and returns a configurable result list."""

    def __init__(self, *, return_value=None, raises=None):
        self._ret = return_value or []
        self._raises = raises
        self.search_calls: list[tuple] = []
        self.record_match_calls: list[tuple] = []

    def search_by_cosine(
        self, instance_id, query_embedding, *,
        cosine_floor=0.92, reinforcement_floor=2, top_k=1,
    ):
        self.search_calls.append((
            instance_id, query_embedding, cosine_floor,
            reinforcement_floor, top_k,
        ))
        if self._raises is not None:
            raise self._raises
        return self._ret

    def record_match(self, shortcut_id, *, at_seq, when=None):
        self.record_match_calls.append((shortcut_id, at_seq, when))


class _ProviderWithEmbed:
    """Stub provider whose embed() returns a controllable vector."""

    name = "local"

    def __init__(self, *, embedding=None, raises=None):
        self._embedding = embedding if embedding is not None else [1.0, 0.0]
        self._raises = raises

    async def complete(self, *a, **kw):
        return "[stub]"

    async def embed(self, text, *, model=None):
        if self._raises is not None:
            raise self._raises
        return list(self._embedding)


class _ProviderNoEmbed:
    name = "frontier"

    async def complete(self, *a, **kw):
        return "[frontier stub]"


def _make_dispatcher(
    *,
    table=None,
    enabled=True,
    cosine_floor=0.5,
    reinforcement_floor=0,
    audit=None,
):
    """Wire a dispatcher with the shortcut substrate hooked in. The
    floors default to permissive values so the FakeShortcutsTable's
    canned response is what's tested, not the floor logic (covered
    by the table's own tests in test_procedural_shortcuts.py)."""
    chain = audit or AuditChain.__new__(AuditChain)  # placeholder
    counters: dict[tuple[str, str], int] = {}

    def get_count(instance_id, session_id):
        return counters.get((instance_id, session_id), 0)

    def inc_count(instance_id, session_id, when):
        key = (instance_id, session_id)
        counters[key] = counters.get(key, 0) + 1
        return counters[key]

    registry = ToolRegistry()
    registry.register(LlmThinkTool())

    return ToolDispatcher(
        registry=registry,
        audit=chain,
        counter_get=get_count,
        counter_inc=inc_count,
        procedural_shortcuts_table=table,
        procedural_shortcut_enabled_fn=lambda: enabled,
        procedural_cosine_floor_fn=lambda: cosine_floor,
        procedural_reinforcement_floor_fn=lambda: reinforcement_floor,
    )


def _run(coro):
    return asyncio.run(coro)


class TestResolveShortcutMatchEligibility:
    """The async _resolve_shortcut_match helper. Each test calls it
    directly with the gate-relevant arguments; the table fake
    captures whether search_by_cosine fired."""

    def _resolve(self, dispatcher, *, tool_name="llm_think",
                 args=None, provider=None, posture=None,
                 instance_id="i1"):
        return _run(dispatcher._resolve_shortcut_match(
            instance_id=instance_id,
            tool_name=tool_name,
            args=args if args is not None else {"prompt": "hi", "task_kind": "conversation"},
            provider=provider or _ProviderWithEmbed(),
            agent_posture=posture,
        ))

    def test_unwired_table_returns_none(self):
        d = _make_dispatcher(table=None)
        assert self._resolve(d) is None

    def test_master_switch_off_skips_embed(self):
        table = _FakeShortcutsTable()
        d = _make_dispatcher(table=table, enabled=False)
        result = self._resolve(d)
        assert result is None
        # Critical: when the switch is off, we never even ran search
        assert table.search_calls == []

    def test_wrong_tool_skips_embed(self):
        table = _FakeShortcutsTable()
        d = _make_dispatcher(table=table)
        result = self._resolve(d, tool_name="timestamp_window")
        assert result is None
        assert table.search_calls == []

    def test_wrong_task_kind_skips_embed(self):
        table = _FakeShortcutsTable()
        d = _make_dispatcher(table=table)
        result = self._resolve(d, args={"prompt": "hi", "task_kind": "classify"})
        assert result is None
        assert table.search_calls == []

    def test_empty_prompt_skips_embed(self):
        table = _FakeShortcutsTable()
        d = _make_dispatcher(table=table)
        for bad in [{"prompt": ""}, {"prompt": "   \n\t"}, {"prompt": None}, {}]:
            assert self._resolve(d, args=bad) is None
        assert table.search_calls == []

    def test_provider_without_embed_skips(self):
        table = _FakeShortcutsTable()
        d = _make_dispatcher(table=table)
        result = self._resolve(d, provider=_ProviderNoEmbed())
        assert result is None
        assert table.search_calls == []

    def test_red_posture_skips(self):
        table = _FakeShortcutsTable()
        d = _make_dispatcher(table=table)
        result = self._resolve(d, posture="red")
        assert result is None
        assert table.search_calls == []

    def test_yellow_posture_skips(self):
        table = _FakeShortcutsTable()
        d = _make_dispatcher(table=table)
        result = self._resolve(d, posture="yellow")
        assert result is None
        assert table.search_calls == []

    def test_green_posture_runs_search(self):
        candidate = _FakeShortcut("sc-1", "response", {"response": "hi back"})
        table = _FakeShortcutsTable(return_value=[(candidate, 0.95)])
        d = _make_dispatcher(table=table)
        result = self._resolve(d, posture="green")
        assert result is not None
        assert result[0] is candidate
        assert result[1] == pytest.approx(0.95)

    def test_none_posture_runs_search(self):
        """None posture means agent_registry is unwired (test contexts).
        We treat that as eligible — same posture as PostureGateStep."""
        candidate = _FakeShortcut("sc-2", "response", {"response": "ok"})
        table = _FakeShortcutsTable(return_value=[(candidate, 0.93)])
        d = _make_dispatcher(table=table)
        result = self._resolve(d, posture=None)
        assert result is not None

    def test_search_empty_returns_none(self):
        table = _FakeShortcutsTable(return_value=[])
        d = _make_dispatcher(table=table)
        assert self._resolve(d) is None
        assert len(table.search_calls) == 1   # search DID run

    def test_embed_error_returns_none(self):
        """If embed_situation raises (Ollama down etc.), fall through
        to llm_think — never crash dispatch."""
        # Provider's embed() raises ProviderUnavailable, which the
        # embed_situation helper wraps as EmbeddingError. We simulate
        # at the embed level here.
        class _BadProvider:
            name = "local"
            async def embed(self, text, *, model=None):
                raise EmbeddingError("simulated provider unavailable")

        table = _FakeShortcutsTable()
        d = _make_dispatcher(table=table)
        result = self._resolve(d, provider=_BadProvider())
        assert result is None

    def test_unexpected_exception_returns_none(self):
        """Any other unexpected exception in the resolver must NOT
        crash dispatch — defensive None return."""
        class _ExplodingProvider:
            name = "local"
            async def embed(self, text, *, model=None):
                raise RuntimeError("boom")
        d = _make_dispatcher(table=_FakeShortcutsTable())
        result = self._resolve(d, provider=_ExplodingProvider())
        assert result is None

    def test_table_search_exception_returns_none(self):
        table = _FakeShortcutsTable(raises=ValueError("bad floor"))
        d = _make_dispatcher(table=table)
        assert self._resolve(d) is None

    def test_resolver_passes_floors_through(self):
        """The runtime cosine + reinforcement floors should reach
        search_by_cosine via the injected closures."""
        candidate = _FakeShortcut("sc-3", "response", {"response": "x"})
        table = _FakeShortcutsTable(return_value=[(candidate, 0.99)])
        d = _make_dispatcher(
            table=table, cosine_floor=0.85, reinforcement_floor=4,
        )
        self._resolve(d)
        assert len(table.search_calls) == 1
        _, _, cf, rf, top_k = table.search_calls[0]
        assert cf == pytest.approx(0.85)
        assert rf == 4
        assert top_k == 1


# ---------------------------------------------------------------------------
# Dispatcher SHORTCUT substitute branch
# ---------------------------------------------------------------------------

class _RealAuditFixture:
    """Wrap a real AuditChain on a tmp file so we can inspect emissions."""

    def __init__(self, tmp_path):
        self.path = tmp_path / "shortcut_chain.jsonl"
        self.chain = AuditChain(self.path)


def _make_real_dispatcher(tmp_path, *, table, posture_for=None,
                          enabled=True):
    """Like _make_dispatcher but with a real on-disk AuditChain so the
    audit emission tests can read events back."""
    chain = AuditChain(tmp_path / "chain.jsonl")
    counters: dict[tuple[str, str], int] = {}

    def get_count(instance_id, session_id):
        return counters.get((instance_id, session_id), 0)

    def inc_count(instance_id, session_id, when):
        key = (instance_id, session_id)
        counters[key] = counters.get(key, 0) + 1
        return counters[key]

    registry = ToolRegistry()
    registry.register(LlmThinkTool())

    d = ToolDispatcher(
        registry=registry,
        audit=chain,
        counter_get=get_count,
        counter_inc=inc_count,
        procedural_shortcuts_table=table,
        procedural_shortcut_enabled_fn=lambda: enabled,
        procedural_cosine_floor_fn=lambda: 0.5,
        procedural_reinforcement_floor_fn=lambda: 0,
    )
    return d, chain, counters


def _write_llm_think_constitution(path):
    """Drop a constitution.yaml whose tools[] block matches llm_think.v1."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "schema_version: 1\n"
        "agent:\n"
        "  role: assistant\n"
        "tools:\n"
        "  - name: llm_think\n"
        "    version: '1'\n"
        "    side_effects: read_only\n"
        "    constraints:\n"
        "      max_calls_per_session: 1000\n"
        "      requires_human_approval: false\n"
        "    applied_rules: []\n",
        encoding="utf-8",
    )


class TestShortcutSubstitute:
    def test_response_kind_returns_succeeded_with_synthetic_result(
        self, tmp_path,
    ):
        candidate = _FakeShortcut(
            shortcut_id="sc-A",
            action_kind="response",
            action_payload={"response": "Hello, recorded answer."},
        )
        table = _FakeShortcutsTable(return_value=[(candidate, 0.97)])
        d, chain, counters = _make_real_dispatcher(tmp_path, table=table)
        cpath = tmp_path / "constitution.yaml"
        _write_llm_think_constitution(cpath)

        outcome = _run(d.dispatch(
            instance_id="i1",
            agent_dna="d" * 12,
            role="assistant",
            genre="companion",
            session_id="s1",
            constitution_path=cpath,
            tool_name="llm_think",
            tool_version="1",
            args={"prompt": "What time is it?",
                  "task_kind": "conversation"},
            provider=_ProviderWithEmbed(),
        ))
        assert isinstance(outcome, DispatchSucceeded)
        assert outcome.tool_key == "llm_think.v1"
        assert outcome.result.output["response"] == "Hello, recorded answer."
        assert outcome.result.output["model"] == "shortcut"
        assert outcome.result.tokens_used == 0
        assert outcome.call_count_after == 1
        # record_match() ran on the table
        assert len(table.record_match_calls) == 1
        assert table.record_match_calls[0][0] == "sc-A"

    def test_emits_dispatched_then_succeeded_with_shortcut_metadata(
        self, tmp_path,
    ):
        candidate = _FakeShortcut(
            shortcut_id="sc-B", action_kind="response",
            action_payload={"response": "ok"},
        )
        table = _FakeShortcutsTable(return_value=[(candidate, 0.96)])
        d, chain, _ = _make_real_dispatcher(tmp_path, table=table)
        cpath = tmp_path / "constitution.yaml"
        _write_llm_think_constitution(cpath)

        _run(d.dispatch(
            instance_id="i1", agent_dna="d" * 12, role="assistant",
            genre="companion", session_id="s1",
            constitution_path=cpath,
            tool_name="llm_think", tool_version="1",
            args={"prompt": "hi", "task_kind": "conversation"},
            provider=_ProviderWithEmbed(),
        ))
        events = chain.read_all()
        types = [e.event_type for e in events]
        # The last two events should be the dispatched + succeeded pair
        assert types[-2:] == [EVENT_DISPATCHED, EVENT_SUCCEEDED]
        for e in events[-2:]:
            assert e.event_data.get("shortcut_applied") is True
            assert e.event_data.get("shortcut_id") == "sc-B"
            assert e.event_data.get("shortcut_action_kind") == "response"
            assert "shortcut_similarity" in e.event_data

    def test_unsupported_action_kind_returns_failed(self, tmp_path):
        candidate = _FakeShortcut(
            shortcut_id="sc-C", action_kind="tool_call",
            action_payload={"tool_name": "memory_recall"},
        )
        table = _FakeShortcutsTable(return_value=[(candidate, 0.99)])
        d, chain, _ = _make_real_dispatcher(tmp_path, table=table)
        cpath = tmp_path / "constitution.yaml"
        _write_llm_think_constitution(cpath)

        outcome = _run(d.dispatch(
            instance_id="i1", agent_dna="d" * 12, role="assistant",
            genre="companion", session_id="s1",
            constitution_path=cpath,
            tool_name="llm_think", tool_version="1",
            args={"prompt": "hi", "task_kind": "conversation"},
            provider=_ProviderWithEmbed(),
        ))
        assert isinstance(outcome, DispatchFailed)
        assert outcome.exception_type == "ShortcutUnsupportedKind"
        events = chain.read_all()
        assert events[-1].event_type == EVENT_FAILED
        assert events[-1].event_data["shortcut_applied"] is True
        assert events[-1].event_data["shortcut_action_kind"] == "tool_call"

    def test_counter_still_increments_on_shortcut(self, tmp_path):
        """A shortcut hit costs a slot — otherwise an adversarial pattern
        could DoS max_calls_per_session by always matching."""
        candidate = _FakeShortcut(
            shortcut_id="sc-D", action_kind="response",
            action_payload={"response": "ok"},
        )
        table = _FakeShortcutsTable(return_value=[(candidate, 0.97)])
        d, _, counters = _make_real_dispatcher(tmp_path, table=table)
        cpath = tmp_path / "constitution.yaml"
        _write_llm_think_constitution(cpath)

        for n in (1, 2, 3):
            outcome = _run(d.dispatch(
                instance_id="i1", agent_dna="d" * 12, role="assistant",
                genre="companion", session_id="s1",
                constitution_path=cpath,
                tool_name="llm_think", tool_version="1",
                args={"prompt": f"q{n}", "task_kind": "conversation"},
                provider=_ProviderWithEmbed(),
            ))
            assert isinstance(outcome, DispatchSucceeded)
            assert outcome.call_count_after == n

    def test_no_match_falls_through_to_llm_think(self, tmp_path):
        """When no shortcut matches, dispatch hits the regular
        execute leg — llm_think runs and returns the provider's
        completion."""
        table = _FakeShortcutsTable(return_value=[])  # no match
        d, _, _ = _make_real_dispatcher(tmp_path, table=table)
        cpath = tmp_path / "constitution.yaml"
        _write_llm_think_constitution(cpath)

        outcome = _run(d.dispatch(
            instance_id="i1", agent_dna="d" * 12, role="assistant",
            genre="companion", session_id="s1",
            constitution_path=cpath,
            tool_name="llm_think", tool_version="1",
            args={"prompt": "hello", "task_kind": "conversation"},
            provider=_ProviderWithEmbed(),
        ))
        assert isinstance(outcome, DispatchSucceeded)
        assert outcome.result.output["model"] != "shortcut"
        # llm_think.v1 returns the provider's response
        assert outcome.result.output["response"] == "[stub]"

    def test_master_switch_off_falls_through(self, tmp_path):
        """When the master switch is off, dispatch never resolves a
        shortcut even if the table has matching rows."""
        candidate = _FakeShortcut(
            shortcut_id="sc-E", action_kind="response",
            action_payload={"response": "should not fire"},
        )
        table = _FakeShortcutsTable(return_value=[(candidate, 0.99)])
        d, _, _ = _make_real_dispatcher(
            tmp_path, table=table, enabled=False,
        )
        cpath = tmp_path / "constitution.yaml"
        _write_llm_think_constitution(cpath)

        outcome = _run(d.dispatch(
            instance_id="i1", agent_dna="d" * 12, role="assistant",
            genre="companion", session_id="s1",
            constitution_path=cpath,
            tool_name="llm_think", tool_version="1",
            args={"prompt": "hi", "task_kind": "conversation"},
            provider=_ProviderWithEmbed(),
        ))
        assert isinstance(outcome, DispatchSucceeded)
        assert outcome.result.output["model"] != "shortcut"
        # search was never called (master switch off short-circuits
        # before embed_situation)
        assert table.search_calls == []
