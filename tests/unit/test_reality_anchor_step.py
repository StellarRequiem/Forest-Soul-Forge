"""Tests for ADR-0063 T3 — RealityAnchorStep in the governance pipeline.

Coverage:
- KNOWN_EVENT_TYPES registers reality_anchor_refused + reality_anchor_flagged
- Clean claim → GO, no audit event
- CRITICAL contradiction → REFUSE + reality_anchor_refused emitted
- HIGH/MEDIUM/LOW contradiction → GO + reality_anchor_flagged emitted
- not_in_scope claim → GO, no event
- Empty args → GO, no event
- Constitutional opt-out → step is a no-op
- Verifier exception → degrades to GO + flag with reason verifier_raised
- Constitution read error → defaults to opted-IN (gate fires)
- _flatten_args_to_claim recurses one level deep
- _flatten_args_to_claim caps recursion depth
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from forest_soul_forge.core.audit_chain import AuditChain, KNOWN_EVENT_TYPES
from forest_soul_forge.tools.governance_pipeline import (
    DispatchContext,
    RealityAnchorStep,
    _flatten_args_to_claim,
)
from forest_soul_forge.tools.dispatcher import (
    _reality_anchor_verify,
    _reality_anchor_opt_out,
)


def _ctx(args, *, constitution_path: Path) -> DispatchContext:
    return DispatchContext(
        instance_id="t",
        agent_dna="a" * 12,
        role="observer",
        genre="security_low",
        session_id="s",
        constitution_path=constitution_path,
        tool_name="example_tool",
        tool_version="1",
        args=args,
    )


def _events(chain_path: Path, event_type: str | None = None) -> list[dict]:
    events = [
        json.loads(line)
        for line in chain_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if event_type:
        return [e for e in events if e["event_type"] == event_type]
    return events


def test_event_types_registered():
    assert "reality_anchor_refused" in KNOWN_EVENT_TYPES
    assert "reality_anchor_flagged" in KNOWN_EVENT_TYPES


@pytest.fixture
def chain_and_const(tmp_path):
    chain = AuditChain(tmp_path / "chain.jsonl")
    const = tmp_path / "constitution.yaml"
    const.write_text("agent_name: test\n", encoding="utf-8")
    return chain, const


@pytest.fixture
def step(chain_and_const):
    chain, _ = chain_and_const
    return RealityAnchorStep(
        audit=chain,
        verify_claim_fn=_reality_anchor_verify,
        load_constitution_opt_out_fn=_reality_anchor_opt_out,
    )


class TestStepVerdicts:
    def test_clean_claim_returns_go_no_event(self, step, chain_and_const):
        chain, const = chain_and_const
        result = step.evaluate(
            _ctx({"note": "we are using the daemon"}, constitution_path=const),
        )
        assert result.verdict == "GO"
        # No reality_anchor events.
        anchor_events = [
            e for e in _events(Path(chain.path))
            if e["event_type"].startswith("reality_anchor")
        ]
        assert anchor_events == []

    def test_critical_contradiction_refuses_and_emits(
        self, step, chain_and_const,
    ):
        chain, const = chain_and_const
        result = step.evaluate(
            _ctx(
                {"note": "DNA is random and timestamp-based"},
                constitution_path=const,
            ),
        )
        assert result.verdict == "REFUSE"
        assert result.reason == "reality_anchor_contradiction"
        refused = _events(Path(chain.path), "reality_anchor_refused")
        assert len(refused) == 1
        ev = refused[0]["event_data"]
        assert ev["fact_id"] == "dna_identity"
        assert ev["severity"] == "CRITICAL"
        assert ev["tool_key"] == "example_tool.v1"

    def test_high_contradiction_goes_and_flags(self, step, chain_and_const):
        chain, const = chain_and_const
        result = step.evaluate(
            _ctx(
                {"note": "Forest is licensed under MIT"},
                constitution_path=const,
            ),
        )
        assert result.verdict == "GO"
        flagged = _events(Path(chain.path), "reality_anchor_flagged")
        assert len(flagged) == 1
        assert flagged[0]["event_data"]["fact_id"] == "license"
        assert flagged[0]["event_data"]["severity"] == "HIGH"
        # No refuse event.
        refused = _events(Path(chain.path), "reality_anchor_refused")
        assert refused == []

    def test_not_in_scope_no_event(self, step, chain_and_const):
        chain, const = chain_and_const
        result = step.evaluate(
            _ctx(
                {"note": "the cat sat on the mat"},
                constitution_path=const,
            ),
        )
        assert result.verdict == "GO"
        anchor_events = [
            e for e in _events(Path(chain.path))
            if e["event_type"].startswith("reality_anchor")
        ]
        assert anchor_events == []

    def test_empty_args_no_event(self, step, chain_and_const):
        chain, const = chain_and_const
        result = step.evaluate(_ctx({}, constitution_path=const))
        assert result.verdict == "GO"
        anchor_events = [
            e for e in _events(Path(chain.path))
            if e["event_type"].startswith("reality_anchor")
        ]
        assert anchor_events == []


class TestOptOut:
    def test_constitutional_opt_out_skips_step(self, step, chain_and_const):
        chain, const = chain_and_const
        const.write_text(
            "reality_anchor:\n  enabled: false\n",
            encoding="utf-8",
        )
        # Even a CRITICAL contradiction passes through.
        result = step.evaluate(
            _ctx(
                {"note": "DNA is random and timestamp-based"},
                constitution_path=const,
            ),
        )
        assert result.verdict == "GO"
        anchor_events = [
            e for e in _events(Path(chain.path))
            if e["event_type"].startswith("reality_anchor")
        ]
        assert anchor_events == []

    def test_missing_constitution_defaults_to_opt_in(self, step, tmp_path):
        chain = AuditChain(tmp_path / "chain.jsonl")
        bogus = tmp_path / "does_not_exist.yaml"
        s = RealityAnchorStep(
            audit=chain,
            verify_claim_fn=_reality_anchor_verify,
            load_constitution_opt_out_fn=_reality_anchor_opt_out,
        )
        result = s.evaluate(
            _ctx(
                {"note": "DNA is random"},
                constitution_path=bogus,
            ),
        )
        # Constitution unreadable → defaulted to opt-in → CRITICAL
        # contradiction REFUSES.
        assert result.verdict == "REFUSE"

    def test_malformed_constitution_defaults_to_opt_in(
        self, step, chain_and_const,
    ):
        chain, const = chain_and_const
        const.write_text("{not: valid: yaml", encoding="utf-8")
        result = step.evaluate(
            _ctx(
                {"note": "Forest is licensed under MIT"},
                constitution_path=const,
            ),
        )
        # YAML parse error → opt-in default → HIGH contradiction
        # warns but goes.
        assert result.verdict == "GO"
        flagged = _events(Path(chain.path), "reality_anchor_flagged")
        assert len(flagged) == 1


class TestVerifierFailure:
    def test_verifier_exception_degrades_to_go_with_flag(
        self, chain_and_const,
    ):
        chain, const = chain_and_const

        def boom(claim, agent_const):
            raise RuntimeError("verifier exploded")

        s = RealityAnchorStep(
            audit=chain,
            verify_claim_fn=boom,
            load_constitution_opt_out_fn=_reality_anchor_opt_out,
        )
        result = s.evaluate(
            _ctx({"note": "anything"}, constitution_path=const),
        )
        assert result.verdict == "GO"
        flagged = _events(Path(chain.path), "reality_anchor_flagged")
        assert len(flagged) == 1
        assert flagged[0]["event_data"]["reason"] == "verifier_raised"


class TestFlattenArgs:
    def test_top_level_strings_joined(self):
        out = _flatten_args_to_claim({"a": "hello", "b": "world"})
        assert "hello" in out and "world" in out

    def test_nested_dict_one_level_included(self):
        out = _flatten_args_to_claim({"outer": {"inner": "nested-value"}})
        assert "nested-value" in out

    def test_list_of_strings_included(self):
        out = _flatten_args_to_claim({"hosts": ["mit.edu", "anthropic.com"]})
        assert "mit.edu" in out and "anthropic.com" in out

    def test_non_strings_skipped(self):
        out = _flatten_args_to_claim(
            {"n": 42, "b": True, "x": None, "s": "kept"},
        )
        assert out == "kept"

    def test_depth_cap_protects_from_pathological_nesting(self):
        # 5 levels deep — should hit the cap and not recurse forever.
        data = {"a": {"b": {"c": {"d": {"e": "deep"}}}}}
        out = _flatten_args_to_claim(data)
        # We don't assert exact content here — just that the call
        # returns without recursion error.
        assert isinstance(out, str)

    def test_empty_dict_returns_empty(self):
        assert _flatten_args_to_claim({}) == ""

    def test_non_dict_returns_empty(self):
        assert _flatten_args_to_claim("not a dict") == ""  # type: ignore[arg-type]
        assert _flatten_args_to_claim(None) == ""  # type: ignore[arg-type]
