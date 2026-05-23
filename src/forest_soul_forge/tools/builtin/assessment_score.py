"""``assessment_score.v1`` — ADR-0089 Phase B response scorer.

Scores an operator response against an assessment item via a
deterministic rubric — combines exact-match lookup against a
ground-truth set with a normalized lexical overlap score. Read-only.
The skill that wraps this tool layers verify_claim.v1 on top for
Reality-Anchored checks of factual content + llm_think for narrative
rubric narration; this tool produces the deterministic numerical
verdict.

## Scoring model

For each item:
  - Strict match against ``ground_truth_answers`` (case-insensitive,
    whitespace-normalized) → score 1.0, verdict ``correct``.
  - Lexical overlap (token-set Jaccard) against ``ground_truth_answers``
    → score in [0.0, 1.0]:
      - >= 0.75 → verdict ``correct``
      - >= 0.40 → verdict ``partial``
      - <  0.40 → verdict ``incorrect``
  - When ``ground_truth_answers`` is empty (open-ended explain
    items the assessor wants to defer to LLM rubric), the tool
    returns score 0.0 + verdict ``deferred`` and surfaces ``needs_rubric: true``.

The tool returns the per-item breakdown so the operator can audit
which dimension drove the verdict. The misconception_log.v1 sibling
tool persists the operator-acknowledged corrections.

side_effects=read_only.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_MAX_RESPONSE_LEN = 10_000
_MAX_GT_ANSWERS = 20
_MAX_GT_LEN = 5000
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


class AssessmentScoreTool:
    """Score an operator response against an assessment item.

    Args:
      item_id (str, required): the assessment_item id this scoring
        references. Stable id from knowledge_assessment.v1.
      topic_slug (str, required): slug of the topic being scored.
      response (str, required): the operator's answer text.
      ground_truth_answers (list[str], optional): accepted answers
        (case-insensitive). Each entry is a full expected answer;
        the response is matched against each. When empty, the tool
        defers to LLM rubric and returns verdict ``deferred``.
      partial_credit_threshold (float, optional): lexical-overlap
        ratio required for ``partial`` verdict. Default 0.40.
      full_credit_threshold (float, optional): lexical-overlap
        ratio required for ``correct`` verdict from overlap (strict
        match is always 1.0). Default 0.75.

    Output:
      {
        "item_id":           str,
        "topic_slug":        str,
        "scored_at":         str (ISO),
        "score":             float,    # 0.0 .. 1.0
        "verdict":           str,      # correct / partial / incorrect / deferred
        "breakdown":         {
          "strict_match":    bool,
          "lexical_overlap": float,    # token-set Jaccard
          "response_tokens": int,
          "best_match_index": int | null,  # which GT was closest
        },
        "needs_rubric":      bool,
        "rationale":         str,      # short human-readable
      }
    """

    name = "assessment_score"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        item_id = args.get("item_id")
        if not isinstance(item_id, str) or not item_id.strip():
            raise ToolValidationError("item_id is required")

        slug = args.get("topic_slug")
        if not isinstance(slug, str) or not slug.strip():
            raise ToolValidationError("topic_slug is required")

        response = args.get("response")
        if not isinstance(response, str):
            raise ToolValidationError("response must be a string")
        if len(response) > _MAX_RESPONSE_LEN:
            raise ToolValidationError(
                f"response must be <= {_MAX_RESPONSE_LEN} chars"
            )

        gt = args.get("ground_truth_answers", [])
        if not isinstance(gt, list):
            raise ToolValidationError(
                "ground_truth_answers must be a list of strings"
            )
        if len(gt) > _MAX_GT_ANSWERS:
            raise ToolValidationError(
                f"ground_truth_answers must have <= {_MAX_GT_ANSWERS} entries"
            )
        for i, a in enumerate(gt):
            if not isinstance(a, str):
                raise ToolValidationError(
                    f"ground_truth_answers[{i}] must be a string"
                )
            if len(a) > _MAX_GT_LEN:
                raise ToolValidationError(
                    f"ground_truth_answers[{i}] must be <= {_MAX_GT_LEN} chars"
                )

        for k in ("partial_credit_threshold", "full_credit_threshold"):
            v = args.get(k)
            if v is not None:
                if not isinstance(v, (int, float)) or v < 0 or v > 1:
                    raise ToolValidationError(
                        f"{k} must be a number in [0, 1]"
                    )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        item_id = args["item_id"]
        slug = args["topic_slug"]
        response = args["response"]
        gt = list(args.get("ground_truth_answers") or [])
        partial_thr = float(args.get("partial_credit_threshold") or 0.40)
        full_thr = float(args.get("full_credit_threshold") or 0.75)

        response_norm = _normalize(response)
        response_tokens = _tokenize(response)

        strict_match = False
        for a in gt:
            if _normalize(a) == response_norm and response_norm:
                strict_match = True
                break

        best_overlap = 0.0
        best_index: int | None = None
        for i, a in enumerate(gt):
            o = _jaccard(response_tokens, _tokenize(a))
            if o > best_overlap:
                best_overlap = o
                best_index = i

        if not gt:
            score = 0.0
            verdict = "deferred"
            needs_rubric = True
            rationale = (
                "No ground_truth_answers supplied; defer to LLM rubric."
            )
        elif strict_match:
            score = 1.0
            verdict = "correct"
            needs_rubric = False
            rationale = "Exact match against ground-truth answer."
        elif best_overlap >= full_thr:
            score = round(best_overlap, 4)
            verdict = "correct"
            needs_rubric = False
            rationale = (
                f"Lexical overlap {best_overlap:.2f} "
                f">= full_credit_threshold {full_thr:.2f}."
            )
        elif best_overlap >= partial_thr:
            score = round(best_overlap, 4)
            verdict = "partial"
            needs_rubric = True
            rationale = (
                f"Lexical overlap {best_overlap:.2f} "
                f"between partial ({partial_thr:.2f}) and full ({full_thr:.2f}); "
                f"recommend LLM rubric for narrative + misconception logging."
            )
        else:
            score = round(best_overlap, 4)
            verdict = "incorrect"
            needs_rubric = True
            rationale = (
                f"Lexical overlap {best_overlap:.2f} "
                f"< partial_credit_threshold {partial_thr:.2f}."
            )

        body = {
            "item_id":       item_id,
            "topic_slug":    slug,
            "scored_at":     datetime.now(timezone.utc)
                                          .replace(tzinfo=None)
                                          .isoformat(timespec="seconds")
                                          + "Z",
            "score":         score,
            "verdict":       verdict,
            "breakdown":     {
                "strict_match":     strict_match,
                "lexical_overlap":  round(best_overlap, 4),
                "response_tokens":  len(response_tokens),
                "best_match_index": best_index,
            },
            "needs_rubric":  needs_rubric,
            "rationale":     rationale,
        }
        return ToolResult(
            output=body,
            metadata={
                "item_id":   item_id,
                "verdict":   verdict,
                "score":     score,
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"scored {item_id}: {verdict} ({score:.2f})"
            ),
        )


def _normalize(s: str) -> str:
    return " ".join(s.lower().split())


def _tokenize(s: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(s)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0
