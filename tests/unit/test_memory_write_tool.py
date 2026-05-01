"""Unit tests for memory_write.v1 — Round A2."""
from __future__ import annotations

import asyncio

import pytest

from forest_soul_forge.core.memory import Memory
from forest_soul_forge.registry import Registry
from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.memory_write import MemoryWriteTool
from tests.unit.conftest import seed_stub_agent


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def env(tmp_path):
    reg = Registry.bootstrap(tmp_path / "reg.sqlite")
    seed_stub_agent(reg, "agent_a")  # Phase A FK-seeding
    memory = Memory(conn=reg._conn)  # noqa: SLF001
    ctx = ToolContext(
        instance_id="agent_a", agent_dna="d" * 12,
        role="researcher", genre="researcher", session_id="s1",
        constraints={}, memory=memory,
    )
    yield {"memory": memory, "ctx": ctx, "registry": reg}
    reg.close()


class TestMemoryWriteValidate:
    def test_minimum_args(self):
        MemoryWriteTool().validate({
            "content": "hello", "layer": "episodic",
        })

    def test_missing_content_rejected(self):
        with pytest.raises(ToolValidationError, match="content"):
            MemoryWriteTool().validate({"layer": "episodic"})

    def test_empty_content_rejected(self):
        with pytest.raises(ToolValidationError):
            MemoryWriteTool().validate({"content": "   ", "layer": "episodic"})

    def test_huge_content_rejected(self):
        with pytest.raises(ToolValidationError, match="exceeds max"):
            MemoryWriteTool().validate({
                "content": "x" * (8 * 1024 + 1), "layer": "episodic",
            })

    def test_unknown_layer_rejected(self):
        with pytest.raises(ToolValidationError):
            MemoryWriteTool().validate({"content": "x", "layer": "garbage"})

    def test_unknown_scope_rejected(self):
        with pytest.raises(ToolValidationError):
            MemoryWriteTool().validate({
                "content": "x", "layer": "episodic", "scope": "garbage",
            })

    def test_tags_must_be_string_list(self):
        with pytest.raises(ToolValidationError):
            MemoryWriteTool().validate({
                "content": "x", "layer": "episodic", "tags": ["ok", 42],
            })


class TestMemoryWriteExecute:
    def test_writes_and_returns_entry(self, env):
        out = _run(MemoryWriteTool().execute(
            {"content": "first thought", "layer": "episodic"},
            env["ctx"],
        ))
        assert "entry_id" in out.output
        assert out.output["layer"] == "episodic"
        assert out.output["scope"] == "private"
        assert out.output["content_digest"].startswith("sha256:")
        # Memory store has the entry.
        entries = env["memory"].recall(instance_id="agent_a")
        assert [e.content for e in entries] == ["first thought"]

    def test_metadata_includes_tags(self, env):
        out = _run(MemoryWriteTool().execute(
            {"content": "x", "layer": "episodic", "tags": ["red", "blue"]},
            env["ctx"],
        ))
        assert out.metadata["tags"] == ["red", "blue"]

    def test_side_effect_summary_set(self, env):
        out = _run(MemoryWriteTool().execute(
            {"content": "x", "layer": "semantic"}, env["ctx"],
        ))
        assert "memory entry" in (out.side_effect_summary or "")

    def test_companion_widening_refused(self, env):
        # ADR-0027 §5 — companion ceiling is `private`. Writing
        # scope=lineage on a companion-genre agent must refuse.
        env["ctx"] = ToolContext(
            instance_id="agent_a", agent_dna="d" * 12,
            role="operator_companion", genre="companion", session_id="s1",
            constraints={}, memory=env["memory"],
        )
        with pytest.raises(ToolValidationError, match="scope violation"):
            _run(MemoryWriteTool().execute(
                {"content": "therapy notes", "layer": "episodic",
                 "scope": "lineage"},
                env["ctx"],
            ))

    def test_per_agent_isolation(self, env):
        # Writing on agent_a doesn't leak to agent_b.
        _run(MemoryWriteTool().execute(
            {"content": "a's note", "layer": "episodic"}, env["ctx"],
        ))
        ctx_b = ToolContext(
            instance_id="agent_b", agent_dna="e" * 12,
            role="researcher", genre="researcher", session_id="s2",
            constraints={}, memory=env["memory"],
        )
        out_b = env["memory"].recall(instance_id="agent_b")
        assert out_b == []
        out_a = env["memory"].recall(instance_id="agent_a")
        assert len(out_a) == 1


class TestRegistration:
    def test_memory_write_registered_at_lifespan(self):
        from forest_soul_forge.tools import ToolRegistry
        from forest_soul_forge.tools.builtin import register_builtins
        reg = ToolRegistry()
        register_builtins(reg)
        assert reg.has("memory_write", "1")
        assert reg.has("memory_recall", "1")
        assert reg.has("timestamp_window", "1")
