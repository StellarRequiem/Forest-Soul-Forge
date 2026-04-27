"""Unit tests for memory_recall.v1 — Round 3c."""
from __future__ import annotations

import asyncio

import pytest

from forest_soul_forge.core.memory import Memory
from forest_soul_forge.registry import Registry
from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.memory_recall import MemoryRecallTool


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def env(tmp_path):
    """Memory + ToolContext bound to a fresh registry."""
    reg = Registry.bootstrap(tmp_path / "reg.sqlite")
    memory = Memory(conn=reg._conn)  # noqa: SLF001
    ctx = ToolContext(
        instance_id="agent_a", agent_dna="d" * 12,
        role="researcher", genre="researcher", session_id="s1",
        constraints={}, memory=memory,
    )
    yield {"memory": memory, "ctx": ctx, "registry": reg}
    reg.close()


def _seed(memory, instance_id="agent_a"):
    memory.append(
        instance_id=instance_id, agent_dna="d" * 12,
        content="first thought", layer="episodic", tags=("tag-a",),
    )
    memory.append(
        instance_id=instance_id, agent_dna="d" * 12,
        content="semantic fact", layer="semantic",
    )
    memory.append(
        instance_id=instance_id, agent_dna="d" * 12,
        content="another episodic note", layer="episodic", tags=("tag-b",),
    )


class TestMemoryRecallValidate:
    def test_no_args_ok(self):
        MemoryRecallTool().validate({})

    def test_unknown_layer_rejected(self):
        with pytest.raises(ToolValidationError):
            MemoryRecallTool().validate({"layer": "garbage"})

    def test_query_must_be_string(self):
        with pytest.raises(ToolValidationError):
            MemoryRecallTool().validate({"query": 42})

    def test_limit_bounds(self):
        with pytest.raises(ToolValidationError):
            MemoryRecallTool().validate({"limit": 0})
        with pytest.raises(ToolValidationError):
            MemoryRecallTool().validate({"limit": 500})


class TestMemoryRecallExecute:
    def test_returns_all_entries_for_agent(self, env):
        _seed(env["memory"])
        out = _run(MemoryRecallTool().execute({}, env["ctx"]))
        assert out.output["count"] == 3
        # newest first
        assert out.output["entries"][0]["content"] == "another episodic note"

    def test_layer_filter(self, env):
        _seed(env["memory"])
        out = _run(MemoryRecallTool().execute({"layer": "episodic"}, env["ctx"]))
        assert out.output["count"] == 2

    def test_query_substring(self, env):
        _seed(env["memory"])
        out = _run(MemoryRecallTool().execute({"query": "semantic"}, env["ctx"]))
        assert out.output["count"] == 1
        assert "semantic" in out.output["entries"][0]["content"]

    def test_limit_caps_results(self, env):
        for i in range(10):
            env["memory"].append(
                instance_id="agent_a", agent_dna="d" * 12,
                content=f"entry-{i}", layer="episodic",
            )
        out = _run(MemoryRecallTool().execute({"limit": 3}, env["ctx"]))
        assert out.output["count"] == 3

    def test_other_agents_memory_isolated(self, env):
        _seed(env["memory"], instance_id="other_agent")
        # Recall is scoped to ctx.instance_id; other_agent's entries
        # are invisible.
        out = _run(MemoryRecallTool().execute({}, env["ctx"]))
        assert out.output["count"] == 0

    def test_pure_function_no_accounting(self, env):
        _seed(env["memory"])
        out = _run(MemoryRecallTool().execute({}, env["ctx"]))
        assert out.tokens_used is None
        assert out.cost_usd is None
        assert out.side_effect_summary is None

    def test_no_memory_bound_raises(self):
        ctx = ToolContext(
            instance_id="x", agent_dna="0" * 12, role="r", genre=None,
            session_id="s", constraints={},
            # memory deliberately omitted
        )
        with pytest.raises(ToolValidationError, match="no Memory bound"):
            _run(MemoryRecallTool().execute({}, ctx))

    def test_test_fallback_via_constraints(self, tmp_path):
        """Fallback path: tests can pass Memory via constraints dict."""
        reg = Registry.bootstrap(tmp_path / "reg.sqlite")
        memory = Memory(conn=reg._conn)
        memory.append(
            instance_id="x", agent_dna="0" * 12,
            content="hi", layer="episodic",
        )
        ctx = ToolContext(
            instance_id="x", agent_dna="0" * 12, role="r", genre=None,
            session_id="s", constraints={"memory": memory},
            # ctx.memory NOT set; falls back to constraints["memory"]
        )
        out = _run(MemoryRecallTool().execute({}, ctx))
        assert out.output["count"] == 1
        reg.close()


class TestRegistration:
    def test_memory_recall_registered_at_lifespan(self):
        from forest_soul_forge.tools import ToolRegistry
        from forest_soul_forge.tools.builtin import register_builtins
        reg = ToolRegistry()
        register_builtins(reg)
        assert reg.has("memory_recall", "1")
        assert reg.has("timestamp_window", "1")
