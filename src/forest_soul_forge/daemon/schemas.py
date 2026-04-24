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


class AgentListOut(BaseModel):
    count: int
    agents: list[AgentOut]


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


class HealthOut(BaseModel):
    ok: bool
    schema_version: int
    canonical_contract: str
    active_provider: str
    provider: ProviderHealthOut


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
