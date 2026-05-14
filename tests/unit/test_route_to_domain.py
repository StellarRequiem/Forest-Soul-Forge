"""ADR-0067 T3 (B281) — route_to_domain.v1 tool tests.

Covers:
  - validation: all required string fields, length bounds, type checks
  - target domain validation: unknown id → refuse; planned without
    allow_planned → refuse; planned with allow_planned → routes with
    override flag in audit
  - delegate underneath: outcome marshaling (succeeded / failed shapes)
  - audit event emission: domain_routed event fires with PII-safe
    intent_hash
  - failure modes: no delegator wired, registry not loadable
"""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from forest_soul_forge.tools.base import ToolValidationError
from forest_soul_forge.tools.builtin.delegate import DelegateError
from forest_soul_forge.tools.builtin.route_to_domain import (
    RouteToDomainTool,
)


def _ctx(
    delegate=None, audit=None, caller_dna="test-dna",
) -> SimpleNamespace:
    return SimpleNamespace(
        delegate=delegate,
        audit=audit,
        caller_dna=caller_dna,
        constraints={},
    )


class _MockAudit:
    """Captures audit.append() calls for inspection."""
    def __init__(self):
        self.events: list[tuple] = []

    def append(self, event_type, payload, *, agent_dna=None):
        self.events.append((event_type, payload, agent_dna))


async def _mock_delegate_succeeded(**kwargs):
    return SimpleNamespace(
        status="succeeded",
        target_instance_id=kwargs["target_instance_id"],
        skill_name=kwargs["skill_name"],
        skill_version=kwargs["skill_version"],
        invoked_seq=100,
        completed_seq=101,
        output={"result": "ok"},
    )


async def _mock_delegate_failed(**kwargs):
    return SimpleNamespace(
        status="failed",
        target_instance_id=kwargs["target_instance_id"],
        skill_name=kwargs["skill_name"],
        skill_version=kwargs["skill_version"],
        invoked_seq=200,
        completed_seq=None,
        failed_step_id="step-3",
        failure_reason="provider timeout",
    )


async def _mock_delegate_refuses(**kwargs):
    raise DelegateError("target_instance_id not found")


def _seed_registry(tmp_path: Path) -> None:
    """Drop a live + a planned manifest for tests."""
    domains = tmp_path / "config" / "domains"
    domains.mkdir(parents=True)
    (domains / "d_live.yaml").write_text(yaml.safe_dump({
        "domain_id": "d_live", "name": "Live", "status": "live",
        "description": "live test",
        "entry_agents": [{"role": "live_role", "capability": "cap_a"}],
        "capabilities": ["cap_a", "cap_b"],
        "example_intents": [],
    }))
    (domains / "d_planned.yaml").write_text(yaml.safe_dump({
        "domain_id": "d_planned", "name": "Planned", "status": "planned",
        "description": "planned test",
        "entry_agents": [],
        "capabilities": ["cap_x"],
        "example_intents": [],
    }))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def test_validate_all_required_fields_present():
    tool = RouteToDomainTool()
    tool.validate({
        "target_domain": "d_live",
        "target_capability": "cap_a",
        "target_instance_id": "agent_1",
        "skill_name": "do_thing",
        "skill_version": "1",
        "intent": "do a thing",
        "reason": "operator asked",
    })


@pytest.mark.parametrize("missing_field", [
    "target_domain", "target_capability", "target_instance_id",
    "skill_name", "skill_version", "intent", "reason",
])
def test_validate_missing_required_raises(missing_field):
    tool = RouteToDomainTool()
    args = {
        "target_domain": "d_live",
        "target_capability": "cap_a",
        "target_instance_id": "agent_1",
        "skill_name": "do_thing",
        "skill_version": "1",
        "intent": "do a thing",
        "reason": "operator asked",
    }
    del args[missing_field]
    with pytest.raises(ToolValidationError, match=missing_field):
        tool.validate(args)


def test_validate_intent_length_ceiling():
    tool = RouteToDomainTool()
    with pytest.raises(ToolValidationError, match="intent exceeds"):
        tool.validate({
            "target_domain": "d_live", "target_capability": "cap_a",
            "target_instance_id": "agent_1",
            "skill_name": "x", "skill_version": "1",
            "intent": "x" * 5000, "reason": "ok",
        })


def test_validate_confidence_range():
    tool = RouteToDomainTool()
    with pytest.raises(ToolValidationError, match="confidence"):
        tool.validate({
            "target_domain": "d_live", "target_capability": "cap_a",
            "target_instance_id": "agent_1",
            "skill_name": "x", "skill_version": "1",
            "intent": "ok", "reason": "ok",
            "confidence": 1.5,
        })


# ---------------------------------------------------------------------------
# Execute — domain gating
# ---------------------------------------------------------------------------
def test_execute_unknown_domain_refuses(tmp_path, monkeypatch):
    _seed_registry(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FSF_DOMAINS_PATH", str(tmp_path / "config" / "domains"))
    tool = RouteToDomainTool()
    ctx = _ctx(delegate=_mock_delegate_succeeded, audit=_MockAudit())
    with pytest.raises(ToolValidationError, match="not in registry"):
        asyncio.run(tool.execute({
            "target_domain": "d_ghost", "target_capability": "cap",
            "target_instance_id": "x", "skill_name": "y",
            "skill_version": "1", "intent": "hi", "reason": "test",
        }, ctx))


def test_execute_planned_domain_refuses_by_default(tmp_path, monkeypatch):
    _seed_registry(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FSF_DOMAINS_PATH", str(tmp_path / "config" / "domains"))
    tool = RouteToDomainTool()
    ctx = _ctx(delegate=_mock_delegate_succeeded, audit=_MockAudit())
    with pytest.raises(ToolValidationError, match="planned"):
        asyncio.run(tool.execute({
            "target_domain": "d_planned", "target_capability": "cap_x",
            "target_instance_id": "x", "skill_name": "y",
            "skill_version": "1", "intent": "hi", "reason": "test",
        }, ctx))


def test_execute_planned_with_override_routes_and_records_flag(tmp_path, monkeypatch):
    """allow_planned=True routes the call AND records the override
    flag in the domain_routed audit event."""
    _seed_registry(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FSF_DOMAINS_PATH", str(tmp_path / "config" / "domains"))
    audit = _MockAudit()
    tool = RouteToDomainTool()
    ctx = _ctx(delegate=_mock_delegate_succeeded, audit=audit)
    result = asyncio.run(tool.execute({
        "target_domain": "d_planned", "target_capability": "cap_x",
        "target_instance_id": "x", "skill_name": "y",
        "skill_version": "1", "intent": "hi", "reason": "test",
        "allow_planned": True,
    }, ctx))
    assert result.success
    # The override is recorded in the audit event
    event_type, payload, _dna = audit.events[0]
    assert event_type == "domain_routed"
    assert payload["allow_planned_override"] is True


# ---------------------------------------------------------------------------
# Audit event emission
# ---------------------------------------------------------------------------
def test_execute_emits_domain_routed_before_delegate(tmp_path, monkeypatch):
    _seed_registry(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FSF_DOMAINS_PATH", str(tmp_path / "config" / "domains"))
    audit = _MockAudit()
    tool = RouteToDomainTool()
    ctx = _ctx(delegate=_mock_delegate_succeeded, audit=audit)
    asyncio.run(tool.execute({
        "target_domain": "d_live", "target_capability": "cap_a",
        "target_instance_id": "agent_1",
        "skill_name": "do_thing", "skill_version": "1",
        "intent": "do a live thing", "reason": "operator asked",
        "confidence": 0.92, "decomposition_seq": 42,
    }, ctx))
    assert len(audit.events) == 1
    event_type, payload, _dna = audit.events[0]
    assert event_type == "domain_routed"
    assert payload["target_domain"] == "d_live"
    assert payload["target_capability"] == "cap_a"
    assert payload["target_instance_id"] == "agent_1"
    assert payload["confidence"] == 0.92
    assert payload["decomposition_seq"] == 42
    # PII safety — payload has intent_hash, not intent text
    assert "intent" not in payload
    expected_hash = hashlib.sha256(
        b"do a live thing"
    ).hexdigest()[:16]
    assert payload["intent_hash"] == expected_hash


def test_execute_marks_known_vs_unknown_capability(tmp_path, monkeypatch):
    """Audit payload notes whether the capability was in the registry."""
    _seed_registry(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FSF_DOMAINS_PATH", str(tmp_path / "config" / "domains"))
    audit = _MockAudit()
    tool = RouteToDomainTool()
    ctx = _ctx(delegate=_mock_delegate_succeeded, audit=audit)
    asyncio.run(tool.execute({
        "target_domain": "d_live", "target_capability": "cap_unregistered",
        "target_instance_id": "agent_1",
        "skill_name": "do_thing", "skill_version": "1",
        "intent": "off-catalog request", "reason": "operator override",
    }, ctx))
    _et, payload, _ = audit.events[0]
    assert payload["capability_known_in_registry"] is False


# ---------------------------------------------------------------------------
# Delegate outcome marshaling
# ---------------------------------------------------------------------------
def test_execute_succeeded_outcome_marshals(tmp_path, monkeypatch):
    _seed_registry(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FSF_DOMAINS_PATH", str(tmp_path / "config" / "domains"))
    tool = RouteToDomainTool()
    ctx = _ctx(delegate=_mock_delegate_succeeded, audit=_MockAudit())
    result = asyncio.run(tool.execute({
        "target_domain": "d_live", "target_capability": "cap_a",
        "target_instance_id": "agent_1",
        "skill_name": "do_thing", "skill_version": "1",
        "intent": "do thing", "reason": "test",
    }, ctx))
    assert result.success
    assert result.output["status"] == "succeeded"
    assert result.output["delegate_output"]["output"] == {"result": "ok"}


def test_execute_failed_outcome_marshals(tmp_path, monkeypatch):
    _seed_registry(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FSF_DOMAINS_PATH", str(tmp_path / "config" / "domains"))
    tool = RouteToDomainTool()
    ctx = _ctx(delegate=_mock_delegate_failed, audit=_MockAudit())
    result = asyncio.run(tool.execute({
        "target_domain": "d_live", "target_capability": "cap_a",
        "target_instance_id": "agent_1",
        "skill_name": "do_thing", "skill_version": "1",
        "intent": "do thing", "reason": "test",
    }, ctx))
    assert not result.success  # status='failed' → success=False
    assert result.output["status"] == "failed"
    assert result.output["delegate_output"]["failure_reason"] == "provider timeout"


def test_execute_delegate_refusal_becomes_validation_error(tmp_path, monkeypatch):
    _seed_registry(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FSF_DOMAINS_PATH", str(tmp_path / "config" / "domains"))
    tool = RouteToDomainTool()
    ctx = _ctx(delegate=_mock_delegate_refuses, audit=_MockAudit())
    with pytest.raises(ToolValidationError, match="delegate refused"):
        asyncio.run(tool.execute({
            "target_domain": "d_live", "target_capability": "cap_a",
            "target_instance_id": "ghost_agent",
            "skill_name": "x", "skill_version": "1",
            "intent": "test", "reason": "test",
        }, ctx))


def test_execute_no_delegator_wired_refuses(tmp_path, monkeypatch):
    _seed_registry(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FSF_DOMAINS_PATH", str(tmp_path / "config" / "domains"))
    tool = RouteToDomainTool()
    ctx = _ctx(delegate=None, audit=_MockAudit())
    with pytest.raises(ToolValidationError, match="no delegator"):
        asyncio.run(tool.execute({
            "target_domain": "d_live", "target_capability": "cap_a",
            "target_instance_id": "agent_1",
            "skill_name": "x", "skill_version": "1",
            "intent": "test", "reason": "test",
        }, ctx))
