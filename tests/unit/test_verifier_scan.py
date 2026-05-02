"""Tests for verifier.scan (ADR-0036 T3b — LLM-dispatching scan runner).

The scan runner is pure logic + injected callables. Tests use a mock
classify_callable that returns canned LLM responses per pair, and a
mock flagger that records flags. No provider, no dispatcher.

Coverage:
- TestPromptBuilder       — build_classification_prompt shape
- TestParser              — parse_llm_classification: well-formed
                            JSON, prose-prefixed, malformed, kind
                            normalization
- TestVerifierScanInit    — __init__ validation
- TestRunScan             — empty pairs, all branches
                            (flagged, skipped_low_conf,
                            skipped_unrelated, skipped_no_contradiction,
                            classify-error, flagger-error, missing
                            entry at hydration), aggregation
- TestAuditEventType      — verifier_scan_completed registered in
                            KNOWN_EVENT_TYPES
"""
from __future__ import annotations

from pathlib import Path

import pytest

from forest_soul_forge.core.audit_chain import KNOWN_EVENT_TYPES
from forest_soul_forge.core.memory import Memory
from forest_soul_forge.registry import Registry
from forest_soul_forge.verifier.scan import (
    DEFAULT_MIN_CONFIDENCE,
    VALID_KINDS,
    ClassificationResult,
    PairOutcome,
    ScanResult,
    VerifierScan,
    build_classification_prompt,
    parse_llm_classification,
)
from tests.unit.conftest import seed_stub_agent


@pytest.fixture
def env(tmp_path):
    reg = Registry.bootstrap(tmp_path / "reg.sqlite")
    seed_stub_agent(reg, "agent_a")
    seed_stub_agent(reg, "verifier_v1")
    memory = Memory(conn=reg._conn)  # noqa: SLF001
    yield {"memory": memory, "registry": reg}
    reg.close()


def _seed_pair(memory, content_a, content_b):
    a = memory.append(
        instance_id="agent_a", agent_dna="d" * 12,
        content=content_a, layer="semantic",
        claim_type="preference",
    )
    b = memory.append(
        instance_id="agent_a", agent_dna="d" * 12,
        content=content_b, layer="semantic",
        claim_type="preference",
    )
    return a, b


def _scan(memory, classify, *, min_confidence=DEFAULT_MIN_CONFIDENCE):
    """Build a VerifierScan with a flag-recording mock flagger."""
    flags_written: list[dict] = []

    def flagger(**kwargs):
        flags_written.append(dict(kwargs))
        cid = f"contra_{len(flags_written):04d}"
        return cid, "2026-05-02T00:00:00Z"

    scan = VerifierScan(
        memory=memory,
        classify=classify,
        flagger=flagger,
        verifier_instance_id="verifier_v1",
        min_confidence=min_confidence,
    )
    return scan, flags_written


# ===========================================================================
# Prompt builder
# ===========================================================================
class TestPromptBuilder:
    def test_contains_both_entries(self):
        p = build_classification_prompt("user prefers tea", "user prefers coffee")
        assert "user prefers tea" in p
        assert "user prefers coffee" in p

    def test_contains_kind_taxonomy(self):
        p = build_classification_prompt("a", "b")
        for kind in VALID_KINDS:
            assert kind in p

    def test_includes_claim_types_when_provided(self):
        p = build_classification_prompt(
            "x", "y",
            earlier_claim_type="preference",
            later_claim_type="user_statement",
        )
        assert "[preference]" in p
        assert "[user_statement]" in p

    def test_omits_claim_type_tags_when_blank(self):
        p = build_classification_prompt("x", "y")
        assert "[preference]" not in p

    def test_instructs_strict_json(self):
        p = build_classification_prompt("x", "y")
        assert "JSON" in p
        assert "same_topic" in p
        assert "confidence" in p


# ===========================================================================
# Parser
# ===========================================================================
class TestParser:
    def test_well_formed(self):
        raw = (
            '{"same_topic": true, "contradictory": true, "kind": "updated",'
            ' "confidence": 0.92, "reasoning": "B replaces A"}'
        )
        clf = parse_llm_classification(raw)
        assert clf.same_topic is True
        assert clf.contradictory is True
        assert clf.kind == "updated"
        assert clf.confidence == pytest.approx(0.92)
        assert clf.reasoning == "B replaces A"

    def test_prose_prefixed(self):
        raw = (
            "Looking at both entries, my analysis is:\n"
            '{"same_topic": true, "contradictory": false, "kind": null,'
            ' "confidence": 0.65, "reasoning": "agree on tea"}'
        )
        clf = parse_llm_classification(raw)
        assert clf.same_topic is True
        assert clf.contradictory is False
        assert clf.kind is None

    def test_kind_forced_null_when_not_contradictory(self):
        # Even if the LLM emits a kind despite same_topic=false, the
        # parser zeroes it out (defense-in-depth).
        raw = (
            '{"same_topic": false, "contradictory": false, "kind": "direct",'
            ' "confidence": 0.9, "reasoning": "different topics"}'
        )
        clf = parse_llm_classification(raw)
        assert clf.kind is None

    def test_invalid_kind(self):
        raw = (
            '{"same_topic": true, "contradictory": true, "kind": "bogus_kind",'
            ' "confidence": 0.9, "reasoning": ""}'
        )
        clf = parse_llm_classification(raw)
        assert clf.confidence == 0.0
        assert "PARSE_ERROR" in clf.reasoning

    def test_clamps_confidence(self):
        raw_high = (
            '{"same_topic": true, "contradictory": true, "kind": "direct",'
            ' "confidence": 5.0, "reasoning": ""}'
        )
        raw_low = (
            '{"same_topic": true, "contradictory": true, "kind": "direct",'
            ' "confidence": -0.5, "reasoning": ""}'
        )
        assert parse_llm_classification(raw_high).confidence == 1.0
        assert parse_llm_classification(raw_low).confidence == 0.0

    def test_non_numeric_confidence(self):
        raw = (
            '{"same_topic": true, "contradictory": true, "kind": "direct",'
            ' "confidence": "high", "reasoning": ""}'
        )
        clf = parse_llm_classification(raw)
        assert "PARSE_ERROR" in clf.reasoning

    def test_empty_response(self):
        clf = parse_llm_classification("")
        assert "PARSE_ERROR" in clf.reasoning

    def test_no_json_block(self):
        clf = parse_llm_classification("the model just rambled in prose")
        assert "PARSE_ERROR" in clf.reasoning

    def test_invalid_json(self):
        clf = parse_llm_classification("{not valid json}")
        assert "PARSE_ERROR" in clf.reasoning


# ===========================================================================
# VerifierScan __init__
# ===========================================================================
class TestVerifierScanInit:
    def test_min_confidence_must_be_in_range(self, env):
        with pytest.raises(ValueError, match="min_confidence"):
            VerifierScan(
                memory=env["memory"],
                classify=lambda p: "",
                flagger=lambda **kw: ("", ""),
                verifier_instance_id="v",
                min_confidence=1.5,
            )
        with pytest.raises(ValueError, match="min_confidence"):
            VerifierScan(
                memory=env["memory"],
                classify=lambda p: "",
                flagger=lambda **kw: ("", ""),
                verifier_instance_id="v",
                min_confidence=-0.1,
            )

    def test_verifier_id_required(self, env):
        with pytest.raises(ValueError, match="verifier_instance_id"):
            VerifierScan(
                memory=env["memory"],
                classify=lambda p: "",
                flagger=lambda **kw: ("", ""),
                verifier_instance_id="",
            )


# ===========================================================================
# run_scan
# ===========================================================================
class TestRunScan:
    def test_no_pairs_returns_empty_result(self, env):
        scan, flags = _scan(env["memory"], lambda p: "")
        result = scan.run_scan(target_instance_id="agent_a")
        assert result.pairs_considered == 0
        assert result.pairs_classified == 0
        assert result.flags_written == 0
        assert result.outcomes == []
        assert flags == []

    def test_high_confidence_contradiction_flags(self, env):
        a, b = _seed_pair(env["memory"], "user prefers tea morning", "user prefers coffee morning")

        def classify(_prompt):
            return (
                '{"same_topic": true, "contradictory": true, '
                '"kind": "updated", "confidence": 0.9, '
                '"reasoning": "B replaces A"}'
            )

        scan, flags = _scan(env["memory"], classify)
        result = scan.run_scan(target_instance_id="agent_a")
        assert result.pairs_considered == 1
        assert result.pairs_classified == 1
        assert result.flags_written == 1
        assert flags[0]["earlier_entry_id"] == a.entry_id
        assert flags[0]["later_entry_id"] == b.entry_id
        assert flags[0]["contradiction_kind"] == "updated"
        assert flags[0]["detected_by"] == "verifier_v1"

    def test_low_confidence_contradiction_skipped(self, env):
        _seed_pair(env["memory"], "user prefers tea morning", "user prefers coffee morning")

        def classify(_prompt):
            return (
                '{"same_topic": true, "contradictory": true, '
                '"kind": "updated", "confidence": 0.5, "reasoning": "maybe"}'
            )

        scan, flags = _scan(env["memory"], classify)
        result = scan.run_scan(target_instance_id="agent_a")
        assert result.flags_written == 0
        assert result.low_confidence_skipped == 1
        assert flags == []

    def test_unrelated_pair_skipped(self, env):
        _seed_pair(env["memory"], "user prefers tea morning", "user prefers tea evening")

        def classify(_prompt):
            return (
                '{"same_topic": false, "contradictory": false, '
                '"kind": null, "confidence": 0.9, '
                '"reasoning": "different topics"}'
            )

        scan, flags = _scan(env["memory"], classify)
        result = scan.run_scan(target_instance_id="agent_a")
        assert result.flags_written == 0
        assert result.unrelated_skipped == 1
        assert flags == []

    def test_same_topic_no_contradiction_skipped(self, env):
        _seed_pair(env["memory"], "user prefers tea", "user enjoys tea")

        def classify(_prompt):
            return (
                '{"same_topic": true, "contradictory": false, '
                '"kind": null, "confidence": 0.9, "reasoning": "agree"}'
            )

        scan, flags = _scan(env["memory"], classify)
        result = scan.run_scan(target_instance_id="agent_a")
        assert result.flags_written == 0
        assert result.no_contradiction_skipped == 1

    def test_classify_error_recorded(self, env):
        _seed_pair(env["memory"], "user prefers tea morning", "user prefers coffee morning")

        def classify(_prompt):
            raise RuntimeError("provider down")

        scan, flags = _scan(env["memory"], classify)
        result = scan.run_scan(target_instance_id="agent_a")
        assert result.errors == 1
        assert result.flags_written == 0
        assert "provider down" in result.outcomes[0].error

    def test_flagger_error_recorded(self, env):
        _seed_pair(env["memory"], "user prefers tea morning", "user prefers coffee morning")

        def classify(_prompt):
            return (
                '{"same_topic": true, "contradictory": true, '
                '"kind": "direct", "confidence": 0.95, "reasoning": ""}'
            )

        def bad_flagger(**_kwargs):
            raise RuntimeError("FK violation")

        scan = VerifierScan(
            memory=env["memory"], classify=classify, flagger=bad_flagger,
            verifier_instance_id="verifier_v1",
        )
        result = scan.run_scan(target_instance_id="agent_a")
        assert result.errors == 1
        assert result.flags_written == 0
        assert "FK violation" in result.outcomes[0].error

    def test_aggregation_across_multiple_pairs(self, env):
        # 4 entries → C(4,2)=6 candidate pairs, all sharing 'user prefers'
        m = env["memory"]
        for i in range(4):
            m.append(
                instance_id="agent_a", agent_dna="d" * 12,
                content=f"user prefers thing-{i}-flavor",
                layer="semantic", claim_type="preference",
            )
        # Mock classify: alternating contradictory(high) / unrelated /
        # contradictory(low) / agree / unrelated / contradictory(high)
        # Note: actual order depends on overlap-size sort, but we
        # assert aggregate counts so order doesn't matter.
        responses = iter([
            '{"same_topic": true, "contradictory": true, "kind": "direct", "confidence": 0.9, "reasoning": ""}',
            '{"same_topic": false, "contradictory": false, "kind": null, "confidence": 0.9, "reasoning": ""}',
            '{"same_topic": true, "contradictory": true, "kind": "direct", "confidence": 0.5, "reasoning": ""}',
            '{"same_topic": true, "contradictory": false, "kind": null, "confidence": 0.9, "reasoning": ""}',
            '{"same_topic": false, "contradictory": false, "kind": null, "confidence": 0.9, "reasoning": ""}',
            '{"same_topic": true, "contradictory": true, "kind": "direct", "confidence": 0.95, "reasoning": ""}',
        ])

        def classify(_p):
            return next(responses)

        scan, flags = _scan(env["memory"], classify)
        result = scan.run_scan(target_instance_id="agent_a")
        assert result.pairs_considered == 6
        assert result.pairs_classified == 6
        assert result.flags_written == 2
        assert result.low_confidence_skipped == 1
        assert result.unrelated_skipped == 2
        assert result.no_contradiction_skipped == 1
        assert result.errors == 0

    def test_max_pairs_caps_scan(self, env):
        m = env["memory"]
        for i in range(5):
            m.append(
                instance_id="agent_a", agent_dna="d" * 12,
                content=f"user prefers thing-{i}-flavor",
                layer="semantic", claim_type="preference",
            )

        # Always contradictory + high — every pair flags.
        def classify(_p):
            return (
                '{"same_topic": true, "contradictory": true, '
                '"kind": "direct", "confidence": 0.95, "reasoning": ""}'
            )

        scan, flags = _scan(env["memory"], classify)
        result = scan.run_scan(target_instance_id="agent_a", max_pairs=3)
        assert result.pairs_considered == 3
        assert result.flags_written == 3

    def test_dedup_against_existing_flags(self, env):
        # Pair already in memory_contradictions → not re-classified.
        a, b = _seed_pair(env["memory"], "user prefers tea morning", "user prefers coffee morning")
        env["memory"].flag_contradiction(
            earlier_entry_id=a.entry_id, later_entry_id=b.entry_id,
            contradiction_kind="direct", detected_by="op",
        )
        # If the scanner did call the LLM, this would pop -> KeyError-
        # equivalent. The pair should be filtered out before classify.
        called = []

        def classify(_p):
            called.append(_p)
            return ""

        scan, flags = _scan(env["memory"], classify)
        result = scan.run_scan(target_instance_id="agent_a")
        assert result.pairs_considered == 0
        assert called == []

    def test_outcomes_have_classification_when_classified(self, env):
        _seed_pair(env["memory"], "user prefers tea morning", "user prefers coffee morning")

        def classify(_p):
            return (
                '{"same_topic": true, "contradictory": true, '
                '"kind": "direct", "confidence": 0.95, '
                '"reasoning": "they conflict"}'
            )

        scan, _ = _scan(env["memory"], classify)
        result = scan.run_scan(target_instance_id="agent_a")
        assert len(result.outcomes) == 1
        outcome = result.outcomes[0]
        assert outcome.action == "flagged"
        assert outcome.classification.contradictory is True
        assert outcome.classification.confidence == 0.95
        assert outcome.contradiction_id is not None


# ===========================================================================
# Audit event type registration
# ===========================================================================
class TestAuditEventType:
    def test_verifier_scan_completed_in_known_events(self):
        # ADR-0036 — the audit event a Verifier emits per scan run.
        # Adding to KNOWN_EVENT_TYPES is the prerequisite for the
        # daemon's verifier service to actually emit the event.
        assert "verifier_scan_completed" in KNOWN_EVENT_TYPES
