"""B380 (ADR-0080 T1) — /agents/{id}/capability-tree endpoint.

Tests the composition rules + response shape. Uses an in-process
FastAPI TestClient with a stub registry + tool registry + skill
catalog wired through app.state — same pattern as the existing
agents router tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from forest_soul_forge.daemon.routers.capability_tree import router


@dataclass
class _StubAgent:
    """Mimics the registry's AgentRow shape closely enough for the
    endpoint's getattr access pattern."""
    instance_id: str
    role: str
    agent_name: str
    posture: str
    constitution_path: str
    dna: str = "deadbeef"
    status: str = "active"
    genre: str = "guardian"


class _StubRegistry:
    def __init__(self, agent: _StubAgent | None = None) -> None:
        self.agent = agent

    def get_agent(self, instance_id: str) -> _StubAgent:
        if self.agent is None or self.agent.instance_id != instance_id:
            from forest_soul_forge.registry.registry import UnknownAgentError
            raise UnknownAgentError(instance_id)
        return self.agent


class _StubToolRegistry:
    """Registry that reports has(name, version) based on a set."""
    def __init__(self, registered_keys: set[str]) -> None:
        self.registered_keys = registered_keys

    def has(self, name: str, version: str) -> bool:
        return f"{name}.v{version}" in self.registered_keys


@dataclass
class _StubSkillDef:
    name: str
    version: str
    description: str
    requires: list[str] = field(default_factory=list)


@dataclass
class _StubSkillCatalog:
    skills: dict[str, _StubSkillDef]


class _StubGenre:
    name = "guardian"


class _StubGenres:
    def genre_for(self, role: str):
        return _StubGenre()


def _make_constitution(path: Path, tools: list[dict[str, Any]]) -> None:
    """Write a minimal constitution YAML the endpoint can parse."""
    doc = {
        "schema_version": 1,
        "constitution_hash": "0" * 64,
        "generated_at": "2026-05-18T00:00:00Z",
        "agent": {"role": "guardian", "agent_name": "Test"},
        "tools": tools,
    }
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")


def _build_app(
    agent: _StubAgent | None,
    registered_keys: set[str],
    skills: dict[str, _StubSkillDef] | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.tool_registry = _StubToolRegistry(registered_keys)
    app.state.skill_catalog = _StubSkillCatalog(skills or {})
    app.state.genre_engine = _StubGenres()
    # Wire dependency overrides for the registry + tool registry.
    from forest_soul_forge.daemon.deps import (
        get_registry as real_get_registry,
        get_tool_registry as real_get_tool_registry,
    )
    app.dependency_overrides[real_get_registry] = lambda: _StubRegistry(agent)
    app.dependency_overrides[real_get_tool_registry] = lambda: app.state.tool_registry
    return app


# ---- 404 path ------------------------------------------------------------

def test_returns_404_for_unknown_agent(tmp_path):
    app = _build_app(agent=None, registered_keys=set())
    client = TestClient(app)
    r = client.get("/agents/unknown_id/capability-tree")
    assert r.status_code == 404


# ---- happy path: all tools live -----------------------------------------

def test_all_live_when_constitution_tools_are_registered(tmp_path):
    const_path = tmp_path / "agent.constitution.yaml"
    _make_constitution(const_path, [
        {"name": "llm_think", "version": "1", "side_effects": "read_only"},
        {"name": "memory_recall", "version": "1", "side_effects": "read_only"},
    ])
    agent = _StubAgent(
        instance_id="guardian_x", role="guardian_role",
        agent_name="Test-G1", posture="green",
        constitution_path=str(const_path),
    )
    app = _build_app(
        agent=agent,
        registered_keys={"llm_think.v1", "memory_recall.v1"},
    )
    r = TestClient(app).get(f"/agents/{agent.instance_id}/capability-tree")
    assert r.status_code == 200
    body = r.json()
    assert body["schema_version"] == 1
    assert body["agent"]["instance_id"] == "guardian_x"
    assert body["agent"]["posture"] == "green"
    tools = body["tree"]["tools"]
    assert len(tools) == 2
    for t in tools:
        assert t["status"] == "live"
        assert t["binding"] == "hard_wired"
    assert body["summary"]["tools_live"] == 2
    assert body["summary"]["tools_broken"] == 0


# ---- mixed: one tool not registered -------------------------------------

def test_unregistered_tool_is_broken(tmp_path):
    const_path = tmp_path / "agent.constitution.yaml"
    _make_constitution(const_path, [
        {"name": "llm_think", "version": "1", "side_effects": "read_only"},
        {"name": "ghost_tool", "version": "1", "side_effects": "read_only"},
    ])
    agent = _StubAgent(
        instance_id="g2", role="guardian_role", agent_name="Test-G2",
        posture="green", constitution_path=str(const_path),
    )
    app = _build_app(
        agent=agent,
        registered_keys={"llm_think.v1"},  # ghost_tool absent
    )
    r = TestClient(app).get(f"/agents/{agent.instance_id}/capability-tree")
    assert r.status_code == 200
    body = r.json()
    tools = {t["key"]: t for t in body["tree"]["tools"]}
    assert tools["llm_think.v1"]["status"] == "live"
    assert tools["ghost_tool.v1"]["status"] == "broken"
    assert "missing from /tools/registered" in tools["ghost_tool.v1"]["reason"]


# ---- missing constitution path falls back cleanly -----------------------

def test_missing_constitution_path_yields_empty_tools(tmp_path):
    agent = _StubAgent(
        instance_id="g3", role="r", agent_name="g3", posture="yellow",
        constitution_path="",  # empty path
    )
    app = _build_app(agent=agent, registered_keys=set())
    r = TestClient(app).get(f"/agents/{agent.instance_id}/capability-tree")
    assert r.status_code == 200
    body = r.json()
    assert body["tree"]["tools"] == []
    assert body["summary"]["tools_total"] == 0


# ---- skill composition: missing tools flagged ----------------------------

def test_skill_with_missing_required_tool_is_broken(tmp_path):
    """Skill requires text_summarize.v1, but agent's constitution
    only has llm_think.v1 -> skill is broken with missing_tools=[
    text_summarize.v1]."""
    const_path = tmp_path / "agent.constitution.yaml"
    _make_constitution(const_path, [
        {"name": "llm_think", "version": "1", "side_effects": "read_only"},
    ])
    agent = _StubAgent(
        instance_id="g4", role="r", agent_name="g4", posture="green",
        constitution_path=str(const_path),
    )
    skill = _StubSkillDef(
        name="summarize_audit",
        version="1",
        description="test skill",
        requires=["llm_think.v1", "text_summarize.v1"],
    )
    app = _build_app(
        agent=agent,
        registered_keys={"llm_think.v1", "text_summarize.v1"},
        skills={"summarize_audit.v1": skill},
    )
    r = TestClient(app).get(f"/agents/{agent.instance_id}/capability-tree")
    assert r.status_code == 200
    body = r.json()
    assert len(body["tree"]["skills"]) == 1
    s = body["tree"]["skills"][0]
    assert s["name"] == "summarize_audit"
    assert s["status"] == "broken"
    assert s["missing_tools"] == ["text_summarize.v1"]
    assert s["binding"] == "operator_toggleable"


def test_skill_with_all_required_tools_is_live(tmp_path):
    const_path = tmp_path / "agent.constitution.yaml"
    _make_constitution(const_path, [
        {"name": "llm_think", "version": "1", "side_effects": "read_only"},
        {"name": "text_summarize", "version": "1", "side_effects": "read_only"},
    ])
    agent = _StubAgent(
        instance_id="g5", role="r", agent_name="g5", posture="green",
        constitution_path=str(const_path),
    )
    skill = _StubSkillDef(
        name="summarize_audit",
        version="1",
        description="test",
        requires=["llm_think.v1", "text_summarize.v1"],
    )
    app = _build_app(
        agent=agent,
        registered_keys={"llm_think.v1", "text_summarize.v1"},
        skills={"summarize_audit.v1": skill},
    )
    body = TestClient(app).get(
        f"/agents/{agent.instance_id}/capability-tree"
    ).json()
    s = body["tree"]["skills"][0]
    assert s["status"] == "live"
    assert s["missing_tools"] == []


# ---- mcp_plugins placeholder ---------------------------------------------

def test_mcp_plugins_empty_today(tmp_path):
    """T1 ships mcp_plugins as an empty placeholder. T2 frontend
    will decide whether to surface; future ADR-0043 wiring populates
    it."""
    const_path = tmp_path / "agent.constitution.yaml"
    _make_constitution(const_path, [])
    agent = _StubAgent(
        instance_id="g6", role="r", agent_name="g6", posture="green",
        constitution_path=str(const_path),
    )
    app = _build_app(agent=agent, registered_keys=set())
    body = TestClient(app).get(
        f"/agents/{agent.instance_id}/capability-tree"
    ).json()
    assert body["tree"]["mcp_plugins"] == []
    assert body["summary"]["mcp_plugins_total"] == 0
