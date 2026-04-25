"""Pydantic response / request schemas for the daemon.

Registry row dataclasses are the source-of-truth shape; these schemas
are thin mirrors used so FastAPI can emit OpenAPI and validate payloads.
Keep them 1:1 with the registry dataclasses — drift here is a bug.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from forest_soul_forge.daemon.providers import ProviderStatus, TaskKind


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------
class AgentOut(BaseModel):
    instance_id: str
    dna: str
    dna_full: str
    role: str
    agent_name: str
    parent_instance: str | None = None
    owner_id: str | None = None
    model_name: str | None = None
    model_version: str | None = None
    soul_path: str
    constitution_path: str
    constitution_hash: str
    created_at: str
    status: str
    legacy_minted: bool
    sibling_index: int = 1


class AgentListOut(BaseModel):
    count: int
    agents: list[AgentOut]


# ---------------------------------------------------------------------------
# Write payloads (Phase 3: /birth, /spawn, /archive)
# ---------------------------------------------------------------------------
class TraitProfileIn(BaseModel):
    """Inbound trait profile — the DNA-determining input.

    The daemon converts this into a real :class:`TraitProfile` via the
    TraitEngine so validation (unknown traits, out-of-range values,
    unknown role) is centralized there. Pydantic only sanity-checks the
    shape.
    """

    role: str = Field(..., description="Role name, e.g. 'network_watcher'.")
    trait_values: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Map of trait_name -> integer 0..100. Traits omitted here fall "
            "back to the engine's role defaults."
        ),
    )
    domain_weight_overrides: dict[str, float] = Field(
        default_factory=dict,
        description="Optional per-domain weight overrides (clamped by engine).",
    )


class ToolRefIn(BaseModel):
    """A reference to a specific catalog tool by name + version.

    Used in BirthRequest.tools_add (per ADR-0018). The daemon validates
    the (name, version) pair against the loaded catalog and rejects
    unknown refs at the request boundary, before any artifact is
    produced.
    """

    name: str = Field(..., min_length=1, max_length=80)
    version: str = Field(..., min_length=1, max_length=16)


class BirthRequest(BaseModel):
    """Create a brand-new (root) agent.

    ``constitution_override`` is a layered backup per ADR-0004 / Path D:
    if provided, it is treated as additional policy text that the daemon
    hashes alongside the derived constitution. Absent means "use the
    engine-derived constitution untouched".

    ``enrich_narrative`` is the per-request opt-out introduced by
    ADR-0017. ``None`` (default) means "use the daemon's
    ``enrich_narrative_default`` setting"; explicit ``True`` / ``False``
    overrides for this birth only. False bypasses the LLM call entirely
    — useful for tests and reproducible benchmarks.

    ``tools_add`` and ``tools_remove`` (ADR-0018 T2) override the
    archetype's standard tool kit. ``tools_remove`` drops by name (any
    version match); ``tools_add`` introduces specific (name, version)
    refs. Empty defaults preserve the archetype-default behavior.
    """

    profile: TraitProfileIn
    agent_name: str = Field(..., min_length=1, max_length=80)
    agent_version: str = Field(default="v1", max_length=16)
    owner_id: str | None = Field(default=None, max_length=120)
    constitution_override: str | None = Field(
        default=None,
        description=(
            "Optional YAML snippet merged over the derived constitution. "
            "When present, the combined hash is what ends up in the soul."
        ),
    )
    enrich_narrative: bool | None = Field(
        default=None,
        description=(
            "When true, the daemon invokes the active provider to write the "
            "soul.md `## Voice` section (ADR-0017). When false, always use "
            "the templated fallback. None defers to FSF_ENRICH_NARRATIVE_DEFAULT."
        ),
    )
    tools_add: list[ToolRefIn] = Field(
        default_factory=list,
        description=(
            "Per-request tool additions on top of the archetype's standard "
            "kit. Each (name, version) must resolve in the daemon's loaded "
            "tool catalog (ADR-0018) — unknown refs return 400."
        ),
    )
    tools_remove: list[str] = Field(
        default_factory=list,
        description=(
            "Per-request tool removals. Matches by NAME (any version), so "
            "'tools_remove: [\"packet_query\"]' drops both packet_query.v1 "
            "and packet_query.v2 if either is in the standard kit. Unknown "
            "names return 400."
        ),
    )


class SpawnRequest(BirthRequest):
    """Spawn a child agent from an existing parent.

    ``parent_instance_id`` identifies the parent; lineage is derived from
    the parent's own lineage + DNA.
    """

    parent_instance_id: str = Field(..., min_length=1)


class ArchiveRequest(BaseModel):
    """Mark an agent as archived.

    ``reason`` is free-text but required — an archive with no recorded
    reason is a governance failure we refuse to log.
    """

    instance_id: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1, max_length=500)
    archived_by: str | None = Field(default=None, max_length=120)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------
class AuditEventOut(BaseModel):
    seq: int
    timestamp: str
    agent_dna: str | None
    instance_id: str | None
    event_type: str
    event_json: str
    entry_hash: str


class AuditListOut(BaseModel):
    count: int
    events: list[AuditEventOut]


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Runtime provider switch
# ---------------------------------------------------------------------------
class ProviderInfoOut(BaseModel):
    active: str
    default: str
    known: list[str]
    health: ProviderHealthOut


class SetProviderIn(BaseModel):
    provider: str = Field(..., description="Provider name to activate (e.g. 'local' or 'frontier').")


# ---------------------------------------------------------------------------
# Generation (POST /runtime/provider/generate)
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Trait tree exposure (GET /traits)
# ---------------------------------------------------------------------------
class TraitOut(BaseModel):
    """One trait in the tree.

    Shape mirrors :class:`forest_soul_forge.core.trait_engine.Trait` plus
    the computed ``tier_weight`` (clients use it to reason about impact
    without having to know the tier-weight table).
    """

    name: str
    domain: str
    subdomain: str
    tier: str
    tier_weight: float
    default: int
    desc: str
    scale_low: str
    scale_mid: str
    scale_high: str


class SubdomainOut(BaseModel):
    name: str
    domain: str
    description: str
    traits: list[TraitOut]


class DomainOut(BaseModel):
    name: str
    description: str
    subdomains: list[SubdomainOut]


class RoleOut(BaseModel):
    name: str
    description: str
    domain_weights: dict[str, float]


class FlaggedCombinationOut(BaseModel):
    name: str
    warning: str
    # conditions is {trait_name: "op threshold"} in display form; clients
    # parse the op/threshold half if they want to render live warnings.
    conditions: dict[str, str]


class TraitTreeOut(BaseModel):
    """Full trait tree as served to the frontend.

    One fetch powers the birth form: iterate ``domains -> subdomains ->
    traits`` to render grouped sliders, pick a ``role`` to seed defaults,
    and compare live profile against ``flagged_combinations`` locally for
    instant feedback (with ``/preview`` as the authoritative check).
    """

    version: str
    min_domain_weight: float
    max_domain_weight: float
    domains: list[DomainOut]
    roles: list[RoleOut]
    flagged_combinations: list[FlaggedCombinationOut]


# ---------------------------------------------------------------------------
# Preview (POST /preview) — zero-write slider feedback
# ---------------------------------------------------------------------------
class DomainGradeOut(BaseModel):
    domain: str
    intrinsic_score: float
    role_weight: float
    weighted_score: float
    subdomain_scores: dict[str, float]
    included_traits: int
    skipped_traits: int


class GradeReportOut(BaseModel):
    profile_dna: str
    role: str
    overall_score: float
    dominant_domain: str
    per_domain: list[DomainGradeOut]
    warnings: list[str]
    schema_version: int


class PreviewRequest(BaseModel):
    """Same shape as the birth payload minus identity.

    Preview is a pure function of the profile + optional override + tool
    surface, so it takes no agent_name and never touches the registry
    or the chain.

    ``tools_add`` and ``tools_remove`` MUST match what the eventual
    /birth call will pass — per ADR-0018 §"Reproducibility", the
    constitution hash now covers the resolved tool surface, so
    different overrides → different hash. /preview-with-defaults
    won't match a /birth-with-tools_add — pass the same overrides on
    both calls to get hash parity.
    """

    profile: TraitProfileIn
    constitution_override: str | None = Field(
        default=None,
        description="Same semantics as BirthRequest.constitution_override.",
    )
    tools_add: list[ToolRefIn] = Field(default_factory=list)
    tools_remove: list[str] = Field(default_factory=list)


class PreviewResponse(BaseModel):
    dna: str
    dna_full: str
    role: str
    constitution_hash_derived: str = Field(
        ...,
        description=(
            "Hash of the derived constitution (no override folded in). "
            "Equal across all agents with the same profile."
        ),
    )
    constitution_hash_effective: str = Field(
        ...,
        description=(
            "What the soul frontmatter would actually store. Equal to "
            "constitution_hash_derived when no override is supplied; "
            "SHA-256(derived || '\\noverride:\\n' || override) otherwise."
        ),
    )
    grade: GradeReportOut
    flagged_combinations: list[FlaggedCombinationOut]
    # Echo the profile back so the frontend can sanity-check it against
    # what it sent (useful when the engine clamps domain weights).
    effective_profile: TraitProfileIn
