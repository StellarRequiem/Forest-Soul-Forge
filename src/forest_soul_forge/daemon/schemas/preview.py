"""Preview (POST /preview) — zero-write slider feedback.

Split out of the original 1139-line monolithic ``schemas.py`` by R1
of the post-marathon roadmap. Re-exported via ``schemas/__init__.py``
so existing ``from forest_soul_forge.daemon.schemas import X`` imports
keep working — this is purely an organizational refactor.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from forest_soul_forge.daemon.providers import ProviderStatus, TaskKind


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
