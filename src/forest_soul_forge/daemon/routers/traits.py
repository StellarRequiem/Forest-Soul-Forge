"""``/traits`` — read-only trait tree exposure.

Purpose: power the frontend's birth form without forcing the browser to
parse ``config/trait_tree.yaml`` directly. Same-origin fetch, already-
validated shape, enriched with computed fields (``tier_weight``) the
browser shouldn't have to re-derive.

Read-only. The trait tree is authored in YAML and loaded at daemon
startup (see ``app.py`` lifespan); this endpoint merely serializes the
in-memory :class:`TraitEngine` into JSON.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from forest_soul_forge.core.trait_engine import TraitEngine
from forest_soul_forge.daemon.deps import get_trait_engine
from forest_soul_forge.daemon.schemas import (
    DomainOut,
    FlaggedCombinationOut,
    RoleOut,
    SubdomainOut,
    TraitOut,
    TraitTreeOut,
)


router = APIRouter(tags=["traits"])


def _trait_to_out(trait) -> TraitOut:  # noqa: ANN001 — Trait is a frozen dataclass
    return TraitOut(
        name=trait.name,
        domain=trait.domain,
        subdomain=trait.subdomain,
        tier=trait.tier,
        tier_weight=trait.tier_weight,
        default=trait.default,
        desc=trait.desc,
        scale_low=trait.scale_low,
        scale_mid=trait.scale_mid,
        scale_high=trait.scale_high,
    )


def _subdomain_to_out(sd) -> SubdomainOut:  # noqa: ANN001
    return SubdomainOut(
        name=sd.name,
        domain=sd.domain,
        description=sd.description,
        traits=[_trait_to_out(t) for t in sd.traits.values()],
    )


def _domain_to_out(d) -> DomainOut:  # noqa: ANN001
    return DomainOut(
        name=d.name,
        description=d.description,
        subdomains=[_subdomain_to_out(sd) for sd in d.subdomains.values()],
    )


def _role_to_out(r) -> RoleOut:  # noqa: ANN001
    return RoleOut(
        name=r.name,
        description=r.description,
        domain_weights=dict(r.domain_weights),
    )


def _flagged_to_out(fc) -> FlaggedCombinationOut:  # noqa: ANN001
    # conditions is {trait: (op, threshold)}; render as "op threshold" so
    # the frontend can display the rule verbatim or re-parse if needed.
    return FlaggedCombinationOut(
        name=fc.name,
        warning=fc.warning,
        conditions={t: f"{op} {thresh}" for t, (op, thresh) in fc.conditions.items()},
    )


@router.get("/traits", response_model=TraitTreeOut)
async def get_trait_tree(
    engine: TraitEngine = Depends(get_trait_engine),
) -> TraitTreeOut:
    """Return the full trait tree.

    Raises 503 via the ``get_trait_engine`` dep when the tree failed to
    load at startup (missing or malformed YAML). Otherwise returns a
    stable snapshot — the tree is loaded once at lifespan startup and
    doesn't change until the process restarts.
    """
    return TraitTreeOut(
        version=engine.version,
        min_domain_weight=engine.min_domain_weight,
        max_domain_weight=engine.max_domain_weight,
        domains=[_domain_to_out(d) for d in engine.domains.values()],
        roles=[_role_to_out(r) for r in engine.roles.values()],
        flagged_combinations=[
            _flagged_to_out(fc) for fc in engine.flagged_combinations
        ],
    )
