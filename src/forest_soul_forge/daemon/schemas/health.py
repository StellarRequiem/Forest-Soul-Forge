"""Provider health + runtime provider switch + ad-hoc generation endpoint.

Split out of the original 1139-line monolithic ``schemas.py`` by R1
of the post-marathon roadmap. Re-exported via ``schemas/__init__.py``
so existing ``from forest_soul_forge.daemon.schemas import X`` imports
keep working — this is purely an organizational refactor.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from forest_soul_forge.daemon.providers import ProviderStatus, TaskKind


class ProviderHealthOut(BaseModel):
    name: str
    status: ProviderStatus
    base_url: str | None = None
    models: dict[TaskKind, str] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None

class StartupDiagnostic(BaseModel):
    """One lifespan-load attempt's outcome.

    Surfaced on /healthz so an operator can tell — at a glance — which
    write-path components actually loaded vs. silently fell to None and
    will 503 their dependent endpoints. Without this, a load failure
    looks like a misleading "trait engine not available" message
    further down the request flow.
    """

    component: str
    status: str  # "ok" | "failed"
    path: str | None = None
    error: str | None = None

class HealthOut(BaseModel):
    ok: bool
    schema_version: int
    canonical_contract: str
    active_provider: str
    provider: ProviderHealthOut
    # Surfaced so the frontend knows whether to prompt for X-FSF-Token.
    # True when the daemon has api_token configured; False when writes
    # are open (dev default).
    auth_required: bool = False
    # True when allow_write_endpoints is on; False when the daemon is
    # configured read-only. Lets the frontend disable the "Birth" /
    # "Spawn" / "Archive" buttons with a clear reason rather than waiting
    # for a 403 at submit time.
    writes_enabled: bool = True
    # Per-component lifespan diagnostics. Empty when allow_write_endpoints
    # is False (no load attempts made).
    startup_diagnostics: list[StartupDiagnostic] = Field(default_factory=list)

class ProviderInfoOut(BaseModel):
    active: str
    default: str
    known: list[str]
    health: ProviderHealthOut

class SetProviderIn(BaseModel):
    provider: str = Field(..., description="Provider name to activate (e.g. 'local' or 'frontier').")

class GenerateRequest(BaseModel):
    """Inbound payload for an arbitrary completion call against the active provider.

    Phase 4 first-slice surface — exists primarily so external integrations
    (Telegram bots, scripted experiments, future audit-chain enrichment
    hooks) can route completions through the daemon's provider stack
    instead of calling the API SDKs directly. Internally it's still
    ``providers.active().complete(...)``; this is purely the HTTP wrapper.

    Bounds on ``max_tokens`` and ``temperature`` are conservative caps,
    not provider-faithful limits — every backend has its own ceiling. The
    point is to fail loudly at the daemon edge rather than push a wildly
    large request to a frontier provider and wear an unexpected token bill.
    """

    prompt: str = Field(..., min_length=1, description="User prompt sent to the model.")
    system: str | None = Field(
        default=None,
        description="Optional system prompt prepended by the provider (Ollama wraps it as 'system'; OpenAI-compat wraps it as a system-role message).",
    )
    task_kind: TaskKind = Field(
        default=TaskKind.CONVERSATION,
        description="Routing hint — providers map this to a model tag (see ADR-0008 + DaemonSettings).",
    )
    max_tokens: int | None = Field(
        default=None,
        ge=1,
        le=8192,
        description="Cap on response tokens. Provider-specific (Ollama: num_predict; OpenAI: max_tokens).",
    )
    temperature: float | None = Field(
        default=None,
        ge=0.0,
        le=2.0,
        description="Sampling temperature. Passed through to provider as 'temperature'.",
    )

class GenerateResponse(BaseModel):
    """Reply payload — the raw model text plus enough metadata to debug routing."""

    response: str = Field(..., description="The model's reply, untouched.")
    provider: str = Field(..., description="Active provider name when the call ran (e.g. 'local').")
    model: str = Field(..., description="Resolved model tag for the requested task_kind. May be 'unknown' if the provider doesn't expose its model map.")
    task_kind: TaskKind
