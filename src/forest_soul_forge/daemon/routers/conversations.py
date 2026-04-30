"""``/conversations`` — ADR-003Y conversation runtime endpoints.

Y1 shipped the data-model layer (CRUD on conversations / participants
/ turns). Y2 adds the optional ``auto_respond`` flag on
``POST /turns`` that triggers single-agent orchestration: when there
is exactly one agent participant in the room, the router dispatches
``llm_think.v1`` to that agent with prior conversation history as
context, appends the response as the next turn, and returns both
turns in one response.

Multi-agent turn passing, @mention resolution, ambient-mode quotas,
and lazy summarization are still future work (Y3-Y7).

Templated on hardware.py (smallest existing K-track router) per the
2026-04-30 load-bearing survey recommendation #5. write_lock + audit
emission discipline preserved; no new state lives outside the registry.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.daemon.deps import (
    get_audit_chain,
    get_registry,
    get_tool_dispatcher,
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
    TurnDispatchResponse,
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
    response_model=TurnDispatchResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_writes_enabled)],
)
async def append_turn(
    conversation_id: str,
    body:            TurnAppendRequest,
    request:         Request,
    registry:        Registry      = Depends(get_registry),
    audit:           AuditChain    = Depends(get_audit_chain),
    write_lock:      threading.Lock = Depends(get_write_lock),
    tool_dispatcher = Depends(get_tool_dispatcher),
) -> TurnDispatchResponse:
    """Append a turn. Y2 adds the auto_respond orchestration path.

    Emits ``conversation_turn`` for both the operator turn and (when
    auto_respond fires) the agent's response turn. Each turn carries
    ``body_hash`` for tamper-evidence; bodies live in the registry
    until the retention window expires (Y7).

    ``auto_respond=True`` requires exactly 1 agent participant in
    the conversation (Y2 scope). Multi-agent rooms with @mention
    resolution come in Y3.
    """
    addressed_to_str = (
        ",".join(body.addressed_to) if body.addressed_to else None
    )
    with write_lock:
        # ---- Append the operator (or whoever spoke) turn -----------------
        try:
            op_row = registry.conversations.append_turn(
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
                    "turn_id":         op_row.turn_id,
                    "speaker":         op_row.speaker,
                    "addressed_to":    op_row.addressed_to,
                    "body_hash":       op_row.body_hash,
                    "token_count":     op_row.token_count,
                    "model_used":      op_row.model_used,
                },
                agent_dna=None,
            )
        except Exception:
            pass

        if not body.auto_respond:
            return TurnDispatchResponse(operator_turn=_turn_out(op_row))

        # ---- Y2 orchestration: dispatch the agent's response -------------
        # Y2 requires exactly 1 agent participant. Multi-agent room
        # resolution is Y3 work (@mention + suggest_agent.v1 fallback).
        participants = registry.conversations.list_participants(conversation_id)
        if len(participants) == 0:
            # Operator turn already landed; we just don't dispatch a
            # response. Operator-only rooms are valid (used as a
            # journaling surface), so this isn't an error.
            return TurnDispatchResponse(operator_turn=_turn_out(op_row))
        if len(participants) > 1:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"auto_respond=True requires exactly 1 agent participant; "
                    f"conversation {conversation_id} has {len(participants)}. "
                    "Use Y3+ multi-agent endpoints once they ship."
                ),
            )

        agent_participant = participants[0]
        try:
            agent = registry.get_agent(agent_participant.instance_id)
        except UnknownAgentError:
            raise HTTPException(
                status_code=500,
                detail=(
                    f"participant {agent_participant.instance_id!r} not in "
                    "agents table — registry inconsistency"
                ),
            )

        # Build the prompt from recent turn history. Older turns whose
        # body has been purged by Y7 retention contribute their summary
        # instead. The history is ordered oldest-first (chronological).
        recent_turns = registry.conversations.list_turns(
            conversation_id, limit=body.history_limit, offset=0,
        )
        prompt = _build_conversation_prompt(
            agent_name=agent.agent_name,
            agent_role=agent.role,
            domain=registry.conversations.get_conversation(conversation_id).domain,
            turns=recent_turns,
        )

        # Dispatch llm_think.v1 against the agent. Session id is the
        # conversation_id so per-session counters scope to the room.
        # Provider is whatever's currently active (provider_constraint
        # check runs inside the pipeline's GenreFloorStep).
        provider = _resolve_active_provider(request)
        constitution_path = Path(agent.constitution_path)
        try:
            outcome = await tool_dispatcher.dispatch(
                instance_id=agent.instance_id,
                agent_dna=agent.dna,
                role=agent.role,
                genre=None,  # GenreFloorStep already resolves via engine
                session_id=f"convy2-{conversation_id}",
                constitution_path=constitution_path,
                tool_name="llm_think",
                tool_version="1",
                args={
                    "prompt":     prompt,
                    "task_kind":  "conversation",
                    "max_tokens": body.max_response_tokens,
                },
                provider=provider,
            )
        except Exception:
            # Dispatcher itself crashed — we already persisted the
            # operator turn, so return with agent_dispatch_failed=True.
            return TurnDispatchResponse(
                operator_turn=_turn_out(op_row),
                agent_dispatch_failed=True,
            )

        from forest_soul_forge.tools.dispatcher import (
            DispatchSucceeded as _DispatchSucceeded,
        )
        if not isinstance(outcome, _DispatchSucceeded):
            # Refused / pending / failed — operator turn lands but no
            # agent turn appended. Audit chain has the diagnostic.
            return TurnDispatchResponse(
                operator_turn=_turn_out(op_row),
                agent_dispatch_failed=True,
            )

        result_output = outcome.result.output or {}
        response_text = result_output.get("response", "") or ""
        if not response_text:
            return TurnDispatchResponse(
                operator_turn=_turn_out(op_row),
                agent_dispatch_failed=True,
            )

        # Append the agent's response as the next turn.
        agent_row = registry.conversations.append_turn(
            conversation_id=conversation_id,
            speaker=agent.instance_id,
            body=response_text,
            addressed_to=None,  # response is to whoever spoke before
            token_count=outcome.result.tokens_used,
            model_used=result_output.get("model"),
        )
        try:
            audit.append(
                "conversation_turn",
                {
                    "conversation_id":    conversation_id,
                    "turn_id":            agent_row.turn_id,
                    "speaker":            agent_row.speaker,
                    "addressed_to":       agent_row.addressed_to,
                    "body_hash":          agent_row.body_hash,
                    "token_count":        agent_row.token_count,
                    "model_used":         agent_row.model_used,
                    "in_response_to":     op_row.turn_id,
                    "dispatched_via":     "llm_think.v1",
                    "dispatch_audit_seq": outcome.audit_seq,
                },
                agent_dna=agent.dna,
            )
        except Exception:
            pass

    return TurnDispatchResponse(
        operator_turn=_turn_out(op_row),
        agent_turn=_turn_out(agent_row),
    )


def _build_conversation_prompt(
    *,
    agent_name: str,
    agent_role: str,
    domain:     str,
    turns:      list,
) -> str:
    """Build the llm_think prompt for a conversation reply.

    Layout: a brief frame ("you are <name> in domain <X>"), the
    conversation history rendered as 'speaker: body' lines (oldest
    first), and a closing instruction. Purged turns surface as
    'speaker: [summarized] <summary>' so the agent has continuity
    even past the retention window.

    Kept here in the router (rather than as a tool) because the
    framing is conversation-specific and doesn't make sense outside
    Y2's single-agent context. Y3 will need a richer builder that
    accounts for @mentions + multi-agent rooms; that's its own
    helper at that point.
    """
    lines: list[str] = []
    lines.append(
        f"You are {agent_name}, an agent in role '{agent_role}' "
        f"participating in a conversation in domain '{domain}'."
    )
    lines.append("")
    lines.append("Recent conversation (oldest → newest):")
    for t in turns:
        body_or_summary = t.body if t.body is not None else (
            f"[summarized] {t.summary or '(content purged, no summary available)'}"
        )
        lines.append(f"  {t.speaker}: {body_or_summary}")
    lines.append("")
    lines.append(
        "Respond as " + agent_name + ", in character with your role. "
        "Keep the response focused and grounded; do not pretend to be "
        "another participant. Speak only as yourself."
    )
    return "\n".join(lines)


def _resolve_active_provider(request: Request):
    """Mirror of skills_run._resolve_active_provider so conversation
    dispatches use the same provider plumbing. Best-effort; tools that
    don't need a provider tolerate None."""
    pr = getattr(request.app.state, "providers", None)
    if pr is None:
        return None
    try:
        return pr.active()
    except Exception:
        return None


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
