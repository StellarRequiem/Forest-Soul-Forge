"""``/operator/*`` router — ADR-0068 T7 (B317) operator-facing surface.

Two endpoints today:

  - **GET /operator/profile/connectors** — list current consent
    state across all domain-connector pairs. Drives the first-boot
    wizard's "what still needs a decision?" view.

  - **POST /operator/connectors/{domain_id}/{connector_name}** —
    upsert one ConnectorConsent. Body: ``{"status": "granted|denied|
    pending", "notes": "..."}``. Emits
    ``operator_connector_consent_changed`` to the audit chain with
    before/after status + notes.

Both endpoints gated by ``require_writes_enabled +
require_api_token`` for the POST; the GET is just
``require_api_token`` (read-only).
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from forest_soul_forge.core.operator_profile import (
    OperatorProfileError,
    default_operator_profile_path,
    load_operator_profile,
    save_operator_profile,
    upsert_connector_consent,
)
from forest_soul_forge.daemon.deps import (
    get_audit_chain,
    require_api_token,
    require_writes_enabled,
)


router = APIRouter(prefix="/operator", tags=["operator"])


class ConsentBody(BaseModel):
    status: str = Field(
        ..., pattern="^(granted|denied|pending)$",
        description="New consent status for this connector.",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Operator-supplied rationale captured in the audit chain.",
    )


def _load_profile(request: Request):
    """Resolve encryption-aware load. Mirrors the
    operator_profile_read tool's pattern."""
    enc_cfg = None
    master_key = getattr(request.app.state, "master_key", None)
    if master_key is not None:
        from forest_soul_forge.core.at_rest_encryption import (
            EncryptionConfig,
        )
        enc_cfg = EncryptionConfig(master_key=master_key)
    try:
        return load_operator_profile(
            default_operator_profile_path(),
            encryption_config=enc_cfg,
        ), enc_cfg
    except OperatorProfileError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"operator profile unavailable: {e}",
        )


@router.get(
    "/profile/connectors",
    dependencies=[Depends(require_api_token)],
)
def list_connectors(request: Request) -> dict[str, Any]:
    """Return the current consent state for every (domain_id,
    connector_name) pair on the profile.

    Drives the first-boot wizard's "what still needs a decision?"
    view + the operator's settings pane.
    """
    profile, _ = _load_profile(request)
    return {
        "schema_version": 1,
        "operator_id":    profile.operator_id,
        "connectors": [
            {
                "domain_id":      c.domain_id,
                "connector_name": c.connector_name,
                "status":         c.status,
                "decided_at":     c.decided_at,
                "notes":          c.notes,
            }
            for c in profile.connectors
        ],
    }


@router.post(
    "/connectors/{domain_id}/{connector_name}",
    dependencies=[
        Depends(require_writes_enabled), Depends(require_api_token),
    ],
)
def upsert_consent(
    domain_id: str,
    connector_name: str,
    body: ConsentBody,
    request: Request,
    audit=Depends(get_audit_chain),
) -> dict[str, Any]:
    """Upsert one ConnectorConsent on the profile.

    Path params identify the connector; body carries the new
    status + optional notes. The endpoint:

      1. Loads the current profile.
      2. Finds the existing entry (if any) for the (domain_id,
         connector_name) pair so before/after diff has shape.
      3. Calls upsert_connector_consent to produce the new profile.
      4. Saves atomically.
      5. Emits operator_connector_consent_changed.

    Returns the new entry + the before-status (None when this is
    a fresh insert).
    """
    profile, enc_cfg = _load_profile(request)

    # Find old status for the audit emit.
    old_status: Optional[str] = None
    for c in profile.connectors:
        if (c.domain_id, c.connector_name) == (domain_id, connector_name):
            old_status = c.status
            break

    try:
        new_profile = upsert_connector_consent(
            profile,
            domain_id=domain_id,
            connector_name=connector_name,
            status=body.status,
            notes=body.notes,
        )
    except OperatorProfileError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    try:
        save_operator_profile(
            new_profile,
            default_operator_profile_path(),
            encryption_config=enc_cfg,
        )
    except OperatorProfileError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"save failed: {e}",
        )

    # Emit the audit event. Best-effort — disk state already
    # changed, the chain entry is the evidence layer.
    if audit is not None:
        try:
            audit.append(
                "operator_connector_consent_changed",
                {
                    "domain_id":       domain_id,
                    "connector_name":  connector_name,
                    "old_status":      old_status,
                    "new_status":      body.status,
                    "notes":           body.notes,
                    "operator_id":     profile.operator_id,
                },
                agent_dna=None,
            )
        except Exception:
            pass

    # Find the new entry to return.
    new_entry = next(
        c for c in new_profile.connectors
        if (c.domain_id, c.connector_name) == (domain_id, connector_name)
    )
    return {
        "ok":               True,
        "domain_id":        domain_id,
        "connector_name":   connector_name,
        "old_status":       old_status,
        "new_status":       new_entry.status,
        "decided_at":       new_entry.decided_at,
        "notes":            new_entry.notes,
    }
