"""``/agents/{instance_id}/memory/consents`` — per-event consent grants.

ADR-0022 v0.2 + ADR-0027 §2. Three endpoints:

* ``POST   /agents/{instance_id}/memory/consents``                  — grant
* ``DELETE /agents/{instance_id}/memory/consents/{entry_id}/{recipient}`` — revoke
* ``GET    /agents/{instance_id}/memory/consents``                  — list

The ``{instance_id}`` in the URL is the **owner of the memory** — the
agent that wrote the entry being consented. The owner is the only
agent that can grant or revoke consent on its own entries; the
endpoint refuses if the entry isn't owned by the URL's instance.

Each successful grant emits ``memory_consent_granted`` on the audit
chain; each revoke emits ``memory_consent_revoked``. Both events
record the entry id, recipient, and granting/revoking principal
(``operator`` for now; future tranches add agent-initiated grants).

Bulk ops are **not** supported — each grant/revoke is its own
endpoint call so the audit chain has one event per information-flow
boundary crossing (ADR-0027 §6 — "an attacker who got operator
approval to consolidate memories should not be able to disclose a
thousand entries inside a single audit line").
"""
from __future__ import annotations

from threading import Lock

from fastapi import APIRouter, Depends, HTTPException, Request, status

from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.core.memory import Memory
from forest_soul_forge.daemon.deps import (
    get_audit_chain,
    get_registry,
    get_write_lock,
    require_api_token,
    require_writes_enabled,
)
from forest_soul_forge.daemon.schemas import (
    MemoryConsentGrantRequest,
    MemoryConsentGrantResponse,
    MemoryConsentListResponse,
    MemoryConsentOut,
)


router = APIRouter(tags=["memory"])


def _resolve_memory(registry) -> Memory:  # noqa: ANN001 — Registry annotated below
    """Build a Memory bound to the registry's connection. Same single-
    writer discipline as the dispatcher path — callers must hold the
    write lock for any mutation."""
    return Memory(conn=registry._conn)  # noqa: SLF001 — internal access by design


def _verify_owner(memory: Memory, entry_id: str, owner_instance: str) -> None:
    """Refuse if the entry isn't owned by ``owner_instance``. The owner
    is the principal that decides who else gets to read it; consent is
    not delegable in v0.2."""
    entry = memory.get(entry_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"memory entry {entry_id!r} not found",
        )
    if entry.instance_id != owner_instance:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"entry {entry_id!r} is owned by {entry.instance_id!r}, "
                f"not by {owner_instance!r}; only the owner may grant or "
                "revoke consent on its entries"
            ),
        )
    if entry.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"entry {entry_id!r} is deleted; consent operations refused",
        )


def _verify_recipient_exists(memory: Memory, recipient: str) -> None:
    row = memory.conn.execute(
        "SELECT 1 FROM agents WHERE instance_id = ? LIMIT 1;",
        (recipient,),
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"recipient agent {recipient!r} not found",
        )


@router.post(
    "/agents/{instance_id}/memory/consents",
    response_model=MemoryConsentGrantResponse,
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
async def grant_consent(
    instance_id: str,
    payload: MemoryConsentGrantRequest,
    request: Request,
    audit: AuditChain = Depends(get_audit_chain),
    registry=Depends(get_registry),
    write_lock: Lock = Depends(get_write_lock),
) -> MemoryConsentGrantResponse:
    """Grant consent on one of ``instance_id``'s entries to ``recipient``.

    Idempotent on (entry_id, recipient): re-granting an existing grant
    refreshes ``granted_at`` and clears any prior ``revoked_at``.
    """
    if payload.recipient_instance == instance_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "recipient_instance must differ from the owner; "
                "self-consent is meaningless"
            ),
        )

    with write_lock:
        memory = _resolve_memory(registry)
        _verify_owner(memory, payload.entry_id, instance_id)
        _verify_recipient_exists(memory, payload.recipient_instance)
        memory.grant_consent(
            entry_id=payload.entry_id,
            recipient_instance=payload.recipient_instance,
            granted_by="operator",
        )
        # Audit-chain emission. agent_dna scopes the event to the
        # owner's lineage so chain queries by agent surface this
        # alongside the owner's other memory events.
        owner_row = memory.conn.execute(
            "SELECT dna FROM agents WHERE instance_id=? LIMIT 1;",
            (instance_id,),
        ).fetchone()
        owner_dna = owner_row[0] if owner_row else None
        audit.append(
            "memory_consent_granted",
            {
                "owner_instance":     instance_id,
                "entry_id":           payload.entry_id,
                "recipient_instance": payload.recipient_instance,
                "granted_by":         "operator",
            },
            agent_dna=owner_dna,
        )

    return MemoryConsentGrantResponse(
        owner_instance=instance_id,
        entry_id=payload.entry_id,
        recipient_instance=payload.recipient_instance,
        revoked=False,
    )


@router.delete(
    "/agents/{instance_id}/memory/consents/{entry_id}/{recipient_instance}",
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
async def revoke_consent(
    instance_id: str,
    entry_id: str,
    recipient_instance: str,
    request: Request,
    audit: AuditChain = Depends(get_audit_chain),
    registry=Depends(get_registry),
    write_lock: Lock = Depends(get_write_lock),
) -> MemoryConsentGrantResponse:
    """Revoke a previously granted consent.

    Per ADR-0027 §2 — withdrawal does NOT propagate to copies the
    recipient already disclosed; that's the deletion contract's job.
    The chain entry records the revocation; downstream is a social
    problem, not a technical one.

    Returns 404 if no active grant exists for the (entry, recipient)
    pair — distinct from "entry not found" so the operator gets a
    precise error.
    """
    with write_lock:
        memory = _resolve_memory(registry)
        _verify_owner(memory, entry_id, instance_id)
        # We don't verify recipient existence here; the recipient may
        # have been archived, and revoking a stale grant is still
        # legitimate (operator wants the consent line off the books).
        revoked = memory.revoke_consent(
            entry_id=entry_id,
            recipient_instance=recipient_instance,
        )
        if not revoked:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"no active consent grant for entry {entry_id!r} → "
                    f"recipient {recipient_instance!r}"
                ),
            )
        owner_row = memory.conn.execute(
            "SELECT dna FROM agents WHERE instance_id=? LIMIT 1;",
            (instance_id,),
        ).fetchone()
        owner_dna = owner_row[0] if owner_row else None
        audit.append(
            "memory_consent_revoked",
            {
                "owner_instance":     instance_id,
                "entry_id":           entry_id,
                "recipient_instance": recipient_instance,
            },
            agent_dna=owner_dna,
        )

    return MemoryConsentGrantResponse(
        owner_instance=instance_id,
        entry_id=entry_id,
        recipient_instance=recipient_instance,
        revoked=True,
    )


@router.get(
    "/agents/{instance_id}/memory/consents",
    response_model=MemoryConsentListResponse,
)
async def list_consents(
    instance_id: str,
    request: Request,
    registry=Depends(get_registry),
) -> MemoryConsentListResponse:
    """List all consent grants the agent has issued on its own entries.

    Read-only; does not emit an audit event (the chain already records
    every grant + revoke). Useful for the frontend memory tab.
    """
    memory = _resolve_memory(registry)
    rows = memory.conn.execute(
        """
        SELECT mc.entry_id, mc.recipient_instance,
               mc.granted_at, mc.granted_by, mc.revoked_at
          FROM memory_consents mc
          JOIN memory_entries  me ON me.entry_id = mc.entry_id
         WHERE me.instance_id = ?
         ORDER BY mc.granted_at DESC;
        """,
        (instance_id,),
    ).fetchall()
    out = [
        MemoryConsentOut(
            entry_id=r[0] if not hasattr(r, "keys") else r["entry_id"],
            recipient_instance=r[1] if not hasattr(r, "keys") else r["recipient_instance"],
            granted_at=r[2] if not hasattr(r, "keys") else r["granted_at"],
            granted_by=r[3] if not hasattr(r, "keys") else r["granted_by"],
            revoked_at=r[4] if not hasattr(r, "keys") else r["revoked_at"],
        )
        for r in rows
    ]
    return MemoryConsentListResponse(
        owner_instance=instance_id,
        count=len(out),
        consents=out,
    )
