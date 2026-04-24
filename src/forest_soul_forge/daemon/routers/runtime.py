"""``/runtime/provider`` — inspect and switch the active model provider.

Switching is an in-process mutation. A fresh daemon restart always comes
back up on ``default_provider`` (``"local"`` out of the box per
ADR-0008) — so an accidental flip never becomes a silent policy change.

The UI "button" on the frontend calls PUT here. Scripts can also curl it.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from forest_soul_forge.daemon.deps import (
    get_provider_registry,
    require_api_token,
)
from forest_soul_forge.daemon.providers import (
    ProviderRegistry,
    UnknownProviderError,
)
from forest_soul_forge.daemon.schemas import (
    ProviderHealthOut,
    ProviderInfoOut,
    SetProviderIn,
)

router = APIRouter(prefix="/runtime", tags=["runtime"])


async def _build_info(providers: ProviderRegistry) -> ProviderInfoOut:
    active = providers.active()
    health = await active.healthcheck()
    return ProviderInfoOut(
        active=providers.active_name,
        default=providers.default_name,
        known=providers.known(),
        health=ProviderHealthOut(
            name=health.name,
            status=health.status,
            base_url=health.base_url,
            models=health.models,
            details=health.details,
            error=health.error,
        ),
    )


@router.get("/provider", response_model=ProviderInfoOut)
async def get_provider(
    providers: ProviderRegistry = Depends(get_provider_registry),
) -> ProviderInfoOut:
    return await _build_info(providers)


@router.put(
    "/provider",
    response_model=ProviderInfoOut,
    dependencies=[Depends(require_api_token)],
)
async def set_provider(
    payload: SetProviderIn,
    providers: ProviderRegistry = Depends(get_provider_registry),
) -> ProviderInfoOut:
    try:
        providers.set_active(payload.provider)
    except UnknownProviderError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    return await _build_info(providers)
