"""``email_draft.v1`` — curated LLM email drafting (B363).

Wraps ``provider.complete`` with a stable email-draft system prompt.
Skills referencing this:
  - bug_report_polish.v1
  - meeting_followup.v1
"""
from __future__ import annotations

from typing import Any

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin._prompt_template_base import (
    PromptTemplateToolBase,
)

_VALID_TONES = {"formal", "friendly", "direct", "apologetic", "neutral"}
_VALID_LENGTHS = {"short", "medium", "long"}


class EmailDraftTool(PromptTemplateToolBase):
    name = "email_draft"
    version = "1"
    side_effects = "read_only"
    _DEFAULT_MAX_TOKENS = 700
    _TASK_KIND_DEFAULT = "generate"

    description = (
        "Draft an email body from intent + audience + tone. Output "
        "is a draft for operator review - tool NEVER sends the "
        "email itself (no network side-effects). Used by bug-report "
        "polish and meeting-followup skills."
    )

    def _validate_specific(self, args: dict[str, Any]) -> None:
        self._validate_text_field(args, "intent", required=True)
        # Recipient + sender are short identity strings, not the big
        # text field - validate separately.
        for field in ("recipient", "sender"):
            v = args.get(field)
            if v is not None:
                if not isinstance(v, str) or len(v) > 200:
                    raise ToolValidationError(
                        f"{field} must be a string up to 200 chars when provided"
                    )
        tone = args.get("tone", "neutral")
        if tone not in _VALID_TONES:
            raise ToolValidationError(
                f"tone must be one of {sorted(_VALID_TONES)}; got {tone!r}"
            )
        length = args.get("length", "medium")
        if length not in _VALID_LENGTHS:
            raise ToolValidationError(
                f"length must be one of {sorted(_VALID_LENGTHS)}; "
                f"got {length!r}"
            )

    def _build_prompts(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> tuple[str, str]:
        intent = args["intent"]
        recipient = args.get("recipient")
        sender = args.get("sender")
        tone = args.get("tone", "neutral")
        length = args.get("length", "medium")
        context = args.get("context")

        length_clause = {
            "short": "2-4 sentences. No greeting beyond a one-line opener.",
            "medium": "3-6 short paragraphs. Standard greeting and sign-off.",
            "long": "Up to one screen of text. Reserve for situations that genuinely require detail.",
        }[length]

        recipient_clause = (
            f" Addressed to: {recipient}." if recipient else ""
        )
        sender_clause = (
            f" Signed from: {sender}." if sender else ""
        )
        context_clause = (
            f"\n\nAdditional context the email should reflect: "
            f"{context.strip()}"
            if context and isinstance(context, str) else ""
        )

        system = (
            f"You are an email-drafting assistant. Draft an email "
            f"body in a {tone} tone, {length_clause}"
            f"{recipient_clause}{sender_clause} Produce the email "
            f"text only - no subject line unless the operator's "
            f"intent calls for it. No preamble explaining what the "
            f"email does. Do not invent facts not in the intent or "
            f"context.{context_clause}"
        )
        user = (
            f"Draft an email expressing this intent:\n\n"
            f"--- BEGIN INTENT ---\n"
            f"{intent}\n"
            f"--- END INTENT ---"
        )
        return system, user
