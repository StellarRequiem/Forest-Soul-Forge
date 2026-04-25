"""``/healthz`` — liveness + registry version + active provider health.

Intentionally cheap: the local provider's healthcheck is the slowest
thing here (Ollama tag listing, typically <100ms). Frontier's health is
a local-only check (no credit burn).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from fastapi import Request

from forest_soul_forge.daemon.deps import (
    get_provider_registry,
    get_registry,
    get_settings,
)
from forest_soul_forge.daemon.providers import ProviderRegistry
from forest_soul_forge.daemon.schemas import (
    HealthOut,
    ProviderHealthOut,
    StartupDiagnostic,
)
from forest_soul_forge.registry import Registry

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthOut)
async def healthz(
    request: Request,
    registry: Registry = Depends(get_registry),
    providers: ProviderRegistry = Depends(get_provider_registry),
    settings=Depends(get_settings),
) -> HealthOut:
    active = providers.active()
    health = await active.healthcheck()
    # Read the canonical_contract marker from registry_meta — it's the
    # tripwire that says "this DB is a derived index, not authoritative".
    row = registry._conn.execute(  # type: ignore[attr-defined]
        "SELECT value FROM registry_meta WHERE key='canonical_contract';"
    ).fetchone()
    canonical_contract = row["value"] if row is not None else ""
    diags_raw = getattr(request.app.state, "startup_diagnostics", []) or []
    diags = [StartupDiagnostic(**d) for d in diags_raw]
    return HealthOut(
        ok=True,
        schema_version=registry.schema_version(),
        canonical_contract=canonical_contract,
        active_provider=providers.active_name,
        provider=ProviderHealthOut(
            name=health.name,
            status=health.status,
            base_url=health.base_url,
            models=health.models,
            details=health.details,
            error=health.error,
        ),
        auth_required=getattr(settings, "api_token", None) is not None,
        writes_enabled=bool(getattr(settings, "allow_write_endpoints", False)),
        startup_diagnostics=diags,
    )
