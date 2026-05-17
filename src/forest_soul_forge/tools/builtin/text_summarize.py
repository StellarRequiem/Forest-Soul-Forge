"""``text_summarize.v1`` — curated LLM summarization (B363).

Wraps ``provider.complete`` with a stable summarization-shaped
system prompt. Returned as a separate tool from llm_think.v1 so
audit events read "text_summarize was invoked" instead of "llm_think
with arbitrary prompt" — the operator's audit-chain readback gains
intent visibility.

Side effects: ``read_only`` — same posture as llm_think.v1.

Used by 5+ catalogued skills today:
  - agent_activity_digest.v1
  - agent_introspect.v1
  - commit_changelog.v1
  - memory_consolidate.v1
  - morning_briefing.v1
  - release_notes.v1
Pre-B363 these skills were dead because text_summarize wasn't in
the catalog. B363 lands the six missing LLM-wrapper tools that
revive the dead-skill set.
"""
from __future__ import annotations

from typing import Any

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin._prompt_template_base import (
    PromptTemplateToolBase,
)

_DEFAULT_TARGET_WORDS = 150
_MAX_TARGET_WORDS = 1500
_VALID_STYLES = {"bullet_points", "paragraph", "tldr"}


class TextSummarizeTool(PromptTemplateToolBase):
    name = "text_summarize"
    version = "1"
    side_effects = "read_only"
    _DEFAULT_MAX_TOKENS = 600
    _TASK_KIND_DEFAULT = "generate"

    description = (
        "Summarize a text blob to a target length and style. Wraps "
        "an LLM call with a stable summarization system prompt; "
        "audit chain logs each invocation as text_summarize so the "
        "operator can see intent, not just 'llm_think was called.'"
    )

    def _validate_specific(self, args: dict[str, Any]) -> None:
        self._validate_text_field(args, "text", required=True)
        tw = args.get("target_words", _DEFAULT_TARGET_WORDS)
        if not isinstance(tw, int) or tw < 10 or tw > _MAX_TARGET_WORDS:
            raise ToolValidationError(
                f"target_words must be int in [10, {_MAX_TARGET_WORDS}]; "
                f"got {tw!r}"
            )
        style = args.get("style", "paragraph")
        if style not in _VALID_STYLES:
            raise ToolValidationError(
                f"style must be one of {sorted(_VALID_STYLES)}; got {style!r}"
            )

    def _build_prompts(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> tuple[str, str]:
        text = args["text"]
        target = int(args.get("target_words", _DEFAULT_TARGET_WORDS))
        style = args.get("style", "paragraph")
        focus = args.get("focus")  # optional emphasis hint

        style_clause = {
            "bullet_points": (
                "Output a bulleted list. Each bullet is one fact or "
                "point. No nested bullets. No preamble."
            ),
            "paragraph": (
                "Output flowing prose. One or two paragraphs. No "
                "bullets, no preamble, no closing remarks."
            ),
            "tldr": (
                "Output a single line - the absolute essential takeaway. "
                "If you cannot fit it in one line, the input is too "
                "complex and you should say so explicitly."
            ),
        }[style]

        focus_clause = ""
        if focus and isinstance(focus, str):
            focus_clause = (
                f"\n\nFocus the summary on: {focus.strip()}. Other "
                "material is context but should not dominate."
            )

        system = (
            f"You are a concise summarization assistant. Your job is to "
            f"compress the user-supplied text to approximately "
            f"{target} words. {style_clause} Preserve numbers, names, "
            f"and dates literally. Do not add information that is not "
            f"present in the source.{focus_clause}"
        )
        user = (
            "Summarize the following text:\n\n"
            "--- BEGIN TEXT ---\n"
            f"{text}\n"
            "--- END TEXT ---"
        )
        return system, user
