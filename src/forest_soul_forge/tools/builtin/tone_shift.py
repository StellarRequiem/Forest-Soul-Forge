"""``tone_shift.v1`` — curated LLM tone-rewriting (B363).

Wraps ``provider.complete`` with a stable tone-rewrite system
prompt. Preserves meaning, changes register. Skills:
  - release_notes.v1
"""
from __future__ import annotations

from typing import Any

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin._prompt_template_base import (
    PromptTemplateToolBase,
)

_VALID_TONES = {
    "formal", "casual", "professional", "friendly", "direct",
    "gentle", "enthusiastic", "neutral", "executive_summary",
    "marketing", "technical",
}


class ToneShiftTool(PromptTemplateToolBase):
    name = "tone_shift"
    version = "1"
    side_effects = "read_only"
    _DEFAULT_MAX_TOKENS = 800
    _TASK_KIND_DEFAULT = "generate"

    description = (
        "Rewrite a text blob in a different tone/register while "
        "preserving meaning, facts, and numbers. Used by the "
        "release-notes skill to adapt a technical draft for a "
        "less-technical audience."
    )

    def _validate_specific(self, args: dict[str, Any]) -> None:
        self._validate_text_field(args, "text", required=True)
        target_tone = args.get("target_tone")
        if target_tone not in _VALID_TONES:
            raise ToolValidationError(
                f"target_tone must be one of {sorted(_VALID_TONES)}; "
                f"got {target_tone!r}"
            )
        preserve_structure = args.get("preserve_structure", True)
        if not isinstance(preserve_structure, bool):
            raise ToolValidationError(
                f"preserve_structure must be a boolean; "
                f"got {type(preserve_structure).__name__}"
            )

    def _build_prompts(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> tuple[str, str]:
        text = args["text"]
        target_tone = args["target_tone"]
        preserve_structure = bool(args.get("preserve_structure", True))

        structure_clause = (
            "Preserve the source's paragraph/section structure "
            "(same number of paragraphs, same heading hierarchy)."
            if preserve_structure else
            "You may restructure paragraphs/sections to fit the "
            "target tone."
        )

        # Tone-specific guidance. Each clause is the model's
        # actionable instruction, not a label.
        tone_clauses = {
            "formal": "Use formal vocabulary. No contractions. Complete sentences.",
            "casual": "Use everyday vocabulary. Contractions allowed. Conversational rhythm.",
            "professional": "Polished business register. Neither stiff nor casual. Active voice.",
            "friendly": "Warm and approachable. Light contractions. Inviting phrasing.",
            "direct": "Short sentences. Lead with the point. Cut hedging and qualifiers.",
            "gentle": "Soften assertions. Use 'may', 'might', 'consider'. Avoid imperative.",
            "enthusiastic": "Active voice. Energetic verbs. Avoid superlatives that aren't in the source.",
            "neutral": "Plain register. No emphasis, no softening. State facts.",
            "executive_summary": "Lead with the bottom line. One paragraph. Numbers stay literal.",
            "marketing": "Benefit-led phrasing. Reader-focused ('you'). Skip jargon.",
            "technical": "Precise terminology. Acronym expansion on first use. Keep numbers + names literal.",
        }
        tone_clause = tone_clauses[target_tone]

        system = (
            f"You are a tone-rewriting assistant. Rewrite the user-"
            f"supplied text in this tone: {tone_clause} "
            f"{structure_clause} Preserve all factual claims, numbers, "
            f"names, and dates LITERALLY - do not paraphrase facts, "
            f"only the surrounding prose. Do not add information not "
            f"in the source. Output only the rewritten text."
        )
        user = (
            "Rewrite the following text:\n\n"
            "--- BEGIN TEXT ---\n"
            f"{text}\n"
            "--- END TEXT ---"
        )
        return system, user
