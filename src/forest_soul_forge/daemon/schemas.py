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

    ``override_genre_spawn_rule`` is the explicit escape hatch for
    cross-genre spawns that the genre engine would otherwise refuse
    (ADR-0021 T6). Default False means the daemon enforces the
    parent-genre's ``spawn_compatibility`` list. Set True for the
    one-off case where a specific incident genuinely calls for the
    forbidden combination — the daemon allows the spawn and appends
    a dedicated ``spawn_genre_override`` audit event so the
    violation is visible after the fact. Operators who set this to
    True without a real need are leaving an obvious trail in the
    chain. Unclaimed-role spawns ignore this flag entirely (no genre,
    no rule to override).
    """

    parent_instance_id: str = Field(..., min_length=1)
    override_genre_spawn_rule: bool = Field(
        default=False,
        description=(
            "ADR-0021 T6 — when True, allow a spawn that violates the "
            "parent genre's spawn_compatibility list. The daemon appends "
            "a spawn_genre_override audit event recording both genres."
        ),
    )


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
# Tool catalog discovery (GET /tools/catalog, GET /tools/kit/{role})
#
# Defined BEFORE PreviewResponse because PreviewResponse embeds
# ResolvedToolOut. Pydantic v2 handles forward refs as long as the
# string-form annotation gets resolved by the time the model is used,
# but keeping the definition order natural avoids an explicit
# model_rebuild() call.
# ---------------------------------------------------------------------------
class ToolDefOut(BaseModel):
    """One catalog entry as exposed to the frontend.

    Mirrors :class:`forest_soul_forge.core.tool_catalog.ToolDef` minus
    ``input_schema`` (heavy, only needed at execution time — the UI
    works from the description and side_effects).
    """

    name: str
    version: str
    description: str
    side_effects: str
    archetype_tags: list[str] = Field(default_factory=list)


class ArchetypeBundleOut(BaseModel):
    """A role's standard kit. ``standard_tools`` are bare {name, version}
    refs the frontend can compare against the resolved kit to identify
    which entries are archetype-defaults vs. user-added."""

    role: str
    standard_tools: list[ToolRefIn]


class ToolCatalogOut(BaseModel):
    """Full catalog snapshot served at GET /tools/catalog.

    Read-only; the catalog is loaded once at lifespan startup and held on
    ``app.state``. ``version`` is the catalog file's version (advanced
    when the YAML changes), distinct from each tool's own version.
    """

    version: str
    tools: list[ToolDefOut]
    archetypes: list[ArchetypeBundleOut]


class ResolvedToolOut(BaseModel):
    """One tool in a role's resolved kit, with policy constraints applied.

    Served by GET /tools/kit/{role} and embedded in PreviewResponse.
    Mirrors the per-tool record that ends up in constitution.yaml's
    ``tools`` block — same name/version/side_effects, same constraint
    dict, same applied_rules — plus the description joined from the
    catalog so the UI doesn't need a second lookup.
    """

    name: str
    version: str
    description: str
    side_effects: str
    constraints: dict[str, Any] = Field(default_factory=dict)
    applied_rules: list[str] = Field(default_factory=list)


class ResolvedKitOut(BaseModel):
    """Response for GET /tools/kit/{role}.

    Includes the role echo and the catalog version so the frontend can
    invalidate cached kits when the underlying catalog changes.
    """

    role: str
    catalog_version: str
    tools: list[ResolvedToolOut]


# ---------------------------------------------------------------------------
# Genre engine read-only exposure (GET /genres) — ADR-0021 T2.
# ---------------------------------------------------------------------------
class GenreRiskProfileOut(BaseModel):
    """The hash-affecting + structural floor of a genre's risk surface."""

    max_side_effects: str
    provider_constraint: str | None = None


class GenreOut(BaseModel):
    """One genre as enumerated by GET /genres.

    Mirrors :class:`forest_soul_forge.core.genre_engine.GenreDef` field
    by field. The frontend's genre selector consumes this to populate
    its dropdown and to filter the role list when a genre is selected.
    """

    name: str
    description: str
    risk_profile: GenreRiskProfileOut
    default_kit_pattern: list[str] = Field(default_factory=list)
    trait_emphasis: list[str] = Field(default_factory=list)
    memory_pattern: str
    spawn_compatibility: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)


class GenresOut(BaseModel):
    """Response for GET /genres.

    ``version`` matches the ``version`` field in ``genres.yaml`` so the
    frontend can detect when the loaded engine has changed under it.
    """

    version: str
    genres: list[GenreOut]


# ---------------------------------------------------------------------------
# Character sheet (ADR-0020) — derived view, not a canonical artifact.
# Composed on demand from registry + soul.md frontmatter + constitution.yaml +
# genre engine. Schema slots for stats/memory/benchmarks are scaffolded now
# (with not_yet_measured: true) so consumers don't need to be rewritten when
# ADR-0022 / ADR-0023 ship.
# ---------------------------------------------------------------------------
class CharacterIdentity(BaseModel):
    instance_id: str
    dna: str
    dna_full: str
    sibling_index: int = 1
    agent_name: str
    agent_version: str
    role: str
    genre: str | None = None
    parent_instance: str | None = None
    lineage: list[str] = Field(default_factory=list)
    lineage_depth: int = 0
    created_at: str
    status: str
    owner_id: str | None = None


class CharacterPersonality(BaseModel):
    trait_values: dict[str, int] = Field(default_factory=dict)
    domain_weight_overrides: dict[str, float] = Field(default_factory=dict)
    voice_text: str | None = None
    narrative_provider: str | None = None
    narrative_model: str | None = None
    narrative_generated_at: str | None = None


class CharacterLoadoutTool(BaseModel):
    name: str
    version: str
    side_effects: str | None = None
    description: str | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    applied_rules: list[str] = Field(default_factory=list)


class CharacterLoadout(BaseModel):
    tools: list[CharacterLoadoutTool] = Field(default_factory=list)
    tool_catalog_version: str | None = None


class CharacterCapabilities(BaseModel):
    """Genre + risk floor + provider constraint. Source for "what kind of
    agent is this allowed to be" decisions in the UI."""

    genre: str | None = None
    genre_description: str | None = None
    max_side_effects: str | None = None
    provider_constraint: str | None = None
    trait_emphasis: list[str] = Field(default_factory=list)
    spawn_compatibility: list[str] = Field(default_factory=list)


class CharacterPolicySummary(BaseModel):
    """Summary of the constitution. Doesn't duplicate every policy
    verbatim (the constitution.yaml is on disk for that) — a count by
    rule type plus the risk thresholds + drift settings the operator
    actually wants on the page at a glance."""

    constitution_hash: str | None = None
    policy_count: int = 0
    policy_count_by_rule: dict[str, int] = Field(default_factory=dict)
    risk_thresholds: dict[str, float] = Field(default_factory=dict)
    drift_monitoring: dict[str, Any] = Field(default_factory=dict)
    out_of_scope: list[str] = Field(default_factory=list)
    operator_duties: list[str] = Field(default_factory=list)


class CharacterStatsPerTool(BaseModel):
    """Per-tool roll-up inside ``CharacterStats.per_tool``.

    ``tokens`` and ``cost`` are nullable: ``None`` means no calls of
    this tool ever reported accounting numbers (e.g., a pure-function
    tool like timestamp_window will always be None). ``0`` would mean
    "ran but reported zero tokens" — different signal.
    """

    tool_key: str
    count: int = 0
    tokens: int | None = None
    cost: float | None = None


class CharacterStats(BaseModel):
    """Operational stats sourced from ADR-0019 T4 ``tool_calls`` table.

    ``not_yet_measured`` is True when the agent has zero recorded
    calls — distinguishes "freshly born, no usage yet" from "actively
    used but quiet today." UI renders the difference (placeholder
    panel vs. live numbers).

    Tokens and cost are nullable so a UI can distinguish "no
    LLM-wrapping tool ever ran for this agent" (None) from "ran but
    used zero tokens" (0). Pure-function tools (timestamp_window,
    summarize when fed a cached result) report None.
    """

    not_yet_measured: bool = True
    total_invocations: int = 0
    failed_invocations: int = 0
    total_tokens_used: int | None = None
    total_cost_usd: float | None = None
    last_active_at: str | None = None
    per_tool: list[CharacterStatsPerTool] = Field(default_factory=list)


class CharacterMemory(BaseModel):
    """Memory subsystem state. Scaffolded for ADR-0022; empty today."""

    not_yet_measured: bool = True
    layers: dict[str, Any] = Field(default_factory=dict)
    consolidation_run_count: int = 0


class CharacterBenchmarks(BaseModel):
    """Benchmark scores. Scaffolded for ADR-0023; empty today."""

    not_yet_measured: bool = True
    suite_results: list[dict[str, Any]] = Field(default_factory=list)


class CharacterProvenance(BaseModel):
    """Audit-chain pointers. Lets the operator click through from the
    sheet to the chain entries that birthed / spawned / archived the
    agent."""

    soul_path: str
    constitution_path: str
    audit_event_count: int = 0
    audit_chain_entry_hash: str | None = None


class CharacterSheetOut(BaseModel):
    """ADR-0020. Single-page descriptor of an agent.

    The eight-section shape is permanent (consumers can rely on every
    field always being present). Sections whose underlying subsystem
    hasn't shipped show ``not_yet_measured: true`` rather than being
    omitted, so the UI never has to handle "field present today, gone
    tomorrow."
    """

    schema_version: int = 1
    rendered_at: str
    identity: CharacterIdentity
    personality: CharacterPersonality
    loadout: CharacterLoadout
    capabilities: CharacterCapabilities
    policies: CharacterPolicySummary
    stats: CharacterStats
    memory: CharacterMemory
    benchmarks: CharacterBenchmarks
    provenance: CharacterProvenance


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
    # ADR-0018 T4: tool surface that would land in the constitution. Same
    # records the daemon writes into constitution.yaml's `tools:` block —
    # frontend uses this to show "what gets capped / approved" without
    # parsing the YAML.
    resolved_tools: list[ResolvedToolOut] = Field(
        default_factory=list,
        description=(
            "Per-tool resolution results: name, version, side_effects, "
            "the merged constraint set after applying tool_policy rules, "
            "and the names of the rules that fired. Identical (in shape) "
            "to what constitution.yaml's `tools:` block stores, plus the "
            "joined description for UI convenience."
        ),
    )


# ---------------------------------------------------------------------------
# Tool dispatch (ADR-0019 T2 — POST /agents/{id}/tools/call)
# ---------------------------------------------------------------------------
class ToolCallRequest(BaseModel):
    """Request body for ``POST /agents/{instance_id}/tools/call``.

    ``session_id`` is operator-supplied. Two reasons:
    * The runtime per-session counter keys on it.
    * Operators batching multiple calls under one logical session want
      stable counter semantics — a single UUID per session, not a
      per-request UUID.

    ``args`` is the tool's input. Validation is the tool's job; the
    daemon refuses to inspect it beyond JSON-decoding.
    """

    tool_name: str = Field(..., min_length=1, max_length=80)
    tool_version: str = Field(..., min_length=1, max_length=16)
    session_id: str = Field(..., min_length=1, max_length=80)
    args: dict[str, Any] = Field(default_factory=dict)


class ToolCallResultOut(BaseModel):
    """The agent-facing result of a successful dispatch."""

    output: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    tokens_used: int | None = None
    cost_usd: float | None = None
    side_effect_summary: str | None = None
    result_digest: str = Field(
        ...,
        description=(
            "SHA-256 over canonical (output, metadata). Mirrors the "
            "audit chain entry's result_digest so callers can verify "
            "result integrity without re-fetching the chain."
        ),
    )


class PendingApprovalOut(BaseModel):
    """One row from ``GET /agents/{id}/pending_calls``.

    ``args`` is parsed back from the registry's canonical JSON so the
    frontend gets a JSON object rather than a quoted string. ``status``
    is exposed even though the default endpoint filters to pending —
    the full-history variant uses the same shape.
    """

    ticket_id: str
    instance_id: str
    session_id: str
    tool_key: str
    args: dict[str, Any] = Field(default_factory=dict)
    side_effects: str
    status: str
    pending_audit_seq: int
    decided_audit_seq: int | None = None
    decided_by: str | None = None
    decision_reason: str | None = None
    created_at: str
    decided_at: str | None = None


class PendingApprovalListOut(BaseModel):
    count: int
    pending_calls: list[PendingApprovalOut]


class ApproveRequest(BaseModel):
    """Body for ``POST /pending_calls/{ticket_id}/approve``."""

    operator_id: str = Field(..., min_length=1, max_length=80)


class RejectRequest(BaseModel):
    """Body for ``POST /pending_calls/{ticket_id}/reject``.

    ``reason`` is required so the rejected event in the audit chain
    carries the operator's stated rationale. Empty rejections obscure
    intent in the long run.
    """

    operator_id: str = Field(..., min_length=1, max_length=80)
    reason: str = Field(..., min_length=1, max_length=500)


class ToolCallResponse(BaseModel):
    """Response shape for ``POST /agents/{id}/tools/call``.

    Exactly one of ``result`` / ``ticket_id`` / ``failure`` is set,
    discriminated by ``status``:
    * ``succeeded`` — ``result`` populated, HTTP 200.
    * ``pending_approval`` — ``ticket_id`` set, HTTP 202.
    * ``failed`` — tool started but raised; ``failure`` set, HTTP 200
      (the API call succeeded — the tool didn't). Distinguishes from
      a ``refused`` outcome which uses HTTP 4xx.

    Refusals don't reach this schema; they're returned as HTTP 400/403/404
    via FastAPI's HTTPException machinery so clients get the standard
    error shape.
    """

    status: str = Field(
        ..., description="One of: succeeded, pending_approval, failed."
    )
    tool_key: str = Field(
        ..., description="The dispatched tool's name.vversion."
    )
    audit_seq: int = Field(
        ..., description="Audit-chain seq of the terminating event."
    )
    call_count_after: int | None = Field(
        default=None,
        description=(
            "Per-session call count after this dispatch (succeeded only). "
            "None for pending_approval and failed since the counter is "
            "incremented BEFORE execute and the failure path still "
            "returns it via the audit chain."
        ),
    )
    result: ToolCallResultOut | None = None
    ticket_id: str | None = None
    failure_exception_type: str | None = None
