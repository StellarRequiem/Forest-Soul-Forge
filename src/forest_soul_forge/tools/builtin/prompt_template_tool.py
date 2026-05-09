"""``PromptTemplateTool`` — generic LLM wrapper per ADR-0058 / B202.

Pattern: one Python class, MULTIPLE registered instances. Each instance
binds at construct time to a specific (name, version, description,
input_schema, prompt_template) read from a forged tool spec at
``data/forge/tools/installed/<name>.v<version>.yaml``. The class is
the implementation; the spec is the data. An operator forging a new
"summarize the audit chain" tool gets a fresh PromptTemplateTool
instance registered under ``summarize_audit.v1`` with that operator's
template baked in — no new Python code, no daemon restart.

side_effects = read_only by construction. Input validation against
the spec's input_schema runs at validate(); template substitution
runs at execute() and routes through ``ctx.provider.complete()``
(same path as ``LlmThinkTool``). Output shape is
``{response, model, elapsed_ms}`` — same as llm_think — so callers
that already know how to consume llm_think output can consume any
prompt-template tool.

Template substitution uses str.format(**args). Operators write
templates with ``{var}`` placeholders; missing vars raise
ToolValidationError at execute() with the offending key surfaced.
Anything fancier (conditionals, loops, partials) argues for a real
implementation, not a template — that's the boundary this MVP
deliberately holds.
"""
from __future__ import annotations

import time
from typing import Any

from forest_soul_forge.daemon.providers import TaskKind
from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


DEFAULT_MAX_TOKENS = 800
MAX_PROMPT_LEN = 32_000


class PromptTemplateTool:
    """An llm_think wrapper with a baked-in prompt template.

    Construct one per forged tool. The instance is a Tool (per
    ``forest_soul_forge.tools.base.Tool`` protocol) and registers
    into the same ToolRegistry as builtins. Dispatch flows through
    the standard governance pipeline; the only extra step is
    template substitution before the provider call.
    """

    def __init__(
        self,
        *,
        name: str,
        version: str,
        description: str,
        input_schema: dict[str, Any],
        prompt_template: str,
        archetype_tags: tuple[str, ...] = (),
        forged_by: str | None = None,
    ) -> None:
        # Basic shape checks at construct time so a malformed spec
        # fails at lifespan rather than first dispatch.
        if not name or not isinstance(name, str):
            raise ValueError("name is required and must be a non-empty string")
        if not version or not isinstance(version, str):
            raise ValueError("version is required and must be a non-empty string")
        if not isinstance(prompt_template, str) or not prompt_template.strip():
            raise ValueError("prompt_template is required and must be non-empty")
        if not isinstance(input_schema, dict):
            raise ValueError("input_schema must be a dict (JSONSchema-shaped)")

        self.name = name
        self.version = version
        self.description = description or ""
        self._input_schema = input_schema
        self._prompt_template = prompt_template
        self.archetype_tags = tuple(archetype_tags)
        self.forged_by = forged_by

    # Forest's Tool protocol: side_effects is read at registration
    # time. Prompt-template tools are pure LLM calls — no I/O — so
    # they're always read_only by construction.
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        """Lightweight validation against the spec's input_schema.

        We don't pull in jsonschema as a runtime dep just for this —
        the schemas operators forge are simple (top-level required
        keys + per-key types). For anything richer, the spec lands
        in a follow-up burst with proper JSONSchema enforcement.
        """
        if not isinstance(args, dict):
            raise ToolValidationError(
                f"args must be a dict; got {type(args).__name__}"
            )
        required = self._input_schema.get("required", [])
        for key in required:
            if key not in args:
                raise ToolValidationError(
                    f"missing required arg {key!r} for {self.name}.v{self.version}"
                )
        properties = self._input_schema.get("properties", {})
        for key, schema in properties.items():
            if key not in args:
                continue
            expected_type = schema.get("type")
            if expected_type is None:
                continue
            value = args[key]
            type_ok = (
                (expected_type == "string" and isinstance(value, str))
                or (expected_type == "integer" and isinstance(value, int)
                    and not isinstance(value, bool))
                or (expected_type == "number" and isinstance(value, (int, float))
                    and not isinstance(value, bool))
                or (expected_type == "boolean" and isinstance(value, bool))
                or (expected_type == "array" and isinstance(value, list))
                or (expected_type == "object" and isinstance(value, dict))
            )
            if not type_ok:
                raise ToolValidationError(
                    f"arg {key!r} expected type {expected_type!r}, "
                    f"got {type(value).__name__}"
                )
            # min/max for numbers, simple length for strings/arrays.
            if expected_type in ("integer", "number"):
                lo = schema.get("minimum")
                hi = schema.get("maximum")
                if lo is not None and value < lo:
                    raise ToolValidationError(
                        f"arg {key!r}={value} below minimum {lo}"
                    )
                if hi is not None and value > hi:
                    raise ToolValidationError(
                        f"arg {key!r}={value} above maximum {hi}"
                    )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        provider = ctx.provider
        if provider is None:
            raise ToolValidationError(
                f"{self.name}.v{self.version}: no LLM provider wired into "
                "this dispatcher. Either the daemon was built without a "
                "provider, or the active provider is offline (check "
                "GET /runtime/provider)."
            )

        # Template substitution. KeyError surfaces as a ToolValidation
        # error pointing at the missing template variable so an
        # operator forging a tool that referenced a typo'd var name
        # gets a clean error rather than a cryptic stack trace.
        try:
            prompt = self._prompt_template.format(**args)
        except KeyError as e:
            missing = str(e).strip("'\"")
            raise ToolValidationError(
                f"{self.name}.v{self.version}: prompt_template references "
                f"variable {{{missing}}} but no arg of that name was provided. "
                f"Spec input_schema lists: {sorted(self._input_schema.get('properties', {}).keys())}"
            ) from e
        except Exception as e:
            # IndexError on positional placeholders, ValueError on
            # malformed format strings, etc. Surface as a clean error.
            raise ToolValidationError(
                f"{self.name}.v{self.version}: prompt_template substitution "
                f"failed: {type(e).__name__}: {e}"
            ) from e

        # Hard cap on rendered prompt length — same guard llm_think uses.
        if len(prompt) > MAX_PROMPT_LEN:
            raise ToolValidationError(
                f"{self.name}.v{self.version}: rendered prompt too long "
                f"({len(prompt)} chars > {MAX_PROMPT_LEN} max). Either "
                "shrink the template or split the call."
            )

        # Honor task_caps if set, same as llm_think.
        max_tokens = DEFAULT_MAX_TOKENS
        usage_cap = ctx.constraints.get("usage_cap_tokens")
        if isinstance(usage_cap, int) and usage_cap > 0 and usage_cap < max_tokens:
            max_tokens = usage_cap

        # Default system prompt — the agent speaks in its role's voice.
        sysprompt = (
            f"You are running the {self.name}.v{self.version} prompt-template "
            f"tool for an agent in the Forest Soul Forge runtime. The "
            "operator forged this tool from a description; the prompt above "
            "is the rendered template with the agent's args substituted in. "
            "Respond directly in the format the template asks for."
        )

        t0 = time.monotonic()
        response = await provider.complete(
            prompt,
            system=sysprompt,
            task_kind=TaskKind.CONVERSATION,
            max_tokens=max_tokens,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        return ToolResult(
            output={
                "response": response,
                "model": getattr(provider, "name", "unknown"),
                "elapsed_ms": elapsed_ms,
            },
            tokens_used=None,  # provider.complete return doesn't expose;
                              # B197 result_digest path will pick it up if
                              # the provider returns structured output.
            cost_usd=None,
        )
