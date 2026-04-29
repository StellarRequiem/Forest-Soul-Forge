"""Genre engine read-only exposure (GET /genres) — ADR-0021 T2.

Split out of the original 1139-line monolithic ``schemas.py`` by R1
of the post-marathon roadmap. Re-exported via ``schemas/__init__.py``
so existing ``from forest_soul_forge.daemon.schemas import X`` imports
keep working — this is purely an organizational refactor.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from forest_soul_forge.daemon.providers import ProviderStatus, TaskKind


class GenreRiskProfileOut(BaseModel):
    """The hash-affecting + structural floor of a genre's risk surface."""

    max_side_effects: str
    provider_constraint: str | None = None
    # ADR-0027 §1+§5 — widest memory scope an agent in this genre may
    # write under. Surfaced via /genres so the frontend can render the
    # privacy floor of each genre and the security-swarm tier difference.
    memory_ceiling: str = "private"

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
