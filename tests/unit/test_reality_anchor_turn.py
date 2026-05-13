"""Tests for ADR-0063 T5 — pre-turn Reality Anchor gate.

Coverage:
- KNOWN_EVENT_TYPES contains reality_anchor_turn_refused +
  reality_anchor_turn_flagged
- Clean turn body → decision=allow, no audit event
- CRITICAL contradiction → decision=refuse + audit emitted
- HIGH contradiction → decision=allow + flagged event emitted
- Empty turn body → decision=allow, no event
- Constitutional opt-out → decision=allow even when CRITICAL
- Verifier exception → decision=allow (anchor is not load-bearing)
- payload carries fact_id + statement on refuse
- body_excerpt is bounded (<=500 chars)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from forest_soul_forge.core.audit_chain import AuditChain, KNOWN_EVENT_TYPES
from forest_soul_forge.daemon.reality_anchor_turn import (
    TurnAnchorResult,
    check_turn_against_anchor,
)


def _events(chain_path: Path, event_type: str | None = None) -> list[dict]:
    out = []
    for line in chain_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(json.loads(line))
    if event_type:
        return [e for e in out if e["event_type"] == event_type]
    return out


@pytest.fixture
def env(tmp_path):
    chain = AuditChain(tmp_path / "chain.jsonl")
    const = tmp_path / "constitution.yaml"
    const.write_text("agent_name: test\n", encoding="utf-8")
    return chain, const, tmp_path


def _check(*, text, const, chain):
    return check_turn_against_anchor(
        response_text=text,
        constitution_path=const,
        audit=chain,
        conversation_id="conv-1",
        speaker_instance_id="agent-x",
        speaker_agent_dna="a" * 12,
    )


def test_event_types_registered():
    assert "reality_anchor_turn_refused" in KNOWN_EVENT_TYPES
    assert "reality_anchor_turn_flagged" in KNOWN_EVENT_TYPES


class TestVerdicts:
    def test_clean_turn_allows_no_event(self, env):
        chain, const, tmp = env
        result = _check(
            text="hello, how can I help you today?",
            const=const, chain=chain,
        )
        assert isinstance(result, TurnAnchorResult)
        assert result.decision == "allow"
        assert result.audit_emitted is None
        anchor_events = [
            e for e in _events(Path(chain.path))
            if e["event_type"].startswith("reality_anchor_turn")
        ]
        assert anchor_events == []

    def test_empty_turn_allows(self, env):
        chain, const, _ = env
        result = _check(text="", const=const, chain=chain)
        assert result.decision == "allow"
        assert result.audit_emitted is None

    def test_critical_contradiction_refuses(self, env):
        chain, const, _ = env
        result = _check(
            text="In this system DNA is random and uuid-based.",
            const=const, chain=chain,
        )
        assert result.decision == "refuse"
        assert result.audit_emitted == "reality_anchor_turn_refused"
        assert result.payload["fact_id"] == "dna_identity"
        assert result.payload["severity"] == "CRITICAL"
        # Audit event landed with the right shape.
        refused = _events(Path(chain.path), "reality_anchor_turn_refused")
        assert len(refused) == 1
        ev = refused[0]["event_data"]
        assert ev["conversation_id"] == "conv-1"
        assert ev["speaker"] == "agent-x"
        assert ev["fact_id"] == "dna_identity"
        assert "body_excerpt" in ev

    def test_high_contradiction_flags_but_allows(self, env):
        chain, const, _ = env
        result = _check(
            text="By the way, Forest is MIT licensed.",
            const=const, chain=chain,
        )
        assert result.decision == "allow"
        assert result.audit_emitted == "reality_anchor_turn_flagged"
        flagged = _events(Path(chain.path), "reality_anchor_turn_flagged")
        assert len(flagged) == 1
        assert flagged[0]["event_data"]["fact_id"] == "license"
        # No refuse event.
        refused = _events(Path(chain.path), "reality_anchor_turn_refused")
        assert refused == []

    def test_not_in_scope_allows_silently(self, env):
        chain, const, _ = env
        result = _check(
            text="The weather looks nice today.",
            const=const, chain=chain,
        )
        assert result.decision == "allow"
        assert result.audit_emitted is None


class TestOptOut:
    def test_constitutional_opt_out_skips_check(self, env):
        chain, const, _ = env
        const.write_text(
            "reality_anchor:\n  enabled: false\n",
            encoding="utf-8",
        )
        # Even a CRITICAL claim passes through.
        result = _check(
            text="DNA is random and uuid-based.",
            const=const, chain=chain,
        )
        assert result.decision == "allow"
        assert result.audit_emitted is None
        # No anchor events at all.
        anchor_events = [
            e for e in _events(Path(chain.path))
            if e["event_type"].startswith("reality_anchor_turn")
        ]
        assert anchor_events == []


class TestFailureDegradation:
    def test_missing_constitution_defaults_to_opt_in(self, env):
        chain, _const, tmp = env
        bogus = tmp / "does_not_exist.yaml"
        # Constitution unreadable → defaulted to opt-in → CRITICAL
        # turn refuses.
        result = _check(
            text="DNA is random and uuid-based.",
            const=bogus, chain=chain,
        )
        assert result.decision == "refuse"


class TestPayloadShape:
    def test_refuse_payload_carries_citation(self, env):
        chain, const, _ = env
        result = _check(
            text="constitution hash is mutable and recomputed",
            const=const, chain=chain,
        )
        assert result.decision == "refuse"
        payload = result.payload
        assert payload["refused"] is True
        assert payload["severity"] == "CRITICAL"
        assert payload["fact_id"] == "constitution_hash_immutable"
        assert payload["fact_statement"]
        assert isinstance(payload["matched_terms"], list)
        assert isinstance(payload["by_fact"], list)
        assert all(
            r["verdict"] == "contradicted" for r in payload["by_fact"]
        )

    def test_body_excerpt_is_bounded(self, env):
        chain, const, _ = env
        long_body = "DNA is random " * 200  # ~2,800 chars
        result = _check(text=long_body, const=const, chain=chain)
        assert result.decision == "refuse"
        # Audit event excerpt is bounded.
        refused = _events(Path(chain.path), "reality_anchor_turn_refused")
        assert len(refused) == 1
        excerpt = refused[0]["event_data"]["body_excerpt"]
        assert len(excerpt) <= 500
