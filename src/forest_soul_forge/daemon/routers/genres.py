"""``/genres`` — read-only enumeration of the loaded genre engine.

ADR-0021 T2. Powers the frontend genre selector (T8). Read-only; the
engine is loaded once at lifespan startup and held on app.state. Never
raises 503 — when the engine failed to load, the dep returns an empty
engine and this endpoint returns ``{"version": "0", "genres": []}``,
which the frontend renders as "no genres available, role selector
shows everything."
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from forest_soul_forge.core.genre_engine import GenreEngine
from forest_soul_forge.daemon.deps import get_genre_engine
from forest_soul_forge.daemon.schemas import (
    GenreOut,
    GenreRiskProfileOut,
    GenresOut,
)


router = APIRouter(tags=["genres"])


def _genre_to_out(gd) -> GenreOut:  # noqa: ANN001 — GenreDef is frozen
    return GenreOut(
        name=gd.name,
        description=gd.description,
        risk_profile=GenreRiskProfileOut(
            max_side_effects=gd.risk_profile.max_side_effects,
            provider_constraint=gd.risk_profile.provider_constraint,
            memory_ceiling=gd.risk_profile.memory_ceiling,
        ),
        default_kit_pattern=list(gd.default_kit_pattern),
        trait_emphasis=list(gd.trait_emphasis),
        memory_pattern=gd.memory_pattern,
        spawn_compatibility=list(gd.spawn_compatibility),
        roles=list(gd.roles),
    )


@router.get("/genres", response_model=GenresOut)
async def get_genres(
    genre_engine: GenreEngine = Depends(get_genre_engine),
) -> GenresOut:
    """Return all loaded genres in declaration order."""
    return GenresOut(
        version=genre_engine.version,
        genres=[_genre_to_out(gd) for gd in genre_engine.all_genres()],
    )
