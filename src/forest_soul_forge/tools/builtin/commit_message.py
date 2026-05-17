"""``commit_message.v1`` — curated LLM commit-message generation (B363).

Wraps ``provider.complete`` with a stable commit-message system
prompt that mirrors Forest-Soul-Forge's commit conventions (see
CLAUDE.md 'make commits followable'). Skills referencing this:
  - commit_changelog.v1
  - release_notes.v1
"""
from __future__ import annotations

from typing import Any

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin._prompt_template_base import (
    PromptTemplateToolBase,
)

_VALID_FORMATS = {"conventional", "imperative", "plain"}


class CommitMessageTool(PromptTemplateToolBase):
    name = "commit_message"
    version = "1"
    side_effects = "read_only"
    _DEFAULT_MAX_TOKENS = 400
    _TASK_KIND_DEFAULT = "generate"

    description = (
        "Generate a commit message from a diff or change summary. "
        "Output is text only - tool NEVER runs git commands. Used "
        "by commit-changelog and release-notes skills."
    )

    def _validate_specific(self, args: dict[str, Any]) -> None:
        self._validate_text_field(args, "diff", required=True)
        fmt = args.get("format", "conventional")
        if fmt not in _VALID_FORMATS:
            raise ToolValidationError(
                f"format must be one of {sorted(_VALID_FORMATS)}; "
                f"got {fmt!r}"
            )
        scope = args.get("scope")
        if scope is not None:
            if not isinstance(scope, str) or len(scope) > 100:
                raise ToolValidationError(
                    "scope must be a string up to 100 chars when provided"
                )

    def _build_prompts(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> tuple[str, str]:
        diff = args["diff"]
        fmt = args.get("format", "conventional")
        scope = args.get("scope")
        ticket = args.get("ticket")  # optional bug/task reference

        format_clause = {
            "conventional": (
                "Conventional Commits format: '<type>(<scope>): <subject>'. "
                "Type one of: feat, fix, docs, style, refactor, perf, "
                "test, chore. Subject ≤ 72 chars, imperative mood, no "
                "trailing period."
            ),
            "imperative": (
                "Imperative subject line ≤ 72 chars, no trailing period, "
                "no type prefix. Body paragraphs below the subject "
                "explain why."
            ),
            "plain": (
                "Plain prose. One subject line, then optional body "
                "paragraphs. No conventional-commit type prefix."
            ),
        }[fmt]

        scope_clause = (
            f" Use scope: {scope}." if scope else
            " Infer scope from the diff (the most-touched module)."
            if fmt == "conventional" else ""
        )
        ticket_clause = (
            f" Reference ticket/task: {ticket}." if ticket
            and isinstance(ticket, str) else ""
        )

        system = (
            f"You are a commit-message assistant. Read the diff and "
            f"write a commit message. {format_clause}{scope_clause} "
            f"Body (if present) explains WHY in 1-3 short paragraphs. "
            f"No emojis. No 'this commit' phrasing - use the imperative."
            f"{ticket_clause} Output ONLY the commit message text - "
            f"no markdown fence, no preamble."
        )
        user = (
            "Write a commit message for the following diff:\n\n"
            "--- BEGIN DIFF ---\n"
            f"{diff}\n"
            "--- END DIFF ---"
        )
        return system, user
