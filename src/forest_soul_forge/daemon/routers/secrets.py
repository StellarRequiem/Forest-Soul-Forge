"""``/secrets/...`` — read-only operator-facing surface for the
ADR-0052 pluggable secret-store backend.

GET endpoints (ungated, same posture as /audit + /healthz):

  GET /secrets/backend
       — return the active backend name + selection source.
         Useful for debugging "why doesn't my secret show up."

  GET /secrets/names
       — return the list of secret names the active backend can
         serve. Names ONLY — never values. Used by the chat-tab
         settings panel (ADR-0052 T6) to show the operator what
         Forest has access to.

Mutating operations stay CLI-only (`fsf secret put|delete`). The
chat tab is intentionally NOT a destructive surface — operators
storing or removing credentials should do it through a deliberate
terminal action with masked-input prompts. Mirrors the design of
posture (read via UI, write via UI; sensitive enough that we
audit) vs. secrets (read via UI, write via CLI; values must
never round-trip the browser).

Per ADR-0052 Decision 1 + ADR-0044 D3: zero kernel ABI surface
changes. New userspace HTTP endpoints; existing audit-chain event
vocabulary unchanged.
"""
from __future__ import annotations

import os
import platform
from typing import Any

from fastapi import APIRouter, HTTPException, status

from forest_soul_forge.security.secrets import (
    SecretStoreError,
    resolve_secret_store,
)


router = APIRouter(tags=["secrets"], prefix="/secrets")


@router.get("/backend")
def get_backend() -> dict[str, Any]:
    """Active secret-store backend + how it was selected.

    Mirrors the `fsf secret backend` CLI surface but over HTTP for
    the chat-tab settings panel. No values exposed.
    """
    explicit = os.environ.get("FSF_SECRET_STORE", "").strip()
    try:
        store = resolve_secret_store()
    except SecretStoreError as e:
        # 503 because the daemon itself is up; only the backend
        # subsystem is unavailable. Operator can fix by setting
        # FSF_SECRET_STORE or installing the missing backend.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"secret-store backend unavailable: {e}",
        ) from e

    if explicit:
        selection_source = "explicit"
        selection_via = f"FSF_SECRET_STORE={explicit!r}"
    else:
        selection_source = "platform_default"
        selection_via = f"platform={platform.system()!r}"

    return {
        "name": store.name,
        "selection_source": selection_source,
        "selection_via": selection_via,
    }


@router.get("/names")
def list_names() -> dict[str, Any]:
    """List secret names served by the active backend.

    Names ONLY — values never traverse the HTTP surface. Used by
    the chat-tab settings panel to render "what does Forest have
    access to right now." Returns a sorted name list so the UI
    rendering is deterministic.
    """
    try:
        store = resolve_secret_store()
    except SecretStoreError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"secret-store backend unavailable: {e}",
        ) from e

    try:
        names = store.list_names()
    except SecretStoreError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"backend {store.name!r} failed listing names: {e}. "
                f"Check backend health (e.g. for the file backend, "
                f"verify ~/.forest/secrets/secrets.yaml is chmod 600)."
            ),
        ) from e

    return {
        "backend": store.name,
        "count": len(names),
        "names": sorted(names),
    }
