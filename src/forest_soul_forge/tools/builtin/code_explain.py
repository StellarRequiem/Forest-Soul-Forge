"""``code_explain.v1`` — curated LLM code-explanation (B363).

Wraps ``provider.complete`` with a stable code-explanation system
prompt. Skills referencing this:
  - bug_report_polish.v1
  - code_review_quick.v1
"""
from __future__ import annotations

from typing import Any

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin._prompt_template_base import (
    PromptTemplateToolBase,
)

_VALID_AUDIENCES = {"novice", "peer", "expert"}


class CodeExplainTool(PromptTemplateToolBase):
    name = "code_explain"
    version = "1"
    side_effects = "read_only"
    _DEFAULT_MAX_TOKENS = 800
    _TASK_KIND_DEFAULT = "generate"

    description = (
        "Explain what a code snippet does. Audience-tunable "
        "(novice/peer/expert). Reading-only; never modifies the "
        "code. Used by code review and bug-report polishing skills."
    )

    def _validate_specific(self, args: dict[str, Any]) -> None:
        self._validate_text_field(args, "code", required=True)
        audience = args.get("audience", "peer")
        if audience not in _VALID_AUDIENCES:
            raise ToolValidationError(
                f"audience must be one of {sorted(_VALID_AUDIENCES)}; "
                f"got {audience!r}"
            )
        language = args.get("language")
        if language is not None and not isinstance(language, str):
            raise ToolValidationError(
                f"language must be a string when provided; "
                f"got {type(language).__name__}"
            )

    def _build_prompts(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> tuple[str, str]:
        code = args["code"]
        audience = args.get("audience", "peer")
        language = args.get("language")
        focus = args.get("focus")

        audience_clause = {
            "novice": (
                "Assume the reader knows general programming but not "
                "this specific language or library. Define jargon "
                "inline the first time you use it."
            ),
            "peer": (
                "Assume the reader is a working engineer fluent in "
                "the language and likely-familiar libraries. Skip "
                "obvious-syntax explanation; focus on intent and "
                "non-obvious behavior."
            ),
            "expert": (
                "Assume the reader is an expert. Focus exclusively on "
                "non-obvious behavior, hidden costs, edge cases, and "
                "alternative idioms. Skip anything they can read at a "
                "glance."
            ),
        }[audience]

        lang_clause = (
            f" The code is {language}." if language else ""
        )
        focus_clause = (
            f"\n\nFocus the explanation on: {focus.strip()}." if focus
            and isinstance(focus, str) else ""
        )

        system = (
            f"You are a code-explanation assistant. Explain what the "
            f"user-supplied code does.{lang_clause} {audience_clause} "
            f"Structure: (1) one-line summary, (2) flow walkthrough, "
            f"(3) gotchas/edge-cases. No code suggestions, no "
            f"refactoring proposals - explanation only.{focus_clause}"
        )
        user = (
            "Explain the following code:\n\n"
            "--- BEGIN CODE ---\n"
            f"{code}\n"
            "--- END CODE ---"
        )
        return system, user
