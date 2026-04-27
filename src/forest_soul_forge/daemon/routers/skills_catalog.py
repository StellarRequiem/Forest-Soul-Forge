"""``GET /skills`` — read-only skill catalog endpoint (ADR-0031 T5).

Returns the catalog loaded from ``settings.skill_install_dir`` at
lifespan. Frontend renders the list; the run endpoint
(/agents/{id}/skills/run) consumes the same manifests.

T7 of ADR-0031 will introduce an install path that mutates the
catalog at runtime; until then operators move staged manifests in
manually and restart the daemon.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from forest_soul_forge.daemon.schemas import (
    SkillCatalogOut,
    SkillSummaryOut,
    SkillStepSummaryOut,
)


router = APIRouter(tags=["skills"])


def _step_summary(step) -> SkillStepSummaryOut:
    """Compress a step (ToolStep or ForEachStep) into the bits the
    frontend cares about — id + the tool / for_each indicator."""
    from forest_soul_forge.forge.skill_manifest import ForEachStep, ToolStep
    if isinstance(step, ToolStep):
        return SkillStepSummaryOut(
            id=step.id, kind="tool", tool=step.tool,
        )
    if isinstance(step, ForEachStep):
        return SkillStepSummaryOut(
            id=step.id, kind="for_each",
            inner_count=len(step.steps),
        )
    return SkillStepSummaryOut(id=getattr(step, "id", "?"), kind="unknown")


@router.get("/skills", response_model=SkillCatalogOut)
async def list_skills(request: Request) -> SkillCatalogOut:
    catalog = getattr(request.app.state, "skill_catalog", None)
    if catalog is None:
        return SkillCatalogOut(count=0, skills=[])
    out = []
    for key in sorted(catalog.skills):
        sd = catalog.skills[key]
        out.append(SkillSummaryOut(
            name=sd.name,
            version=sd.version,
            description=sd.description,
            requires=list(sd.requires),
            inputs_schema=sd.inputs_schema,
            output_keys=sorted(sd.output.keys()),
            steps=[_step_summary(s) for s in sd.steps],
            skill_hash=sd.skill_hash,
            forged_at=sd.forged_at,
            forged_by=sd.forged_by,
            forge_provider=sd.forge_provider,
        ))
    return SkillCatalogOut(count=len(out), skills=out)
