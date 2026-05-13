"""``/agents/{instance_id}/passport`` — ADR-0061 T6 (Burst 248) passport mint.

This is the operator-facing HTTP surface for the agent-passport
substrate (mint primitives in ``security/passport.py`` + operator
master key in ``security/operator_key.py``, both shipped B246).

The endpoint is intentionally narrow:

  - **POST** ``/agents/{instance_id}/passport`` — mint a new
    passport authorizing the agent on a given fingerprint set.
    Replaces any existing ``passport.json`` next to the agent's
    constitution. The operator's master keypair is resolved
    server-side via :func:`resolve_operator_keypair` — the
    operator never handles the private key in this flow.

No GET / DELETE today. Reasons:

  - **GET**: ``passport.json`` is plain JSON next to the agent
    artifacts. Operators with read access already see it; no
    need for a dedicated endpoint.
  - **DELETE**: deleting passport.json is a filesystem op an
    operator can do themselves; we don't want the daemon
    silently rm'ing files. Re-minting with a past ``expires_at``
    revokes via expiry, which is auditable.

Refused passports (K6 quarantine surfaced the dispatcher
``passport_reason``) emit ``agent_passport_refused`` from the
dispatcher path; this router emits ``agent_passport_minted`` on
success.
"""
from __future__ import annotations

import base64
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.daemon.deps import (
    get_audit_chain,
    get_registry,
    get_write_lock,
    require_api_token,
    require_writes_enabled,
)
from forest_soul_forge.daemon.schemas import (
    PassportMintRequest,
    PassportMintResponse,
)
from forest_soul_forge.registry import Registry
from forest_soul_forge.registry.registry import UnknownAgentError
from forest_soul_forge.security.operator_key import resolve_operator_keypair
from forest_soul_forge.security.passport import (
    PassportError,
    PassportFormatError,
    mint_passport,
)

router = APIRouter(prefix="/agents", tags=["passport"])


@router.post(
    "/{instance_id}/passport",
    response_model=PassportMintResponse,
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
def mint_passport_endpoint(
    instance_id: str,
    body: PassportMintRequest,
    registry: Registry = Depends(get_registry),
    audit: AuditChain = Depends(get_audit_chain),
    write_lock: threading.Lock = Depends(get_write_lock),
) -> PassportMintResponse:
    # 1. Resolve agent (existence + identity).
    try:
        agent = registry.get_agent(instance_id)
    except UnknownAgentError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"agent {instance_id!r} not found",
        )

    # 2. Pull the agent's public key from the agents table. Per
    #    ADR-0049 T4 (B243) every freshly-born agent has one; legacy
    #    pre-B243 agents have NULL and can't have passports minted
    #    until they're re-keyed.
    row = registry._conn.execute(  # noqa: SLF001 — registry has no public read shim for this column yet
        "SELECT public_key FROM agents WHERE instance_id = ? LIMIT 1;",
        (instance_id,),
    ).fetchone()
    if row is None or row[0] is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"agent {instance_id!r} has no public_key on file "
                "(legacy pre-ADR-0049 agent). Re-birth or run a "
                "key-issuance migration before minting a passport."
            ),
        )
    agent_public_key_b64 = row[0]

    # 3. Resolve the operator master keypair (auto-generated on
    #    first daemon boot per ADR-0061 D1).
    try:
        priv_bytes, issuer_pub_b64 = resolve_operator_keypair()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"operator master keypair unavailable: {e}",
        ) from e

    # 4. Mint via the primitive layer. Format errors surface as 400
    #    so the operator sees what's wrong (e.g. malformed
    #    expires_at).
    issued_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        passport = mint_passport(
            agent_dna=agent.dna,
            instance_id=instance_id,
            agent_public_key_b64=agent_public_key_b64,
            authorized_fingerprints=list(body.authorized_fingerprints),
            operator_private_key=priv_bytes,
            issuer_public_key_b64=issuer_pub_b64,
            issued_at=issued_at,
            expires_at=body.expires_at,
        )
    except PassportFormatError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e),
        ) from e
    except PassportError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"passport mint failed: {e}",
        ) from e

    # 5. Persist passport.json next to the constitution + emit audit
    #    event under the write lock so concurrent mints don't race.
    const_path = Path(agent.constitution_path)
    if not const_path.exists():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"constitution file missing for {instance_id}: "
                f"{const_path}"
            ),
        )
    passport_path = const_path.parent / "passport.json"
    with write_lock:
        tmp_path = passport_path.with_suffix(passport_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(passport, indent=2), encoding="utf-8")
        tmp_path.replace(passport_path)

        # event_data carries the COUNT of fingerprints rather than
        # the list — the full list lives in passport.json and we
        # don't want to redundantly inflate every audit entry. The
        # issuer_public_key is included so an auditor can verify
        # *which* operator's master signed THIS mint without
        # cross-referencing the file (operators rotating their
        # master would otherwise create ambiguity).
        entry = audit.append(
            "agent_passport_minted",
            {
                "instance_id":                instance_id,
                "issuer_public_key":          issuer_pub_b64,
                "authorized_fingerprint_count": len(
                    body.authorized_fingerprints,
                ),
                "issued_at":                  issued_at,
                "expires_at":                 body.expires_at,
                "operator_id":                body.operator_id,
                "reason":                     body.reason,
                "passport_path":              str(passport_path),
            },
            agent_dna=agent.dna,
        )

    return PassportMintResponse(
        instance_id=instance_id,
        issuer_public_key=issuer_pub_b64,
        authorized_fingerprints=list(body.authorized_fingerprints),
        issued_at=issued_at,
        expires_at=body.expires_at,
        passport_path=str(passport_path),
        seq=entry.seq,
        timestamp=entry.timestamp,
    )
