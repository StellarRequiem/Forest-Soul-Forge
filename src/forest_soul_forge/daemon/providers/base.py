"""Model provider protocol — the shape every backend must implement.

Forest Soul Forge runs *local-first*: the default provider is whatever
the user is running on their own machine (Ollama, LM Studio, llama.cpp
in server mode). A frontier provider (Anthropic / OpenAI / xAI / private
gateway) is opt-in, never a silent fallback.

See ADR-0008 for the rationale. The short version: if an agent is doing
medical/therapeutic work with user data, that data must not leave the
machine by accident. Remote inference is a disclosure event.

Multi-model routing is first-class here. A single provider can map
different :class:`TaskKind` values to different underlying models — e.g.
a fast 3B model for baseline status checks and a 14B model for
constitution generation. Callers pass ``task_kind`` so the provider can
pick the right horse.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


class TaskKind(str, enum.Enum):
    """Why we're calling a model. Providers use this to pick a model size.

    The values are intentionally coarse. Finer-grained routing (per-agent,
    per-role) happens one layer up, not inside the provider.
    """

    #: Short classification call, low latency required. Examples: detecting
    #: whether a user turn contains a self-harm signal, tagging an audit
    #: event's emotional valence, routing intent.
    CLASSIFY = "classify"

    #: One-shot generation of structured text. Examples: constitution
    #: generation at birth, rendering a soul.md body, synthesizing a lineage
    #: summary. Latency budget is seconds, not tenths.
    GENERATE = "generate"

    #: Second-opinion pass over another model's output. Examples: checking
    #: for refusal-worthy content, verifying a safety-critical response
    #: before delivery. Runs on a small independent model so the larger
    #: model can't rubber-stamp itself.
    SAFETY_CHECK = "safety_check"

    #: Conversational agent runtime — the actual therapy / blue-team / or
    #: companion interaction. Latency must be tight but context budget must
    #: be enough to carry rapport. This is the "middle-sized" slot.
    CONVERSATION = "conversation"

    #: Tool-use / structured-output turn. Examples: emitting a tool call
    #: schema, producing JSON that drives a downstream action. Needs a
    #: model trained on structured output (coder variants tend to win here).
    TOOL_USE = "tool_use"


class ProviderStatus(str, enum.Enum):
    """Coarse health signal surfaced to ``/healthz`` and the frontend."""

    OK = "ok"
    DEGRADED = "degraded"      # reachable but slow, stale, or partially working
    UNREACHABLE = "unreachable"  # cannot connect at all
    DISABLED = "disabled"       # provider exists in code but is turned off


@dataclass(frozen=True)
class ProviderHealth:
    """Snapshot of provider reachability + configured model tags.

    ``details`` is free-form, provider-specific context (e.g. Ollama's
    server version, loaded models list). Not part of any stable contract.
    """

    name: str
    status: ProviderStatus
    base_url: str | None
    models: dict[TaskKind, str]
    details: dict[str, Any]
    error: str | None = None


class ProviderError(Exception):
    """Base class for provider-layer failures."""


class ProviderUnavailable(ProviderError):
    """Raised when the provider is configured-off or unreachable.

    Callers translate this into a clear user-facing message rather than a
    500 — the user can't fix a crash trace, but they can start Ollama.
    """


class ProviderDisabled(ProviderError):
    """Raised when the provider is intentionally turned off by config.

    Distinguished from ``ProviderUnavailable`` so the frontend can show
    "Frontier is disabled by your settings" vs. "Local model is down".
    """


@runtime_checkable
class ModelProvider(Protocol):
    """Protocol every provider satisfies.

    Implementations should be cheap to construct — actual network I/O
    happens in ``complete`` and ``healthcheck``.
    """

    #: Short identifier used in config and API responses. E.g. ``"local"``.
    name: str

    async def complete(
        self,
        prompt: str,
        *,
        task_kind: TaskKind = TaskKind.CONVERSATION,
        system: str | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> str:
        """Return the model's reply as a single string.

        Streaming is intentionally not part of this v1 protocol; add it as
        ``stream_complete`` when a concrete caller needs it, rather than
        guessing the interface upfront.
        """
        ...

    async def healthcheck(self) -> ProviderHealth:
        """Report reachability without making a real generation call.

        Implementations should keep this fast (<1s) and side-effect-free.
        """
        ...
