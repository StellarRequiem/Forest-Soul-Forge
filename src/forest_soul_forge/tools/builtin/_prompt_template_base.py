"""Shared base for curated-prompt LLM tools (B363).

Six tools registered with the catalog (text_summarize, code_explain,
email_draft, commit_message, action_items_extract, tone_shift) are
all thin wrappers over an LLM provider call: each defines its own
input schema + system prompt template + user prompt assembler, then
calls ``provider.complete`` and returns the response.

The duplicated boilerplate (provider lookup, max_tokens clamping,
elapsed_ms timing, token-count estimation, ToolResult assembly)
lives here once. Subclasses only declare the prompt-curation
specifics. Same audit shape as ``llm_think.v1``: each call lands as
its own ``tool_call_dispatched`` -> ``tool_call_succeeded`` event,
so the operator can audit "which curated tool ran when" rather than
seeing every LLM call homogenized under ``llm_think``.

Side effects: ``read_only`` for the whole family. The LLM call
produces no external mutation; cost is compute (tokens) only. This
lets Guardian-genre agents reach for these tools without per-call
human approval, same posture as llm_think.v1 (ADR-0033 design
decision: read_only LLM tools are routinely callable).

Pattern (per subclass):
  1. Set class attributes ``name``, ``version`` ("1"), ``side_effects``
     ("read_only"), ``description``, ``_DEFAULT_MAX_TOKENS``,
     ``_TASK_KIND_DEFAULT``.
  2. Override ``_validate_specific(args)`` to enforce the per-tool
     input schema. The base ``validate`` clamps max_tokens range
     and ensures provider-available types; subclasses extend.
  3. Override ``_build_prompts(args, ctx) -> (system, user)`` to
     assemble the curated prompts from validated args.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

from forest_soul_forge.daemon.providers import TaskKind
from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)

_TASK_KIND_MAP: dict[str, TaskKind] = {
    "classify":      TaskKind.CLASSIFY,
    "generate":      TaskKind.GENERATE,
    "safety_check":  TaskKind.SAFETY_CHECK,
    "conversation":  TaskKind.CONVERSATION,
    "tool_use":      TaskKind.TOOL_USE,
}

# Cap text-input size at 16k chars across the family. Smaller than
# llm_think's 32k because these tools take a single substantive text
# blob (a document to summarize, a diff to write a message about);
# the per-tool prompt template adds further framing on top.
_MAX_TEXT_LEN = 16_000
_MIN_TEXT_LEN = 1


class PromptTemplateToolBase:
    """Mixin-style base. Each subclass IS a Tool (registered into the
    catalog), not an instance of this class — the registry calls
    ``validate`` and ``execute`` directly on the class instance, so
    subclasses inherit both."""

    # Set by subclasses. Sentinels so a subclass that forgets one
    # raises at registration time, not at first call.
    name: str = ""
    version: str = "1"
    side_effects: str = "read_only"

    # Subclass-tunable.
    _DEFAULT_MAX_TOKENS: int = 600
    _TASK_KIND_DEFAULT: str = "conversation"

    # ----- subclass hooks (override these) ------------------------------

    def _validate_specific(self, args: dict[str, Any]) -> None:
        """Per-tool input validation. Subclasses extend; default is
        a no-op so the base ``validate`` works on its own."""
        return None

    def _build_prompts(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> tuple[str, str]:
        """Return (system_prompt, user_prompt) from validated args.
        Subclasses MUST override; this base implementation raises so
        a misconfigured subclass surfaces immediately."""
        raise NotImplementedError(
            f"{type(self).__name__} must override _build_prompts"
        )

    # ----- base validate (shared) ---------------------------------------

    def validate(self, args: dict[str, Any]) -> None:
        # max_tokens clamp - inherited across the family.
        max_tokens = args.get("max_tokens", self._DEFAULT_MAX_TOKENS)
        if not isinstance(max_tokens, int) or max_tokens < 1 or max_tokens > 8192:
            raise ToolValidationError(
                f"max_tokens must be an integer in [1, 8192]; "
                f"got {max_tokens!r}"
            )
        # task_kind clamp.
        task_kind = args.get("task_kind", self._TASK_KIND_DEFAULT)
        if task_kind not in _TASK_KIND_MAP:
            raise ToolValidationError(
                f"task_kind must be one of {sorted(_TASK_KIND_MAP)}; "
                f"got {task_kind!r}"
            )
        # temperature clamp.
        temp = args.get("temperature")
        if temp is not None:
            if not isinstance(temp, (int, float)) or temp < 0 or temp > 2:
                raise ToolValidationError(
                    f"temperature must be a number in [0, 2]; got {temp!r}"
                )
        # Per-tool checks last so any specific error from the subclass
        # gets the operator's attention (more actionable than a
        # generic max_tokens error).
        self._validate_specific(args)

    def _validate_text_field(
        self, args: dict[str, Any], field: str, required: bool = True,
    ) -> str | None:
        """Helper subclasses use to validate a text-input field with
        the shared 1..16k char rule. Returns the value (or None if
        not required and absent)."""
        val = args.get(field)
        if val is None:
            if required:
                raise ToolValidationError(f"{field} is required")
            return None
        if not isinstance(val, str):
            raise ToolValidationError(
                f"{field} must be a string; got {type(val).__name__}"
            )
        n = len(val)
        if n < _MIN_TEXT_LEN:
            raise ToolValidationError(f"{field} must not be empty")
        if n > _MAX_TEXT_LEN:
            raise ToolValidationError(
                f"{field} too long ({n} chars > {_MAX_TEXT_LEN} max); "
                f"split into smaller calls or summarize first"
            )
        return val

    # ----- base execute (shared) ----------------------------------------

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        provider = ctx.provider
        if provider is None:
            raise ToolValidationError(
                f"{self.name}.v{self.version}: no LLM provider wired "
                "into this dispatcher. Either the daemon was built "
                "without a provider, or the active provider is offline "
                "(check GET /runtime/provider)."
            )

        system_prompt, user_prompt = self._build_prompts(args, ctx)

        max_tokens: int = int(args.get("max_tokens", self._DEFAULT_MAX_TOKENS))
        # Honor per-task usage_cap (same pattern as llm_think.v1).
        usage_cap = ctx.constraints.get("usage_cap_tokens")
        if isinstance(usage_cap, int) and usage_cap > 0 and usage_cap < max_tokens:
            max_tokens = usage_cap

        task_kind_str: str = args.get("task_kind", self._TASK_KIND_DEFAULT)
        task_kind = _TASK_KIND_MAP[task_kind_str]
        temperature = args.get("temperature")
        passthrough: dict[str, Any] = {}
        if temperature is not None:
            passthrough["options"] = {"temperature": float(temperature)}

        t0 = time.perf_counter()
        try:
            response = await provider.complete(
                user_prompt,
                task_kind=task_kind,
                system=system_prompt,
                max_tokens=max_tokens,
                **passthrough,
            )
        except Exception as e:
            raise ToolValidationError(
                f"provider.complete failed: {e!r}"
            ) from e
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        model_tag = "unknown"
        models = getattr(provider, "models", None) or getattr(provider, "_models", None)
        if isinstance(models, dict):
            model_tag = models.get(task_kind) or models.get(TaskKind.CONVERSATION) or "unknown"

        # Rough token estimate for audit accounting (same heuristic
        # as llm_think.v1).
        words = (
            len(user_prompt.split()) + len(system_prompt.split())
            + len(response.split())
        )
        tokens_estimate = int(words * 1.3)

        prompt_hash = hashlib.sha256(user_prompt.encode("utf-8")).hexdigest()[:16]

        return ToolResult(
            output={
                "response":   response,
                "model":      model_tag,
                "task_kind":  task_kind_str,
                "elapsed_ms": elapsed_ms,
            },
            metadata={
                "tool":             self.name,
                "prompt_hash":      prompt_hash,
                "prompt_chars":     len(user_prompt),
                "response_chars":   len(response),
                "system_chars":     len(system_prompt),
                "max_tokens":       max_tokens,
                "temperature":      temperature,
                "usage_cap_clipped": isinstance(usage_cap, int) and usage_cap > 0,
            },
            tokens_used=tokens_estimate,
            cost_usd=None,
            side_effect_summary=(
                f"{self.name}: model={model_tag} task={task_kind_str} "
                f"input={len(user_prompt)}c output={len(response)}c "
                f"elapsed={elapsed_ms}ms"
            ),
        )
