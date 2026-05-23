"""``confidence_score.v1`` — ADR-0090 Phase B confidence calibrator.

Computes a calibrated confidence band (low / medium / high) for
a single claim from three signals:

  1. Source count — how many sources back the claim.
  2. Verify_claim verdict — what did the Reality Anchor say?
     CONFIRMED = +1 band; REFUTED = -1 band; INCONCLUSIVE / UNKNOWN
     = 0 band.
  3. Critic counter density — how many counter-evidence
     attestations does the critic agent have against this claim?
     The higher the counter density, the lower the confidence.

Deterministic. Two calls with the same inputs produce the same
score and band. Read-only — the lab_synthesizer (D10 Phase B)
is the primary consumer; the score is recorded in every
synthesis report so the operator can see the per-conclusion
uncertainty band.

## Scoring model

Each claim gets a base score from source_count:

  - source_count == 0  →  0.10
  - source_count == 1  →  0.35
  - source_count == 2  →  0.55
  - source_count == 3  →  0.70
  - source_count >= 4  →  0.80

A verdict adjustment is then applied:

  - CONFIRMED     →  +0.15
  - REFUTED       →  -0.30
  - INCONCLUSIVE  →   0.00
  - UNKNOWN       →  -0.05

A critic-counter penalty is then subtracted:

  - counter_count * 0.10 (clamped so the score never drops below
    0.0)

Final score is clamped to [0.0, 1.0] and bucketed into a band:

  - score >= 0.70  →  high
  - score >= 0.40  →  medium
  - score <  0.40  →  low

The tool also surfaces the contributions of each signal so the
operator can audit which signal drove the band.

side_effects=read_only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_MAX_CLAIM_LEN = 2000
_VALID_VERDICTS = {"CONFIRMED", "REFUTED", "INCONCLUSIVE", "UNKNOWN"}

_BASE_BY_SOURCES = {
    0: 0.10,
    1: 0.35,
    2: 0.55,
    3: 0.70,
}
_BASE_SATURATED = 0.80

_VERDICT_ADJ = {
    "CONFIRMED":    0.15,
    "REFUTED":     -0.30,
    "INCONCLUSIVE": 0.00,
    "UNKNOWN":     -0.05,
}

_COUNTER_PENALTY = 0.10

_HIGH_THRESHOLD = 0.70
_MEDIUM_THRESHOLD = 0.40


class ConfidenceScoreTool:
    """Compute a calibrated confidence band for a single claim."""

    name = "confidence_score"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        claim = args.get("claim")
        if not isinstance(claim, str) or not claim.strip():
            raise ToolValidationError("claim is required")
        if len(claim) > _MAX_CLAIM_LEN:
            raise ToolValidationError(
                f"claim must be <= {_MAX_CLAIM_LEN} chars"
            )

        sc = args.get("source_count")
        if not isinstance(sc, int) or sc < 0:
            raise ToolValidationError(
                "source_count must be a non-negative integer"
            )

        verdict = args.get("verdict")
        if verdict is not None and verdict not in _VALID_VERDICTS:
            raise ToolValidationError(
                f"verdict must be one of {sorted(_VALID_VERDICTS)}"
            )

        cc = args.get("counter_count", 0)
        if not isinstance(cc, int) or cc < 0:
            raise ToolValidationError(
                "counter_count must be a non-negative integer"
            )

        for k in ("topic_slug", "claim_id"):
            v = args.get(k)
            if v is not None and not isinstance(v, str):
                raise ToolValidationError(f"{k} must be a string")

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        claim = args["claim"]
        sc = int(args["source_count"])
        verdict = args.get("verdict") or "UNKNOWN"
        cc = int(args.get("counter_count") or 0)
        slug = args.get("topic_slug") or ""
        claim_id = args.get("claim_id") or ""

        base = _BASE_BY_SOURCES.get(sc, _BASE_SATURATED)
        verdict_adj = _VERDICT_ADJ[verdict]
        counter_penalty = cc * _COUNTER_PENALTY

        raw = base + verdict_adj - counter_penalty
        # Round before bucketing so float drift (e.g. 0.7 - 0.3 ≈
        # 0.3999…) doesn't silently flip a boundary score from
        # `medium` to `low`. The persisted score in the output body
        # is the same rounded value, so band ↔ score stay coherent.
        score = round(max(0.0, min(1.0, raw)), 4)

        if score >= _HIGH_THRESHOLD:
            band = "high"
        elif score >= _MEDIUM_THRESHOLD:
            band = "medium"
        else:
            band = "low"

        rationale_parts = [
            f"source_count={sc} base={base:.2f}",
            f"verdict={verdict} adj={verdict_adj:+.2f}",
            f"counter_count={cc} penalty=-{counter_penalty:.2f}",
            f"final={score:.2f} -> {band}",
        ]
        rationale = "; ".join(rationale_parts)

        body = {
            "claim":            claim,
            "claim_id":         claim_id,
            "topic_slug":       slug,
            "scored_at":        datetime.now(timezone.utc)
                                            .replace(tzinfo=None)
                                            .isoformat(timespec="seconds")
                                            + "Z",
            "score":            round(score, 4),
            "band":             band,
            "breakdown":        {
                "base":              round(base, 4),
                "verdict":           verdict,
                "verdict_adjustment": round(verdict_adj, 4),
                "counter_count":     cc,
                "counter_penalty":   round(counter_penalty, 4),
                "source_count":      sc,
            },
            "rationale":        rationale,
        }
        return ToolResult(
            output=body,
            metadata={
                "claim_id":   claim_id,
                "topic_slug": slug,
                "score":      round(score, 4),
                "band":       band,
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"scored claim {claim_id or '<inline>'}: "
                f"{band} ({score:.2f})"
            ),
        )
