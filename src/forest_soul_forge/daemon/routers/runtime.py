"""``/runtime/provider`` — inspect and switch the active model provider.

Switching is an in-process mutation. A fresh daemon restart always comes
back up on ``default_provider`` (``"local"`` out of the box per
ADR-0008) — so an accidental flip never becomes a silent policy change.

The UI "button" on the frontend calls PUT here. Scripts can also curl it.

Phase 4: ``POST /runtime/provider/generate`` exposes the active provider's
``complete()`` method as an HTTP endpoint so external callers can route
LLM requests through the daemon (consistent provider stack, consistent
auth, consistent task-kind routing) rather than calling SDK-shaped APIs
directly. Auth-protected — when ``FSF_API_TOKEN`` is set, callers must
present the matching ``X-FSF-Token`` header.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from forest_soul_forge.daemon.deps import (
    get_provider_registry,
    require_api_token,
)
from forest_soul_forge.daemon.providers import (
    ProviderDisabled,
    ProviderError,
    ProviderRegistry,
    ProviderUnavailable,
    TaskKind,
    UnknownProviderError,
)
from forest_soul_forge.daemon.schemas import (
    GenerateRequest,
    GenerateResponse,
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


def _resolve_model_tag(provider: object, task_kind: TaskKind) -> str:
    """Pull the model tag a provider would use for this task_kind.

    The provider Protocol doesn't mandate a ``models`` attribute — only
    ``name``, ``complete``, and ``healthcheck``. Both shipped concrete
    implementations (Local, Frontier) DO expose ``.models`` as a
    ``dict[TaskKind, str]``, so we read it when present and fall back to
    ``"unknown"`` if a future provider opts out. Callers shouldn't rely
    on this being authoritative — for that, query ``/runtime/provider``
    which round-trips through the live healthcheck.
    """
    models = getattr(provider, "models", None)
    if not isinstance(models, dict):
        return "unknown"
    # ``models`` may be keyed by TaskKind enum members or by their string
    # values depending on the provider; check both.
    return (
        models.get(task_kind)
        or models.get(task_kind.value)
        or models.get(TaskKind.CONVERSATION)
        or models.get(TaskKind.CONVERSATION.value)
        or "unknown"
    )


@router.post(
    "/provider/generate",
    response_model=GenerateResponse,
    dependencies=[Depends(require_api_token)],
    responses={
        502: {"description": "Provider returned an error response."},
        503: {"description": "Active provider is unavailable or disabled."},
    },
)
async def generate(
    payload: GenerateRequest,
    providers: ProviderRegistry = Depends(get_provider_registry),
) -> GenerateResponse:
    """Run a one-shot completion against the currently active provider.

    Errors are mapped explicitly so callers can distinguish "I need to
    start Ollama" (503 unavailable) from "the provider is intentionally
    off by config" (503 disabled) from "the provider answered with a
    non-2xx" (502 bad gateway). 500 is reserved for actual daemon bugs.
    """
    provider = providers.active()

    # Build the kwargs the provider expects. ``temperature`` is provider-
    # specific (Ollama nests it under ``options``, OpenAI takes it as a
    # top-level field); both providers we ship handle it correctly when
    # passed as a bare kwarg.
    extra_kwargs: dict[str, object] = {}
    if payload.temperature is not None:
        extra_kwargs["temperature"] = payload.temperature

    try:
        response_text = await provider.complete(
            payload.prompt,
            task_kind=payload.task_kind,
            system=payload.system,
            max_tokens=payload.max_tokens,
            **extra_kwargs,
        )
    except ProviderUnavailable as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"provider unavailable: {e}",
        ) from e
    except ProviderDisabled as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"provider disabled: {e}",
        ) from e
    except ProviderError as e:
        # Catch-all for upstream non-2xx / shape errors. 502 communicates
        # that the daemon was reachable but the upstream said no.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"provider error: {e}",
        ) from e

    return GenerateResponse(
        response=response_text,
        provider=providers.active_name,
        model=_resolve_model_tag(provider, payload.task_kind),
        task_kind=payload.task_kind,
    )
