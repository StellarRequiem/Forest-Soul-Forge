"""Character sheet (ADR-0020) — derived view, not a canonical artifact.

Split out of the original 1139-line monolithic ``schemas.py`` by R1
of the post-marathon roadmap. Re-exported via ``schemas/__init__.py``
so existing ``from forest_soul_forge.daemon.schemas import X`` imports
keep working — this is purely an organizational refactor.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from forest_soul_forge.daemon.providers import ProviderStatus, TaskKind


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
    """Memory subsystem state. Populated from Memory.count() per
    ADR-0022 v0.1.

    ``not_yet_measured`` flips False the moment the agent has any
    memory entry — distinguishes "freshly born, no memory yet" from
    "actively used."

    ``layers`` is a dict ``{episodic: count, semantic: count,
    procedural: count}``. Tombstoned entries are excluded.

    ``consolidation_run_count`` stays at 0 in v0.1 — consolidation
    (rolling episodic into semantic / extracting procedural patterns)
    is a v0.2+ feature.
    """

    not_yet_measured: bool = True
    total_entries: int = 0
    layers: dict[str, int] = Field(default_factory=dict)
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
