"""``action_items_extract.v1`` — curated LLM action-item extraction (B363).

Wraps ``provider.complete`` with a stable extraction system prompt
that asks the model to enumerate ownable tasks from a text blob
(meeting transcript, conversation log, document). Skills:
  - meeting_followup.v1
"""
from __future__ import annotations

from typing import Any

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin._prompt_template_base import (
    PromptTemplateToolBase,
)

_MAX_LIMIT = 50


class ActionItemsExtractTool(PromptTemplateToolBase):
    name = "action_items_extract"
    version = "1"
    side_effects = "read_only"
    _DEFAULT_MAX_TOKENS = 700
    _TASK_KIND_DEFAULT = "classify"

    description = (
        "Extract ownable action items from a text blob (meeting "
        "transcript, conversation log, document). Returns a "
        "structured list. Used by the meeting-followup skill."
    )

    def _validate_specific(self, args: dict[str, Any]) -> None:
        self._validate_text_field(args, "text", required=True)
        limit = args.get("limit", 20)
        if not isinstance(limit, int) or limit < 1 or limit > _MAX_LIMIT:
            raise ToolValidationError(
                f"limit must be int in [1, {_MAX_LIMIT}]; got {limit!r}"
            )
        require_owner = args.get("require_owner", False)
        if not isinstance(require_owner, bool):
            raise ToolValidationError(
                f"require_owner must be a boolean; got {type(require_owner).__name__}"
            )

    def _build_prompts(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> tuple[str, str]:
        text = args["text"]
        limit = int(args.get("limit", 20))
        require_owner = bool(args.get("require_owner", False))

        owner_clause = (
            "Every item MUST name an owner. Drop any item where no "
            "owner is identifiable in the source."
            if require_owner else
            "Name an owner when one is identifiable in the source; "
            "use 'UNASSIGNED' otherwise."
        )

        system = (
            f"You are an action-item extraction assistant. Read the "
            f"user-supplied text and enumerate at most {limit} action "
            f"items. {owner_clause} For each item produce a line in "
            f"this exact shape:\n"
            f"  - [OWNER] action verb + concrete deliverable + due "
            f"(optional)\n"
            f"Example: '- [Alex] review B359 PR before Friday'\n"
            f"Sort by source-order. Omit non-actionable content "
            f"(announcements, status updates, opinions). If no action "
            f"items, output exactly: 'No action items identified.'"
        )
        user = (
            "Extract action items from the following text:\n\n"
            "--- BEGIN TEXT ---\n"
            f"{text}\n"
            "--- END TEXT ---"
        )
        return system, user
