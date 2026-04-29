"""Skill catalog + skill run request/response shapes.

Split out of the original 1139-line monolithic ``schemas.py`` by R1
of the post-marathon roadmap. Re-exported via ``schemas/__init__.py``
so existing ``from forest_soul_forge.daemon.schemas import X`` imports
keep working — this is purely an organizational refactor.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from forest_soul_forge.daemon.providers import ProviderStatus, TaskKind


class SkillStepSummaryOut(BaseModel):
    """One step's summary for the GET /skills response. Compact —
    full step shape is available by reading the manifest file."""

    id: str
    kind: str  # "tool" | "for_each" | "unknown"
    tool: str | None = None
    inner_count: int | None = None

class SkillSummaryOut(BaseModel):
    """One skill in the catalog. Frontend renders this card-by-card."""

    name: str
    version: str
    description: str
    requires: list[str] = Field(default_factory=list)
    inputs_schema: dict[str, Any] = Field(default_factory=dict)
    output_keys: list[str] = Field(default_factory=list)
    steps: list[SkillStepSummaryOut] = Field(default_factory=list)
    skill_hash: str
    forged_at: str | None = None
    forged_by: str | None = None
    forge_provider: str | None = None

class SkillCatalogOut(BaseModel):
    count: int
    skills: list[SkillSummaryOut]

class SkillRunRequest(BaseModel):
    """Request body for ``POST /agents/{instance_id}/skills/run``.

    The skill is identified by ``skill_name`` + ``skill_version``; the
    ad-hoc loader (pre-ADR-0031 T5) reads the manifest from
    ``data/forge/skills/installed/<name>.v<version>.yaml``. T5 will
    introduce a registry-backed catalog.

    ``inputs`` is the skill's args block — validated by the manifest's
    ``inputs`` schema before the runtime walks the DAG.
    """

    skill_name: str = Field(..., min_length=1, max_length=80)
    skill_version: str = Field(..., min_length=1, max_length=16)
    session_id: str = Field(..., min_length=1, max_length=80)
    inputs: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = Field(
        default=False,
        description=(
            "Recorded in skill_invoked event. ADR-0031 T6 will wire a "
            "real dry-run mode that uses stub providers; T2 just plumbs "
            "the flag for forward-compat."
        ),
    )

class SkillRunResponse(BaseModel):
    """Response shape for skill run.

    Exactly one of ``output`` / ``failure_step_id`` is set, discriminated
    by ``status``:
      ``succeeded`` — output populated, HTTP 200.
      ``failed``   — failure_step_id + failure_reason set, HTTP 200
                     (the API call worked; the skill didn't).
    Refusals (unknown skill, unknown agent) come as HTTPException, not
    this schema.
    """

    status: str
    skill_name: str
    skill_version: str
    skill_hash: str
    invoked_seq: int
    completed_seq: int
    output: dict[str, Any] | None = None
    steps_executed: int = 0
    steps_skipped: int = 0
    failed_step_id: str | None = None
    failure_reason: str | None = None
    failure_detail: str | None = None
    bindings_at_failure: dict[str, Any] | None = None
