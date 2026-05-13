"""Agent CRUD requests + responses (and the per-agent operations layered on top — hardware unbind, triune bond, archive).

Split out of the original 1139-line monolithic ``schemas.py`` by R1
of the post-marathon roadmap. Re-exported via ``schemas/__init__.py``
so existing ``from forest_soul_forge.daemon.schemas import X`` imports
keep working — this is purely an organizational refactor.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from forest_soul_forge.daemon.providers import ProviderStatus, TaskKind


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
    # ADR-003X K6 — opt-in hardware binding. Default False so existing
    # operators don't get surprised; True writes this machine's
    # fingerprint into the constitution YAML and the agent will be
    # quarantined at lifespan if moved to a different machine. See
    # /agents/{id}/hardware/unbind for the operator-driven release.
    bind_to_hardware: bool = Field(
        default=False,
        description=(
            "When True, embed this machine's hardware fingerprint into "
            "the agent's constitution. Subsequent loads on a different "
            "machine quarantine the agent. Operator unbinds via "
            "POST /agents/{id}/hardware/unbind to permit migration."
        ),
    )
    allow_weak_binding: bool = Field(
        default=False,
        description=(
            "When True, allow hardware binding even when the fingerprint "
            "source is the hostname fallback (no IOPlatformUUID, no "
            "machine-id). Hostnames can change without notification, so "
            "this binding is weaker than IOPlatformUUID/machine-id."
        ),
    )

# ADR-003X K6 — operator-driven hardware unbind. Distinct from bind
# because unbind is a deliberate governance act (the operator is
# saying "this agent is migrating; clear the fingerprint so the next
# machine accepts it"). Audited as a hardware_unbound ceremony.
class HardwareUnbindRequest(BaseModel):
    operator_id: str = Field(..., min_length=1, max_length=120)
    reason: str = Field(..., min_length=1, max_length=500)

class HardwareUnbindResponse(BaseModel):
    instance_id: str
    previous_binding: str | None
    seq: int
    timestamp: str

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

# ADR-003X K4 — seal three peer-root agents into a triune.
# The three agents must already exist (operator runs /birth × 3 first).
# This endpoint patches each agent's constitution YAML with a `triune`
# block containing bond_name, partners (the OTHER two instance_ids),
# and restrict_delegations: true. delegate.v1 then refuses any call to
# a target outside the partners list.
#
# constitution_hash is intentionally NOT recomputed — the triune block
# sits OUTSIDE Constitution.canonical_body() (which defines the hash).
# That keeps existing hash verification stable across bond/un-bond and
# avoids cascading rebuild work for every existing agent.
class TriuneBondRequest(BaseModel):
    bond_name: str = Field(..., min_length=1, max_length=64)
    instance_ids: list[str] = Field(..., min_length=3, max_length=3)
    operator_id: str = Field(..., min_length=1, max_length=120)
    restrict_delegations: bool = True   # SAFETY DEFAULT

class TriuneBondResponse(BaseModel):
    bond_name: str
    instance_ids: list[str]
    restrict_delegations: bool
    ceremony_seq: int
    ceremony_timestamp: str

# ADR-0061 T6 (Burst 248) — operator-facing passport mint endpoint.
# Distinct from the existing hardware/unbind because the passport
# is the EXPLICIT-roaming escape hatch (issue a cert authorizing
# the agent on another machine) rather than the broad-strokes
# release (clear the binding entirely). Operators choose between
# the two based on whether they want to KEEP the binding-as-
# protection (passport) or drop it (unbind).
class PassportMintRequest(BaseModel):
    authorized_fingerprints: list[str] = Field(
        ..., min_length=1,
        description=(
            "Hardware fingerprints (16-char hex strings) the passport "
            "authorizes the agent to run on. Must include at least the "
            "birth-machine fingerprint AND any additional roaming targets."
        ),
    )
    expires_at: str | None = Field(
        default=None,
        description=(
            "Optional RFC 3339 / ISO-8601 UTC expiration timestamp "
            "(e.g. '2026-08-12T00:00:00Z'). When set, verifier rejects "
            "the passport after this moment. When null, the passport "
            "is open-ended (operator can still revoke via re-mint with "
            "a past expires_at or via deleting passport.json)."
        ),
    )
    operator_id: str | None = Field(
        default=None, max_length=120,
        description=(
            "Operator identifier recorded in the audit event. "
            "Free-text label; not used cryptographically. Helps "
            "operators answer 'which of my admins minted this?' "
            "when running multi-operator deployments."
        ),
    )
    reason: str | None = Field(
        default=None, max_length=500,
        description=(
            "Free-text reason ('moving to laptop for trip'). "
            "Surfaced in chronicle exports + the audit event. "
            "Optional but encouraged."
        ),
    )

class PassportMintResponse(BaseModel):
    instance_id: str
    issuer_public_key: str
    authorized_fingerprints: list[str]
    issued_at: str
    expires_at: str | None
    passport_path: str
    seq: int
    timestamp: str
