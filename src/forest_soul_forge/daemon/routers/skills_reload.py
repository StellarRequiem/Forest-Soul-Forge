"""``POST /skills/reload`` — refresh the in-memory skill catalog.

Re-reads ``settings.skill_install_dir`` and replaces
``app.state.skill_catalog`` with the new view. Used after a manifest
is added/removed/edited so the daemon picks it up without a restart.

Read-then-swap, no hot-load — the new catalog is built independently
and only assigned to ``app.state`` when the load succeeds, so a
malformed manifest doesn't blow away the existing catalog.
"""
from __future__ import annotations

from threading import Lock

from fastapi import APIRouter, Depends, Request

from forest_soul_forge.daemon.deps import (
    get_settings,
    get_write_lock,
    require_api_token,
    require_writes_enabled,
)


router = APIRouter(tags=["skills"])


@router.post(
    "/skills/reload",
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
async def reload_skills(
    request: Request,
    settings=Depends(get_settings),
    write_lock: Lock = Depends(get_write_lock),
) -> dict:
    """Reload the skill catalog from disk. Returns counts + any
    errors surfaced during the load (malformed manifests, etc.).

    Held under the write lock so a concurrent ``/skills/run`` can't
    observe a half-swapped catalog.
    """
    from forest_soul_forge.core.skill_catalog import load_catalog
    with write_lock:
        catalog, errors = load_catalog(settings.skill_install_dir)
        request.app.state.skill_catalog = catalog
    return {
        "count": catalog.count,
        "errors": errors,
        "source_dir": str(catalog.source_dir) if catalog.source_dir else None,
    }
