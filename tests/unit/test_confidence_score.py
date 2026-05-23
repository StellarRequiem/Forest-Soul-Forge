"""Tests for ADR-0090 Phase B — confidence_score.v1 builtin tool."""
from __future__ import annotations

import asyncio

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.confidence_score import (
    ConfidenceScoreTool,
)


def _ctx():
    return ToolContext(
        instance_id="lab_synth_test",
        agent_dna="a" * 12,
        role="lab_synthesizer",
        genre="researcher",
        session_id=None,
    )


def _run(args):
    return asyncio.run(ConfidenceScoreTool().execute(args, _ctx()))


class TestValidation:
    def test_claim_required(self):
        with pytest.raises(ToolValidationError, match="claim"):
            ConfidenceScoreTool().validate({"source_count": 1})

    def test_source_count_required(self):
        with pytest.raises(ToolValidationError, match="source_count"):
            ConfidenceScoreTool().validate({"claim": "X"})

    def test_source_count_must_be_nonneg(self):
        with pytest.raises(ToolValidationError, match="source_count"):
            ConfidenceScoreTool().validate(
                {"claim": "X", "source_count": -1}
            )

    def test_verdict_must_be_valid(self):
        with pytest.raises(ToolValidationError, match="verdict"):
            ConfidenceScoreTool().validate(
                {"claim": "X", "source_count": 1, "verdict": "bogus"}
            )

    def test_counter_count_must_be_nonneg(self):
        with pytest.raises(ToolValidationError, match="counter_count"):
            ConfidenceScoreTool().validate(
                {"claim": "X", "source_count": 1, "counter_count": -1}
            )

    def test_claim_length_capped(self):
        with pytest.raises(ToolValidationError, match="claim"):
            ConfidenceScoreTool().validate(
                {"claim": "x" * 3000, "source_count": 1}
            )


class TestScoring:
    def test_zero_sources_unknown_verdict_low_band(self):
        r = _run({"claim": "X", "source_count": 0})
        # base=0.10 + UNKNOWN(-0.05) = 0.05 -> low
        assert r.output["band"] == "low"
        assert r.output["score"] == pytest.approx(0.05, abs=0.001)

    def test_one_source_confirmed_medium_band(self):
        r = _run({
            "claim": "X", "source_count": 1, "verdict": "CONFIRMED",
        })
        # base=0.35 + CONFIRMED(0.15) = 0.50 -> medium
        assert r.output["band"] == "medium"
        assert r.output["score"] == pytest.approx(0.50, abs=0.001)

    def test_three_sources_confirmed_high_band(self):
        r = _run({
            "claim": "X", "source_count": 3, "verdict": "CONFIRMED",
        })
        # base=0.70 + CONFIRMED(0.15) = 0.85 -> high
        assert r.output["band"] == "high"
        assert r.output["score"] == pytest.approx(0.85, abs=0.001)

    def test_four_plus_sources_saturates_base(self):
        r1 = _run({"claim": "X", "source_count": 4})
        r2 = _run({"claim": "X", "source_count": 100})
        assert r1.output["breakdown"]["base"] == r2.output["breakdown"]["base"]
        assert r1.output["breakdown"]["base"] == 0.80

    def test_refuted_drops_band(self):
        r = _run({
            "claim": "X", "source_count": 3, "verdict": "REFUTED",
        })
        # base=0.70 + REFUTED(-0.30) = 0.40 -> medium (boundary)
        assert r.output["band"] == "medium"
        assert r.output["score"] == pytest.approx(0.40, abs=0.001)

    def test_counter_penalty_subtracts(self):
        r = _run({
            "claim": "X", "source_count": 3, "verdict": "CONFIRMED",
            "counter_count": 2,
        })
        # base=0.70 + CONFIRMED(0.15) - 2*0.10 = 0.65 -> medium
        assert r.output["band"] == "medium"
        assert r.output["score"] == pytest.approx(0.65, abs=0.001)

    def test_counter_penalty_clamps_to_zero(self):
        r = _run({
            "claim": "X", "source_count": 0, "verdict": "UNKNOWN",
            "counter_count": 10,
        })
        # base=0.10 + UNKNOWN(-0.05) - 1.00 -> clamped to 0.0
        assert r.output["score"] == 0.0
        assert r.output["band"] == "low"

    def test_score_clamps_to_one(self):
        # Synthetic case with all positives at saturation
        r = _run({
            "claim": "X", "source_count": 4, "verdict": "CONFIRMED",
        })
        assert r.output["score"] <= 1.0

    def test_inconclusive_no_adjustment(self):
        r1 = _run({
            "claim": "X", "source_count": 2, "verdict": "INCONCLUSIVE",
        })
        r2 = _run({
            "claim": "X", "source_count": 2,
        })  # default UNKNOWN
        # INCONCLUSIVE = 0.00 adj; UNKNOWN = -0.05 adj
        assert r1.output["score"] > r2.output["score"]

    def test_deterministic(self):
        a = _run({
            "claim": "X", "source_count": 2, "verdict": "CONFIRMED",
            "counter_count": 1,
        })
        b = _run({
            "claim": "X", "source_count": 2, "verdict": "CONFIRMED",
            "counter_count": 1,
        })
        assert a.output["score"] == b.output["score"]
        assert a.output["band"] == b.output["band"]

    def test_breakdown_records_signals(self):
        r = _run({
            "claim": "X", "source_count": 2, "verdict": "CONFIRMED",
            "counter_count": 1,
        })
        bd = r.output["breakdown"]
        assert bd["base"] == 0.55
        assert bd["verdict"] == "CONFIRMED"
        assert bd["verdict_adjustment"] == 0.15
        assert bd["counter_count"] == 1
        assert bd["counter_penalty"] == 0.10
        assert bd["source_count"] == 2

    def test_rationale_is_human_readable(self):
        r = _run({
            "claim": "X", "source_count": 2, "verdict": "CONFIRMED",
        })
        assert "source_count=2" in r.output["rationale"]
        assert "verdict=CONFIRMED" in r.output["rationale"]
        assert "final=" in r.output["rationale"]

    def test_topic_slug_and_claim_id_recorded(self):
        r = _run({
            "claim": "X", "source_count": 1,
            "topic_slug": "diffusion", "claim_id": "cl_abc123",
        })
        assert r.output["topic_slug"] == "diffusion"
        assert r.output["claim_id"] == "cl_abc123"

    def test_high_threshold_boundary(self):
        # Boundary: score >= 0.70 = high
        r = _run({
            "claim": "X", "source_count": 3,
        })
        # base=0.70 + UNKNOWN(-0.05) = 0.65 -> medium
        assert r.output["band"] == "medium"

    def test_medium_threshold_boundary(self):
        # Boundary: score >= 0.40 = medium
        r = _run({
            "claim": "X", "source_count": 1, "verdict": "INCONCLUSIVE",
        })
        # base=0.35 + 0.00 = 0.35 -> low
        assert r.output["band"] == "low"
