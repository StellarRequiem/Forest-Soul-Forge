"""``llm_think.v1`` — audited LLM completion as a dispatchable tool.

The bridge tool that turns Forest agents from "metadata + audit trail"
into "agents you can ask things of." Every call:

  1. Goes through the governance pipeline (constitution constraints,
     genre kit-tier ceiling, per-task task_caps, hardware quarantine).
  2. Lands in the audit chain as ``tool_call_dispatched`` →
     ``tool_call_succeeded`` (or ``tool_call_failed``).
  3. Reports tokens used so per-session counters work and operators
     can see real cost.

Side effects: ``read_only``. The LLM call itself produces no external
mutation — it spends compute (tokens), nothing more. That keeps the
tool runnable inside Guardian-genre agents (read-only ceiling) so
the Reviewer in a coding triune can think out loud without needing
human approval per call.

Why this is "the bridge": Forest agents have personality (traits),
documented stance (constitution + soul.md voice), and audit trail.
What they LACKED until now was an in-runtime way to actually use
their LLM. ``llm_think.v1`` is the smallest possible action that
gives them that. Once the conversation runtime (ADR-003Y / Y-track)
ships, agents will chain llm_think calls themselves; for now, the
operator drives it.

Future evolution:
  - v2: streaming response (so callers can watch the agent think)
  - v2: optional retrieval — pull recent memory.recall results into
        the prompt as context
  - v2: tool-use mode — let the model emit follow-up tool_call requests
        the dispatcher can execute (true reasoning loop, post Y-track)
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

DEFAULT_MAX_TOKENS = 800
MAX_PROMPT_LEN = 32_000  # cheap guard against accidentally pasting a huge file
MIN_PROMPT_LEN = 1
DEFAULT_TASK_KIND = "conversation"

# Maps the operator-facing string to the TaskKind enum the provider
# expects. Same values the constitution constraint policy keys off,
# so a tool call with task_kind="generate" lands on the model the
# operator picked for generation tasks (FSF_LOCAL_MODEL_GENERATE).
_TASK_KIND_MAP: dict[str, TaskKind] = {
    "classify":      TaskKind.CLASSIFY,
    "generate":      TaskKind.GENERATE,
    "safety_check":  TaskKind.SAFETY_CHECK,
    "conversation":  TaskKind.CONVERSATION,
    "tool_use":      TaskKind.TOOL_USE,
}


class LlmThinkTool:
    """Args:
      prompt (str, required): the user/operator question or instruction.
        1 ≤ length ≤ 32000.
      system (str, optional): system prompt. If omitted, a default is
        generated from the agent's role + genre so the model knows
        whose voice it's speaking in.
      max_tokens (int, optional): upper bound on response length.
        Default 800; clamped to whatever the agent's task_caps allow.
      task_kind (str, optional): which model to route to. One of
        classify/generate/safety_check/conversation/tool_use.
        Default "conversation". Lets operators send heavy reasoning
        to a different model than chitchat without changing the call
        site.
      temperature (float, optional): passthrough to the provider.
        Default left to provider.

    Output:
      {
        "response": str,         # the model's text
        "model": str,            # the resolved model tag (e.g. "qwen2.5-coder:7b")
        "task_kind": str,        # the task_kind that was used
        "elapsed_ms": int,       # wall-clock time spent in provider.complete
      }
    """

    name = "llm_think"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        prompt = args.get("prompt")
        if not isinstance(prompt, str):
            raise ToolValidationError(
                f"prompt is required and must be a string; got {type(prompt).__name__}"
            )
        n = len(prompt)
        if n < MIN_PROMPT_LEN:
            raise ToolValidationError(
                f"prompt must be at least {MIN_PROMPT_LEN} char; got empty"
            )
        if n > MAX_PROMPT_LEN:
            raise ToolValidationError(
                f"prompt too long ({n} chars > {MAX_PROMPT_LEN} max); "
                f"split into smaller calls or summarize first"
            )
        sysprompt = args.get("system")
        if sysprompt is not None and not isinstance(sysprompt, str):
            raise ToolValidationError(
                f"system must be a string when provided; got {type(sysprompt).__name__}"
            )
        max_tokens = args.get("max_tokens", DEFAULT_MAX_TOKENS)
        if not isinstance(max_tokens, int) or max_tokens < 1 or max_tokens > 8192:
            raise ToolValidationError(
                f"max_tokens must be an integer in [1, 8192]; got {max_tokens!r}"
            )
        task_kind = args.get("task_kind", DEFAULT_TASK_KIND)
        if task_kind not in _TASK_KIND_MAP:
            raise ToolValidationError(
                f"task_kind must be one of {sorted(_TASK_KIND_MAP)}; got {task_kind!r}"
            )
        temp = args.get("temperature")
        if temp is not None:
            if not isinstance(temp, (int, float)) or temp < 0 or temp > 2:
                raise ToolValidationError(
                    f"temperature must be a number in [0, 2]; got {temp!r}"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        provider = ctx.provider
        if provider is None:
            raise ToolValidationError(
                "llm_think.v1: no LLM provider wired into this dispatcher. "
                "Either the daemon was built without a provider, or the "
                "active provider is offline (check GET /runtime/provider)."
            )

        prompt: str = args["prompt"]
        sysprompt: str | None = args.get("system")
        max_tokens: int = int(args.get("max_tokens", DEFAULT_MAX_TOKENS))
        task_kind_str: str = args.get("task_kind", DEFAULT_TASK_KIND)
        task_kind = _TASK_KIND_MAP[task_kind_str]
        temperature = args.get("temperature")

        # Honor the per-task usage_cap (T2.2b) if the operator set one
        # — it can shrink max_tokens but never grow it.
        usage_cap = ctx.constraints.get("usage_cap_tokens")
        if isinstance(usage_cap, int) and usage_cap > 0 and usage_cap < max_tokens:
            max_tokens = usage_cap

        # Default system prompt — gives the model agent identity so its
        # response is in-character. Operators who want a different
        # framing pass `system` explicitly.
        if sysprompt is None:
            sysprompt = _default_system_prompt(ctx)

        # Build the provider passthrough kwargs. Only include
        # temperature when the caller set it (None means "provider
        # default", which differs across providers).
        passthrough: dict[str, Any] = {}
        if temperature is not None:
            passthrough["options"] = {"temperature": float(temperature)}

        t0 = time.perf_counter()
        try:
            response = await provider.complete(
                prompt,
                task_kind=task_kind,
                system=sysprompt,
                max_tokens=max_tokens,
                **passthrough,
            )
        except Exception as e:
            # The dispatcher will turn this into a tool_call_failed
            # event with reason="provider_error" — we re-raise
            # ToolValidationError so it routes through the same path
            # as bad_args, which keeps the audit shape uniform.
            raise ToolValidationError(
                f"provider.complete failed: {e!r}"
            ) from e
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        # Resolve the model tag the provider actually used. The
        # LocalProvider exposes .models (dict[TaskKind, str]); other
        # providers might not — fall back to "unknown" so a future
        # provider that drops .models doesn't crash this tool.
        model_tag = "unknown"
        models = getattr(provider, "models", None) or getattr(provider, "_models", None)
        if isinstance(models, dict):
            model_tag = models.get(task_kind) or models.get(TaskKind.CONVERSATION) or "unknown"

        # Approximate token count for the audit row. We don't have
        # provider-reported token counts threaded through yet
        # (LocalProvider.complete returns a string, not a usage
        # struct), so we estimate input + output via word count × 1.3
        # (a standard rough heuristic). Replace with real counts when
        # the provider interface grows a usage return.
        tokens_estimate = _estimate_tokens(prompt, sysprompt or "", response)

        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]

        return ToolResult(
            output={
                "response":   response,
                "model":      model_tag,
                "task_kind":  task_kind_str,
                "elapsed_ms": elapsed_ms,
            },
            metadata={
                "prompt_hash":     prompt_hash,
                "prompt_chars":    len(prompt),
                "response_chars":  len(response),
                "system_chars":    len(sysprompt or ""),
                "max_tokens":      max_tokens,
                "temperature":     temperature,
                "usage_cap_clipped": isinstance(usage_cap, int) and usage_cap > 0,
            },
            tokens_used=tokens_estimate,
            cost_usd=None,  # local providers are zero-cost
            side_effect_summary=(
                f"llm_think: model={model_tag} task={task_kind_str} "
                f"prompt={len(prompt)}c response={len(response)}c "
                f"elapsed={elapsed_ms}ms"
            ),
        )


def _default_system_prompt(ctx: ToolContext) -> str:
    """Build a minimal system prompt that gives the model the agent's
    identity. Operators who need richer framing pass `system` directly.

    The prompt is deliberately short — the model already has the
    constitution-derived voice baked into the soul.md at birth time;
    we don't need to re-describe every trait. We just remind it of
    role + genre so it stays in-character for this turn.
    """
    parts = [f"You are a Forest Soul Forge agent with role={ctx.role!r}."]
    if ctx.genre:
        parts.append(f"Your genre is {ctx.genre!r}.")
    parts.append("Respond directly and substantively. No preamble, no excess hedging.")
    return " ".join(parts)


def _estimate_tokens(prompt: str, system: str, response: str) -> int:
    """Rough token count estimate for audit accounting.

    Word-count × 1.3 is a standard heuristic that's roughly correct
    for English text across most BPE tokenizers. Real per-call counts
    require the provider to return a usage object — when LocalProvider
    grows that, replace this with the reported value.
    """
    words = (
        len(prompt.split()) +
        len(system.split()) +
        len(response.split())
    )
    return int(words * 1.3)
