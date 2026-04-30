"""``/conversations`` — ADR-003Y Y1 conversation runtime CRUD.

Y1 ships only the data-model layer: create / get / list / archive
conversations, add / list / remove participants, append turns by an
operator, list turns. NO orchestration — multi-agent turn passing,
@mention resolution, ambient-mode quotas, lazy summarization all live
in Y2-Y7.

Templated on hardware.py (smallest existing K-track router) per the
2026-04-30 load-bearing survey recommendation #5. write_lock + audit
emission discipline preserved; no new state lives outside the registry.
"""
from __future__ import annotations

import threading
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status

from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.daemon.deps import (
    get_audit_chain,
    get_registry,
    get_write_lock,
    require_writes_enabled,
)
from forest_soul_forge.daemon.schemas import (
    ConversationCreateRequest,
    ConversationListOut,
    ConversationOut,
    ConversationStatusUpdateRequest,
    ParticipantAddRequest,
    ParticipantListOut,
    ParticipantOut,
    RetentionPolicyUpdateRequest,
    TurnAppendRequest,
    TurnListOut,
    TurnOut,
)
from forest_soul_forge.registry import Registry
from forest_soul_forge.registry.registry import UnknownAgentError
from forest_soul_forge.registry.tables import ConversationNotFoundError

router = APIRouter(prefix="/conversations", tags=["conversations"])


# ---------------------------------------------------------------------------
# Helpers — adapt registry dataclasses to Pydantic response shapes.
# ---------------------------------------------------------------------------
def _conversation_out(row: Any) -> ConversationOut:
    return ConversationOut(
        conversation_id=row.conversation_id,
        domain=row.domain,
        operator_id=row.operator_id,
        created_at=row.created_at,
        last_turn_at=row.last_turn_at,
        status=row.status,
        retention_policy=row.retention_policy,
    )


def _participant_out(row: Any) -> ParticipantOut:
    return ParticipantOut(
        conversation_id=row.conversation_id,
        instance_id=row.instance_id,
        joined_at=row.joined_at,
        bridged_from=row.bridged_from,
    )


def _turn_out(row: Any) -> TurnOut:
    return TurnOut(
        turn_id=row.turn_id,
        conversation_id=row.conversation_id,
        speaker=row.speaker,
        addressed_to=row.addressed_to,
        body=row.body,
        summary=row.summary,
        body_hash=row.body_hash,
        token_count=row.token_count,
        timestamp=row.timestamp,
        model_used=row.model_used,
    )


# ---------------------------------------------------------------------------
# Conversations CRUD
# ---------------------------------------------------------------------------
@router.post(
    "",
    response_model=ConversationOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_writes_enabled)],
)
def create_conversation(
    body:       ConversationCreateRequest,
    registry:   Registry      = Depends(get_registry),
    audit:      AuditChain    = Depends(get_audit_chain),
    write_lock: threading.Lock = Depends(get_write_lock),
) -> ConversationOut:
    """Open a new conversation. Emits ``conversation_started`` audit event."""
    cid = str(uuid4())
    with write_lock:
        try:
            row = registry.conversations.create_conversation(
                domain=body.domain.strip(),
                operator_id=body.operator_id.strip(),
                retention_policy=body.retention_policy,
                conversation_id=cid,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        try:
            audit.append(
                "conversation_started",
                {
                    "conversation_id":  row.conversation_id,
                    "domain":           row.domain,
                    "operator_id":      row.operator_id,
                    "retention_policy": row.retention_policy,
                },
                agent_dna=None,
            )
        except Exception:
            # Audit emit failure should not fail the create — the row
            # exists; the operator can re-emit context separately.
            pass
    return _conversation_out(row)


@router.get("/{conversation_id}", response_model=ConversationOut)
def get_conversation(
    conversation_id: str,
    registry:        Registry = Depends(get_registry),
) -> ConversationOut:
    try:
        row = registry.conversations.get_conversation(conversation_id)
    except ConversationNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"conversation {conversation_id!r} not found",
        )
    return _conversation_out(row)


@router.get("", response_model=ConversationListOut)
def list_conversations(
    domain:      str | None = Query(default=None),
    operator_id: str | None = Query(default=None),
    status_:     str | None = Query(default=None, alias="status"),
    limit:       int        = Query(default=100, ge=1, le=500),
    offset:      int        = Query(default=0, ge=0),
    registry:    Registry   = Depends(get_registry),
) -> ConversationListOut:
    rows = registry.conversations.list_conversations(
        domain=domain, operator_id=operator_id, status=status_,
        limit=limit, offset=offset,
    )
    return ConversationListOut(
        conversations=[_conversation_out(r) for r in rows],
        limit=limit, offset=offset,
    )


@router.post(
    "/{conversation_id}/status",
    response_model=ConversationOut,
    dependencies=[Depends(require_writes_enabled)],
)
def update_status(
    conversation_id: str,
    body:            ConversationStatusUpdateRequest,
    registry:        Registry      = Depends(get_registry),
    audit:           AuditChain    = Depends(get_audit_chain),
    write_lock:      threading.Lock = Depends(get_write_lock),
) -> ConversationOut:
    """Move the conversation to a new status. ``archived`` emits
    ``conversation_archived``; other transitions emit
    ``conversation_status_changed``."""
    with write_lock:
        try:
            registry.conversations.set_conversation_status(
                conversation_id, body.status,
            )
        except ConversationNotFoundError:
            raise HTTPException(status_code=404, detail=f"conversation {conversation_id!r} not found")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        event_type = (
            "conversation_archived" if body.status == "archived"
            else "conversation_status_changed"
        )
        try:
            audit.append(
                event_type,
                {
                    "conversation_id": conversation_id,
                    "new_status":      body.status,
                    "reason":          body.reason,
                },
                agent_dna=None,
            )
        except Exception:
            pass
        row = registry.conversations.get_conversation(conversation_id)
    return _conversation_out(row)


@router.post(
    "/{conversation_id}/retention",
    response_model=ConversationOut,
    dependencies=[Depends(require_writes_enabled)],
)
def update_retention(
    conversation_id: str,
    body:            RetentionPolicyUpdateRequest,
    registry:        Registry      = Depends(get_registry),
    audit:           AuditChain    = Depends(get_audit_chain),
    write_lock:      threading.Lock = Depends(get_write_lock),
) -> ConversationOut:
    """Change the retention policy. Emits ``retention_policy_changed``."""
    with write_lock:
        try:
            registry.conversations.set_retention_policy(conversation_id, body.policy)
        except ConversationNotFoundError:
            raise HTTPException(status_code=404, detail=f"conversation {conversation_id!r} not found")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        try:
            audit.append(
                "retention_policy_changed",
                {
                    "conversation_id": conversation_id,
                    "new_policy":      body.policy,
                    "reason":          body.reason,
                },
                agent_dna=None,
            )
        except Exception:
            pass
        row = registry.conversations.get_conversation(conversation_id)
    return _conversation_out(row)


# ---------------------------------------------------------------------------
# Participants
# ---------------------------------------------------------------------------
@router.post(
    "/{conversation_id}/participants",
    response_model=ParticipantOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_writes_enabled)],
)
def add_participant(
    conversation_id: str,
    body:            ParticipantAddRequest,
    registry:        Registry      = Depends(get_registry),
    audit:           AuditChain    = Depends(get_audit_chain),
    write_lock:      threading.Lock = Depends(get_write_lock),
) -> ParticipantOut:
    """Add an agent to a conversation. Idempotent on
    (conversation_id, instance_id). When ``bridged_from`` is supplied,
    emits ``conversation_bridged`` for cross-domain visibility."""
    with write_lock:
        # Ensure both refs exist for clearer error messages.
        try:
            registry.conversations.get_conversation(conversation_id)
        except ConversationNotFoundError:
            raise HTTPException(status_code=404, detail=f"conversation {conversation_id!r} not found")
        try:
            registry.get_agent(body.instance_id)
        except UnknownAgentError:
            raise HTTPException(status_code=404, detail=f"agent {body.instance_id!r} not found")

        row = registry.conversations.add_participant(
            conversation_id, body.instance_id, bridged_from=body.bridged_from,
        )
        event_type = (
            "conversation_bridged" if body.bridged_from else "conversation_participant_joined"
        )
        try:
            audit.append(
                event_type,
                {
                    "conversation_id": conversation_id,
                    "instance_id":     body.instance_id,
                    "bridged_from":    body.bridged_from,
                },
                agent_dna=None,
            )
        except Exception:
            pass
    return _participant_out(row)


@router.get(
    "/{conversation_id}/participants",
    response_model=ParticipantListOut,
)
def list_participants(
    conversation_id: str,
    registry:        Registry = Depends(get_registry),
) -> ParticipantListOut:
    try:
        registry.conversations.get_conversation(conversation_id)
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail=f"conversation {conversation_id!r} not found")
    rows = registry.conversations.list_participants(conversation_id)
    return ParticipantListOut(participants=[_participant_out(r) for r in rows])


@router.delete(
    "/{conversation_id}/participants/{instance_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_writes_enabled)],
)
def remove_participant(
    conversation_id: str,
    instance_id:     str,
    registry:        Registry      = Depends(get_registry),
    audit:           AuditChain    = Depends(get_audit_chain),
    write_lock:      threading.Lock = Depends(get_write_lock),
):
    """Remove an agent from a conversation. Idempotent — returns 204 even
    if the participant wasn't present (so callers can use it defensively).
    Emits ``conversation_participant_left`` only when an actual row was
    removed."""
    with write_lock:
        removed = registry.conversations.remove_participant(
            conversation_id, instance_id,
        )
        if removed:
            try:
                audit.append(
                    "conversation_participant_left",
                    {"conversation_id": conversation_id, "instance_id": instance_id},
                    agent_dna=None,
                )
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Turns
# ---------------------------------------------------------------------------
@router.post(
    "/{conversation_id}/turns",
    response_model=TurnOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_writes_enabled)],
)
def append_turn(
    conversation_id: str,
    body:            TurnAppendRequest,
    registry:        Registry      = Depends(get_registry),
    audit:           AuditChain    = Depends(get_audit_chain),
    write_lock:      threading.Lock = Depends(get_write_lock),
) -> TurnOut:
    """Append a turn. Y1 supports operator-spoken turns; agent turns
    in Y2 will reuse this endpoint with ``speaker=instance_id`` once
    the orchestrator wires through.

    Emits ``conversation_turn`` (without body — body_hash is the
    integrity proof; full content lives in the registry until the
    retention window expires)."""
    addressed_to_str = (
        ",".join(body.addressed_to) if body.addressed_to else None
    )
    with write_lock:
        try:
            row = registry.conversations.append_turn(
                conversation_id=conversation_id,
                speaker=body.speaker,
                body=body.body,
                addressed_to=addressed_to_str,
                token_count=body.token_count,
                model_used=body.model_used,
            )
        except ConversationNotFoundError:
            raise HTTPException(status_code=404, detail=f"conversation {conversation_id!r} not found")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        try:
            audit.append(
                "conversation_turn",
                {
                    "conversation_id": conversation_id,
                    "turn_id":         row.turn_id,
                    "speaker":         row.speaker,
                    "addressed_to":    row.addressed_to,
                    "body_hash":       row.body_hash,
                    "token_count":     row.token_count,
                    "model_used":      row.model_used,
                },
                agent_dna=None,
            )
        except Exception:
            pass
    return _turn_out(row)


@router.get(
    "/{conversation_id}/turns",
    response_model=TurnListOut,
)
def list_turns(
    conversation_id: str,
    limit:           int = Query(default=100, ge=1, le=500),
    offset:          int = Query(default=0, ge=0),
    registry:        Registry = Depends(get_registry),
) -> TurnListOut:
    try:
        registry.conversations.get_conversation(conversation_id)
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail=f"conversation {conversation_id!r} not found")
    rows = registry.conversations.list_turns(
        conversation_id, limit=limit, offset=offset,
    )
    return TurnListOut(
        turns=[_turn_out(r) for r in rows],
        limit=limit, offset=offset,
    )
