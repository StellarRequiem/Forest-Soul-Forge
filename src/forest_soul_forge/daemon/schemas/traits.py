"""Trait tree read-only exposure (GET /traits).

Split out of the original 1139-line monolithic ``schemas.py`` by R1
of the post-marathon roadmap. Re-exported via ``schemas/__init__.py``
so existing ``from forest_soul_forge.daemon.schemas import X`` imports
keep working — this is purely an organizational refactor.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from forest_soul_forge.daemon.providers import ProviderStatus, TaskKind


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
