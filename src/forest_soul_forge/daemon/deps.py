"""Dependency-injection helpers for the daemon.

Two shared objects live on ``app.state``:

* ``app.state.registry`` — a single open :class:`Registry` for the
  process. WAL mode means concurrent reads are fine; writes go through
  an asyncio lock (future Phase 3 write endpoints).
* ``app.state.providers`` — the :class:`ProviderRegistry`.

Routers reach these via the helpers below so the dependency graph is
explicit and testable.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, Request, status

from forest_soul_forge.daemon.providers import (
    ModelProvider,
    ProviderRegistry,
    UnknownProviderError,
)

if TYPE_CHECKING:
    from forest_soul_forge.registry import Registry


def get_registry(request: Request) -> "Registry":
    reg = getattr(request.app.state, "registry", None)
    if reg is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="registry not initialized",
        )
    return reg


def get_provider_registry(request: Request) -> ProviderRegistry:
    pr = getattr(request.app.state, "providers", None)
    if pr is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="provider registry not initialized",
        )
    return pr


def get_active_provider(
    providers: ProviderRegistry = Depends(get_provider_registry),
) -> ModelProvider:
    return providers.active()


def get_provider_by_name(
    name: str,
    providers: ProviderRegistry = Depends(get_provider_registry),
) -> ModelProvider:
    try:
        return providers.get(name)
    except UnknownProviderError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
