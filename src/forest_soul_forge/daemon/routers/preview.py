"""``/preview`` — zero-write profile preview.

What a caller gets for free:

* ``dna`` / ``dna_full`` — what DNA this profile would produce.
* ``grade`` — the full :class:`GradeReport`, useful for live UI badges
  (overall score, dominant domain, per-domain intrinsic vs weighted).
* ``flagged_combinations`` — anything the engine flags against this
  profile. Rendered live so the user sees *why* a combo is risky
  before committing.
* ``constitution_hash_derived`` — content-addressed hash over the base
  constitution the engine would build. Equal across all agents with
  the same profile (that's the point of ADR-0004).
* ``constitution_hash_effective`` — same as derived when no override is
  supplied, else ``sha256(derived || "\\noverride:\\n" || override)``,
  matching the folding logic in ``/birth``.

No registry writes, no artifact writes, no audit append. The endpoint
is pure: two calls with the same body produce the same response, byte
for byte (modulo float representation in JSON).

Used by the birth / spawn form to give live feedback on every slider
nudge — the real mint happens through ``/birth`` or ``/spawn``.
"""
from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, HTTPException

from forest_soul_forge.core.constitution import build as build_constitution
from forest_soul_forge.core.dna import dna_full, dna_short
from forest_soul_forge.core.grading import grade as grade_profile
from forest_soul_forge.core.trait_engine import (
    InvalidTraitValueError,
    SchemaError as TraitSchemaError,
    TraitEngine,
    UnknownRoleError,
    UnknownTraitError,
)
from forest_soul_forge.daemon.deps import get_trait_engine
from forest_soul_forge.daemon.schemas import (
    DomainGradeOut,
    FlaggedCombinationOut,
    GradeReportOut,
    PreviewRequest,
    PreviewResponse,
    TraitProfileIn,
)


router = APIRouter(tags=["preview"])


def _fold_override(derived_hash: str, override: str | None) -> str:
    """Match the folding performed in ``writes.py`` so preview and birth
    produce identical hashes for the same inputs."""
    if not override:
        return derived_hash
    h = hashlib.sha256()
    h.update(derived_hash.encode("utf-8"))
    h.update(b"\noverride:\n")
    h.update(override.encode("utf-8"))
    return h.hexdigest()


def _grade_to_out(report) -> GradeReportOut:  # noqa: ANN001
    per = [
        DomainGradeOut(
            domain=d.domain,
            intrinsic_score=d.intrinsic_score,
            role_weight=d.role_weight,
            weighted_score=d.weighted_score,
            subdomain_scores=dict(d.subdomain_scores),
            included_traits=d.included_traits,
            skipped_traits=d.skipped_traits,
        )
        for d in report.per_domain.values()
    ]
    return GradeReportOut(
        profile_dna=report.profile_dna,
        role=report.role,
        overall_score=report.overall_score,
        dominant_domain=report.dominant_domain,
        per_domain=per,
        warnings=list(report.warnings),
        schema_version=report.schema_version,
    )


@router.post("/preview", response_model=PreviewResponse)
async def preview(
    req: PreviewRequest,
    engine: TraitEngine = Depends(get_trait_engine),
) -> PreviewResponse:
    # Build the profile — same validation path as /birth, but failures
    # surface as 400 without ever touching the registry.
    try:
        profile = engine.build_profile(
            role=req.profile.role,
            overrides=dict(req.profile.trait_values or {}),
            domain_weight_overrides=dict(req.profile.domain_weight_overrides or {}),
        )
    except UnknownRoleError as e:
        raise HTTPException(status_code=400, detail=f"unknown role: {e}") from e
    except UnknownTraitError as e:
        raise HTTPException(status_code=400, detail=f"unknown trait: {e}") from e
    except InvalidTraitValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid trait value: {e}") from e
    except TraitSchemaError as e:
        raise HTTPException(status_code=400, detail=f"invalid profile: {e}") from e

    dna_s = dna_short(profile)
    dna_hex = dna_full(profile)
    report = grade_profile(profile, engine)

    try:
        constitution = build_constitution(profile, engine, agent_name="preview")
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"constitution build failed: {e}"
        ) from e

    effective_hash = _fold_override(
        constitution.constitution_hash, req.constitution_override
    )

    flagged = [
        FlaggedCombinationOut(
            name=fc.name,
            warning=fc.warning,
            conditions={
                t: f"{op} {thresh}" for t, (op, thresh) in fc.conditions.items()
            },
        )
        for fc in engine.scan_flagged(profile)
    ]

    # Echo the post-validation profile back. The engine may have clamped
    # domain weight overrides or filled in defaults for unspecified traits
    # — the frontend compares this against what it sent to spot the drift.
    effective_profile = TraitProfileIn(
        role=profile.role,
        trait_values=dict(profile.trait_values),
        domain_weight_overrides=dict(profile.domain_weight_overrides),
    )

    return PreviewResponse(
        dna=dna_s,
        dna_full=dna_hex,
        role=profile.role,
        constitution_hash_derived=constitution.constitution_hash,
        constitution_hash_effective=effective_hash,
        grade=_grade_to_out(report),
        flagged_combinations=flagged,
        effective_profile=effective_profile,
    )
