"""writes — package facade. Per-endpoint sub-routers.

Package layout (ADR-0040 T3, closed Burst 80, 2026-05-02):

    writes/
      __init__.py    — this file. Pure package facade. Declares the
                       parent APIRouter (governance dependencies live
                       here so they fire once per request) and
                       include_router()s each per-endpoint sub-router.
                       Carries no @router decorators of its own — every
                       endpoint lives in a sub-router.
      _shared.py     — idempotency-replay + voice-render helpers used
                       by multiple endpoints (T3.1, T3.2).
      birth.py       — /birth + /spawn + _perform_create + 10 creation
                       helpers (T3.2).
      voice.py       — /regenerate-voice (T3.3).
      archive.py     — /archive (T3.4).

Public symbol: ``router`` (the parent APIRouter). ``app.py`` mounts it
exactly once via ``app.include_router(writes_router.router)``.

Trust-surface separation (ADR-0040 §1):
Each per-endpoint sub-router file is independently grant-able as a
constitutional ``allowed_paths`` target. An agent given access to
``writes/voice.py`` can extend the voice-regeneration logic without
inheriting the ability to create new agents or archive existing ones;
likewise for ``writes/birth.py`` and ``writes/archive.py``. The
governance dependencies (``require_writes_enabled`` +
``require_api_token``) are declared on the parent router HERE and
applied to all included routes via FastAPI's include_router; sub-
routers don't redeclare them so the deps fire once per request rather
than twice via include-stacking.

Ordering discipline — artifact-authoritative (ADR-0006):

    1. Generate soul + constitution byte-for-byte.
    2. Write them to disk (soul_generated/<filename>).
    3. Append one audit-chain entry.
    4. Register in SQLite (sibling_index + instance_id + ancestry).

Step 3 is the commit point. If step 3 succeeds but step 4 fails, the
registry can be rebuilt from artifacts and will re-derive the same row.
If step 3 fails, we delete the files from step 2 — the chain is the
source of truth, so a soul on disk that the chain never acknowledged is
a ghost we refuse to keep. (Implementation lives in birth.py.)

Serialization: every handler runs under ``app.state.write_lock`` (a
``threading.Lock``). FastAPI dispatches sync routes on a threadpool, so
a thread-level lock is the right primitive. This also guards the
``next_sibling_index`` → ``INSERT`` race on twin births. (Each sub-
router takes the lock as a Depends-injected fixture.)

Phase 4 (ADR-0017): when ``enrich_narrative`` resolves true, the LLM-
backed Voice renderer is called *outside* the write lock. Holding a
threading lock across a 1-4s network call would serialize unrelated
births for no benefit — the renderer's only side effect is the returned
``VoiceText``, not registry state. (Both birth.py and voice.py route
through ``_maybe_render_voice`` in ``_shared.py`` for this.)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from forest_soul_forge.daemon.deps import require_api_token, require_writes_enabled

# Per-endpoint sub-routers. Each owns one trust surface; mounted under
# this package's parent router, which carries the governance deps.
from forest_soul_forge.daemon.routers.writes import archive as _archive_module
from forest_soul_forge.daemon.routers.writes import birth as _birth_module
from forest_soul_forge.daemon.routers.writes import voice as _voice_module


# Parent router. Governance deps declared HERE only — sub-routers
# carry no deps of their own so include_router doesn't double-stack
# the deps on the included routes.
router = APIRouter(
    tags=["writes"],
    # Order matters: 403 fires before 401 when writes are disabled, which
    # is the more informative response — "this deployment doesn't accept
    # writes" is a different problem than "you're missing the token".
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)

# Mount the per-endpoint sub-routers. Order is documentary — FastAPI
# resolves routes by exact-path match, not declaration order, so the
# include sequence doesn't change behavior.
router.include_router(_birth_module.router)
router.include_router(_voice_module.router)
router.include_router(_archive_module.router)
