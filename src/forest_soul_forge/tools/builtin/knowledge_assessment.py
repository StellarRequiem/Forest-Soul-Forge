"""``knowledge_assessment.v1`` — ADR-0089 Phase B quiz generator.

Deterministically generates a structured assessment item from a
curriculum slug + difficulty level + item kind (multiple_choice /
short_answer / explain). Returns a self-contained quiz item the
operator can answer; the answer is scored separately by
``assessment_score.v1``.

Read-only. The ``knowledge_assessment.v1`` skill wraps this tool
with memory_recall of curriculum context + a memory_write of the
emitted item; the LLM-grade item-text composition is layered
separately via llm_think (this tool's output gives the skill the
structural template — kind, difficulty, slug, item_id — and the
LLM fills the prompt text).

## Why deterministic structure

The structural template (slug + difficulty + kind + stable item_id)
is the load-bearing audit substrate. Two calls with the same inputs
produce the same item envelope, so the assessor can replay the
generation + the operator can audit which item generation produced
which scoring event. LLM item-text composition on top stays
auditable because the structure that frames it is deterministic.

side_effects=read_only.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_VALID_KINDS = {"multiple_choice", "short_answer", "explain"}
_VALID_DIFFICULTIES = {"easy", "medium", "hard"}
_MAX_SLUG_LEN = 200
_MAX_PROMPT_LEN = 5000


class KnowledgeAssessmentTool:
    """Generate a structured quiz-item template for an operator.

    Args:
      topic_slug (str, required): kebab-case slug identifying the
        topic being assessed. Should match a slug from a
        curriculum_design.v1 ordered_path entry.
      difficulty (str, optional): ``easy`` / ``medium`` / ``hard``.
        Default ``medium``.
      kind (str, optional): ``multiple_choice`` / ``short_answer`` /
        ``explain``. Default ``short_answer``.
      seed (str, optional): operator-supplied seed for item id
        derivation. Lets the operator generate alternative items
        on the same (slug, difficulty, kind) by varying the seed.
      prompt_template (str, optional): operator-curated framing
        text the downstream LLM uses to compose the item. When
        absent, the tool returns the structural envelope only
        and the skill's llm_think step composes the prompt.

    Output:
      {
        "item_id":         str,        # stable derived id
        "topic_slug":      str,
        "difficulty":      str,
        "kind":            str,
        "generated_at":    str (ISO),
        "structural":      {
          "options_required":   bool,  # True for multiple_choice
          "free_text_allowed":  bool,
          "max_answer_len":     int,
        },
        "prompt_template": str | null,  # echoes operator input
      }
    """

    name = "knowledge_assessment"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        slug = args.get("topic_slug")
        if not isinstance(slug, str) or not slug.strip():
            raise ToolValidationError(
                "topic_slug must be a non-empty string"
            )
        if len(slug) > _MAX_SLUG_LEN:
            raise ToolValidationError(
                f"topic_slug must be <= {_MAX_SLUG_LEN} chars"
            )

        difficulty = args.get("difficulty", "medium")
        if not isinstance(difficulty, str) or difficulty not in _VALID_DIFFICULTIES:
            raise ToolValidationError(
                f"difficulty must be one of {sorted(_VALID_DIFFICULTIES)}"
            )

        kind = args.get("kind", "short_answer")
        if not isinstance(kind, str) or kind not in _VALID_KINDS:
            raise ToolValidationError(
                f"kind must be one of {sorted(_VALID_KINDS)}"
            )

        seed = args.get("seed")
        if seed is not None and not isinstance(seed, str):
            raise ToolValidationError("seed must be a string")

        pt = args.get("prompt_template")
        if pt is not None:
            if not isinstance(pt, str):
                raise ToolValidationError(
                    "prompt_template must be a string"
                )
            if len(pt) > _MAX_PROMPT_LEN:
                raise ToolValidationError(
                    f"prompt_template must be <= {_MAX_PROMPT_LEN} chars"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        slug = args["topic_slug"]
        difficulty = args.get("difficulty") or "medium"
        kind = args.get("kind") or "short_answer"
        seed = args.get("seed") or ""
        prompt_template = args.get("prompt_template")

        item_id = _derive_item_id(slug, difficulty, kind, seed)

        structural = {
            "multiple_choice": {
                "options_required": True,
                "free_text_allowed": False,
                "max_answer_len": 200,
            },
            "short_answer": {
                "options_required": False,
                "free_text_allowed": True,
                "max_answer_len": 500,
            },
            "explain": {
                "options_required": False,
                "free_text_allowed": True,
                "max_answer_len": 3000,
            },
        }[kind]

        body = {
            "item_id":         item_id,
            "topic_slug":      slug,
            "difficulty":      difficulty,
            "kind":            kind,
            "generated_at":    datetime.now(timezone.utc)
                                          .replace(tzinfo=None)
                                          .isoformat(timespec="seconds")
                                          + "Z",
            "structural":      structural,
            "prompt_template": prompt_template,
        }
        return ToolResult(
            output=body,
            metadata={
                "item_id":    item_id,
                "topic_slug": slug,
                "difficulty": difficulty,
                "kind":       kind,
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"assessment item {item_id} for {slug!r} "
                f"({difficulty}/{kind})"
            ),
        )


def _derive_item_id(
    slug: str, difficulty: str, kind: str, seed: str,
) -> str:
    blob = f"{slug}|{difficulty}|{kind}|{seed}"
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return f"item_{digest[:12]}"
