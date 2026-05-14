"""ADR-0067 T2 (B280) — decompose_intent.v1 tool tests.

Covers:
  - validation: utterance present + bounded length + threshold range
  - response parsing: strict JSON / markdown-wrapped JSON / extracted
    from prose / bare array / parse-failure fallback
  - status classification: routable / ambiguous / no_match / planned_domain
  - audit payload contents (hash, not raw utterance)
  - confidence threshold flow

Uses a mock provider for hermetic tests — no Ollama dependency.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolValidationError,
)
from forest_soul_forge.tools.builtin.decompose_intent import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    DecomposeIntentTool,
    _parse_decomposition_response,
)


# ---------------------------------------------------------------------------
# Mock provider for hermetic tests
# ---------------------------------------------------------------------------


class _MockProvider:
    """Returns a canned response. Mimics the provider.complete async API."""

    def __init__(self, response_text: str, model: str = "mock-model:test"):
        self.response_text = response_text
        self.model = model
        self.calls: list[dict] = []

    async def complete(self, prompt, *, task_kind, system, max_tokens, **kwargs):
        self.calls.append({
            "prompt": prompt, "task_kind": task_kind,
            "system": system, "max_tokens": max_tokens, "kwargs": kwargs,
        })
        return SimpleNamespace(text=self.response_text, model=self.model)


def _ctx(provider) -> ToolContext:
    """Build a minimal ToolContext for the tool's execute path."""
    return SimpleNamespace(
        provider=provider,
        constraints={},
        master_key=None,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def test_validate_requires_utterance():
    tool = DecomposeIntentTool()
    with pytest.raises(ToolValidationError, match="utterance"):
        tool.validate({})


def test_validate_utterance_must_be_string():
    tool = DecomposeIntentTool()
    with pytest.raises(ToolValidationError, match="string"):
        tool.validate({"utterance": 42})


def test_validate_utterance_length_floor():
    tool = DecomposeIntentTool()
    with pytest.raises(ToolValidationError, match="at least"):
        tool.validate({"utterance": "a"})


def test_validate_utterance_length_ceiling():
    tool = DecomposeIntentTool()
    with pytest.raises(ToolValidationError, match="too long"):
        tool.validate({"utterance": "x" * 5000})


def test_validate_confidence_threshold_range():
    tool = DecomposeIntentTool()
    with pytest.raises(ToolValidationError, match="confidence_threshold"):
        tool.validate({"utterance": "hello", "confidence_threshold": 1.5})


def test_validate_accepts_good_args():
    tool = DecomposeIntentTool()
    tool.validate({"utterance": "remind me to call Mom"})
    tool.validate({
        "utterance": "remind me to call Mom",
        "confidence_threshold": 0.8,
    })


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------
def test_parse_strict_json_object():
    raw = json.dumps({
        "subintents": [
            {"intent": "x", "domain": "d2_daily_life_os",
             "capability": "reminder", "confidence": 0.9},
        ],
    })
    out = _parse_decomposition_response(raw)
    assert len(out) == 1
    assert out[0]["domain"] == "d2_daily_life_os"
    assert out[0]["confidence"] == 0.9


def test_parse_markdown_fenced_json():
    raw = "```json\n" + json.dumps({"subintents": [
        {"intent": "y", "domain": "d3_local_soc",
         "capability": "incident_summary", "confidence": 0.7},
    ]}) + "\n```"
    out = _parse_decomposition_response(raw)
    assert out[0]["domain"] == "d3_local_soc"


def test_parse_json_embedded_in_prose():
    raw = (
        "Here's the decomposition: " +
        json.dumps({"subintents": [
            {"intent": "z", "domain": "d1_knowledge_forge",
             "capability": "knowledge_curation", "confidence": 0.85},
        ]}) +
        " Hope that helps."
    )
    out = _parse_decomposition_response(raw)
    assert out[0]["domain"] == "d1_knowledge_forge"


def test_parse_bare_array_form():
    raw = json.dumps([
        {"intent": "a", "domain": "d2_daily_life_os",
         "capability": "reminder", "confidence": 0.5},
    ])
    out = _parse_decomposition_response(raw)
    assert len(out) == 1


def test_parse_empty_string_returns_failure_subintent():
    out = _parse_decomposition_response("")
    assert len(out) == 1
    assert out[0]["domain"] == "d_unknown"
    assert out[0]["confidence"] == 0.0


def test_parse_garbage_returns_failure_subintent():
    out = _parse_decomposition_response("just some prose, no JSON anywhere")
    assert out[0]["domain"] == "d_unknown"


def test_parse_pads_missing_fields():
    raw = json.dumps({"subintents": [{"intent": "x"}]})
    out = _parse_decomposition_response(raw)
    assert out[0]["domain"] == "d_unknown"
    assert out[0]["capability"] == "unknown"
    assert out[0]["confidence"] == 0.0


# ---------------------------------------------------------------------------
# Full execute path with mock provider + temp registry
# ---------------------------------------------------------------------------


def _seed_minimal_registry(tmp_path: Path) -> Path:
    """Drop two seed manifests so load_domain_registry() works
    inside the test."""
    config_domains = tmp_path / "config" / "domains"
    config_domains.mkdir(parents=True)
    (config_domains / "d_test_live.yaml").write_text(yaml.safe_dump({
        "domain_id": "d_test_live",
        "name": "Live Test",
        "status": "live",
        "description": "live test domain",
        "entry_agents": [
            {"role": "live_role", "capability": "live_cap"},
        ],
        "capabilities": ["live_cap"],
        "example_intents": ["do the live thing"],
    }))
    (config_domains / "d_test_planned.yaml").write_text(yaml.safe_dump({
        "domain_id": "d_test_planned",
        "name": "Planned Test",
        "status": "planned",
        "description": "planned test domain",
        "entry_agents": [],
        "capabilities": ["planned_cap"],
        "example_intents": ["do the planned thing"],
    }))
    return config_domains


def test_execute_classifies_routable(tmp_path, monkeypatch):
    """High-confidence sub-intent routing to a live domain →
    status='routable'."""
    _seed_minimal_registry(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FSF_DOMAINS_PATH", str(tmp_path / "config" / "domains"))

    provider = _MockProvider(json.dumps({
        "subintents": [
            {"intent": "live task", "domain": "d_test_live",
             "capability": "live_cap", "confidence": 0.92},
        ],
    }))
    tool = DecomposeIntentTool()
    result = asyncio.run(tool.execute(
        {"utterance": "do the live thing"}, _ctx(provider),
    ))
    assert result.success
    subs = result.output["subintents"]
    assert subs[0]["status"] == "routable"
    assert result.output["ambiguity_count"] == 0


def test_execute_classifies_ambiguous_below_threshold(tmp_path, monkeypatch):
    """Confidence below threshold → status='ambiguous'."""
    _seed_minimal_registry(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FSF_DOMAINS_PATH", str(tmp_path / "config" / "domains"))

    provider = _MockProvider(json.dumps({
        "subintents": [
            {"intent": "fuzzy task", "domain": "d_test_live",
             "capability": "live_cap", "confidence": 0.3},
        ],
    }))
    tool = DecomposeIntentTool()
    result = asyncio.run(tool.execute(
        {"utterance": "do something maybe"}, _ctx(provider),
    ))
    subs = result.output["subintents"]
    assert subs[0]["status"] == "ambiguous"
    assert result.output["ambiguity_count"] == 1


def test_execute_classifies_no_match_for_unknown_domain(tmp_path, monkeypatch):
    """LLM-emitted domain not in registry → status='no_match'."""
    _seed_minimal_registry(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FSF_DOMAINS_PATH", str(tmp_path / "config" / "domains"))

    provider = _MockProvider(json.dumps({
        "subintents": [
            {"intent": "ghost task", "domain": "d_not_real",
             "capability": "anything", "confidence": 0.95},
        ],
    }))
    tool = DecomposeIntentTool()
    result = asyncio.run(tool.execute(
        {"utterance": "do the impossible"}, _ctx(provider),
    ))
    assert result.output["subintents"][0]["status"] == "no_match"


def test_execute_classifies_planned_domain(tmp_path, monkeypatch):
    """Domain in registry but status=planned → status='planned_domain'."""
    _seed_minimal_registry(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FSF_DOMAINS_PATH", str(tmp_path / "config" / "domains"))

    provider = _MockProvider(json.dumps({
        "subintents": [
            {"intent": "planned task", "domain": "d_test_planned",
             "capability": "planned_cap", "confidence": 0.9},
        ],
    }))
    tool = DecomposeIntentTool()
    result = asyncio.run(tool.execute(
        {"utterance": "do the planned thing"}, _ctx(provider),
    ))
    assert result.output["subintents"][0]["status"] == "planned_domain"


def test_execute_audit_payload_is_pii_safe(tmp_path, monkeypatch):
    """Audit payload contains hash, not raw utterance."""
    _seed_minimal_registry(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FSF_DOMAINS_PATH", str(tmp_path / "config" / "domains"))

    provider = _MockProvider(json.dumps({"subintents": []}))
    tool = DecomposeIntentTool()
    utterance = "remind me about my medical appointment"
    result = asyncio.run(tool.execute(
        {"utterance": utterance}, _ctx(provider),
    ))
    assert "utterance" not in result.audit_payload
    assert "utterance_hash" in result.audit_payload
    # Hash is deterministic + short (16 chars per the impl).
    assert len(result.audit_payload["utterance_hash"]) == 16


def test_execute_refuses_without_provider(tmp_path, monkeypatch):
    """No LLM provider wired → ToolValidationError, not a crash."""
    _seed_minimal_registry(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FSF_DOMAINS_PATH", str(tmp_path / "config" / "domains"))

    ctx = SimpleNamespace(provider=None, constraints={}, master_key=None)
    tool = DecomposeIntentTool()
    with pytest.raises(ToolValidationError, match="provider"):
        asyncio.run(tool.execute({"utterance": "anything"}, ctx))
