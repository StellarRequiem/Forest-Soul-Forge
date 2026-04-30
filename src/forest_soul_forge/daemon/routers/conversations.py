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
    AMBIENT_QUOTA_BY_RATE,
    AmbientNudgeRequest,
    AmbientNudgeResponse,
    ConversationBridgeRequest,
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
from forest_soul_forge.daemon.routers.conversation_resolver import (
    resolve_chain_continuation,
    resolve_initial_addressees,
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


@router.post(
    "/{conversation_id}/bridge",
    response_model=ParticipantOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_writes_enabled)],
)
def bridge_participant(
    conversation_id: str,
    body:            ConversationBridgeRequest,
    registry:        Registry      = Depends(get_registry),
    audit:           AuditChain    = Depends(get_audit_chain),
    write_lock:      threading.Lock = Depends(get_write_lock),
) -> ParticipantOut:
    """Y4: cross-domain bridge invitation.

    Distinct from ``POST /participants`` because bringing an agent
    IN from another domain is the main exfiltration vector per
    ADR-003Y §threat-model. The endpoint requires operator_id +
    reason and emits a richer ``conversation_bridged`` audit event
    so the action is attributable to a specific operator decision.

    Idempotent on (conversation_id, instance_id) per the underlying
    add_participant — re-bridging the same agent returns the existing
    participant row (with the original ``bridged_from`` preserved).
    """
    with write_lock:
        try:
            conv = registry.conversations.get_conversation(conversation_id)
        except ConversationNotFoundError:
            raise HTTPException(status_code=404, detail=f"conversation {conversation_id!r} not found")
        try:
            agent = registry.get_agent(body.instance_id)
        except UnknownAgentError:
            raise HTTPException(status_code=404, detail=f"agent {body.instance_id!r} not found")

        # Sanity-check: refusing to bridge an agent FROM the same domain
        # they're already in. That's a same-domain join (use
        # /participants instead). Y4 wants the cross-domain invariant
        # visible at submission time.
        if body.from_domain == conv.domain:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"from_domain={body.from_domain!r} is the same as the "
                    f"conversation's domain — use POST /participants for "
                    "in-domain joins. /bridge is for cross-domain only."
                ),
            )

        row = registry.conversations.add_participant(
            conversation_id, body.instance_id, bridged_from=body.from_domain,
        )
        try:
            audit.append(
                "conversation_bridged",
                {
                    "conversation_id": conversation_id,
                    "instance_id":     body.instance_id,
                    "agent_name":      agent.agent_name,
                    "from_domain":     body.from_domain,
                    "to_domain":       conv.domain,
                    "operator_id":     body.operator_id,
                    "reason":          body.reason,
                },
                agent_dna=agent.dna,
            )
        except Exception:
            pass
    return _participant_out(row)


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

        # ---- Y3 multi-agent orchestration --------------------------------
        # Resolution: addressed_to → @mentions in body → fallback to
        # first agent. Then walk the chain: each agent's response can
        # @mention the next responder, capped at max_chain_depth.
        participants = registry.conversations.list_participants(conversation_id)
        if not participants:
            # Operator-only room. Valid (journaling surface).
            return TurnDispatchResponse(operator_turn=_turn_out(op_row))

        def _safe_get_agent(iid: str):
            try:
                return registry.get_agent(iid)
            except UnknownAgentError:
                return None

        addressees = resolve_initial_addressees(
            addressed_to=body.addressed_to,
            body=body.body,
            participants=participants,
            agent_lookup_fn=_safe_get_agent,
        )

        domain = registry.conversations.get_conversation(conversation_id).domain
        provider = _resolve_active_provider(request)

        from forest_soul_forge.tools.dispatcher import (
            DispatchSucceeded as _DispatchSucceeded,
        )

        agent_turns: list = []
        any_failed = False
        prior_speaker_turn = op_row
        chain_depth = 0

        # Process addressees one at a time. The first addressee responds;
        # if its response @mentions another participant, that becomes the
        # next addressee. Self-mentions are filtered by the resolver.
        while addressees and chain_depth < body.max_chain_depth:
            next_id = addressees[0]
            agent = _safe_get_agent(next_id)
            if agent is None:
                # Participant references unknown agent — skip but log.
                addressees = addressees[1:]
                continue

            # Refresh history each iteration so each agent sees the
            # latest chain state including prior agents' turns.
            recent_turns = registry.conversations.list_turns(
                conversation_id, limit=body.history_limit, offset=0,
            )
            prompt = _build_conversation_prompt(
                agent_name=agent.agent_name,
                agent_role=agent.role,
                domain=domain,
                turns=recent_turns,
            )

            constitution_path = Path(agent.constitution_path)
            try:
                outcome = await tool_dispatcher.dispatch(
                    instance_id=agent.instance_id,
                    agent_dna=agent.dna,
                    role=agent.role,
                    genre=None,
                    session_id=f"conv-{conversation_id}",
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
                any_failed = True
                break

            if not isinstance(outcome, _DispatchSucceeded):
                any_failed = True
                break

            result_output = outcome.result.output or {}
            response_text = result_output.get("response", "") or ""
            if not response_text:
                any_failed = True
                break

            agent_row = registry.conversations.append_turn(
                conversation_id=conversation_id,
                speaker=agent.instance_id,
                body=response_text,
                addressed_to=None,
                token_count=outcome.result.tokens_used,
                model_used=result_output.get("model"),
            )
            agent_turns.append(agent_row)
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
                        "in_response_to":     prior_speaker_turn.turn_id,
                        "dispatched_via":     "llm_think.v1",
                        "dispatch_audit_seq": outcome.audit_seq,
                        "chain_depth":        chain_depth + 1,
                    },
                    agent_dna=agent.dna,
                )
            except Exception:
                pass

            chain_depth += 1
            prior_speaker_turn = agent_row

            # Decide who's next. Priority:
            #  - additional explicit addressees from caller's list (Y3
            #    addressed_to was a multi-element list)
            #  - @mentions in the agent's response (Y3 chain pass)
            # Empty list → chain ends naturally.
            remaining_explicit = addressees[1:]
            mention_continuation = resolve_chain_continuation(
                last_responder_id=next_id,
                last_response_body=response_text,
                participants=participants,
                agent_lookup_fn=_safe_get_agent,
            )
            # Explicit addressing wins; mentions are the natural
            # extension when explicit list runs out.
            if remaining_explicit:
                addressees = remaining_explicit
            else:
                addressees = mention_continuation

    return TurnDispatchResponse(
        operator_turn=_turn_out(op_row),
        agent_turn=(_turn_out(agent_turns[0]) if agent_turns else None),
        agent_turn_chain=[_turn_out(t) for t in agent_turns],
        chain_depth=len(agent_turns),
        agent_dispatch_failed=any_failed,
    )


# ---------------------------------------------------------------------------
# Y5 ambient mode — opt-in + rate-gated proactive turn
# ---------------------------------------------------------------------------
def _read_ambient_opt_in(constitution_path: Path) -> bool:
    """Read ``interaction_modes.ambient_opt_in`` from the agent's
    constitution.yaml. Default False — Y5 is structurally opt-in
    even when the genre would permit it.
    """
    import yaml
    if not constitution_path.exists():
        return False
    try:
        data = yaml.safe_load(constitution_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return False
    block = data.get("interaction_modes") or {}
    if isinstance(block, dict):
        return bool(block.get("ambient_opt_in", False))
    return False


def _ambient_quota_used(
    *, audit_chain, instance_id: str, conversation_id: str,
) -> int:
    """Count ``ambient_nudge`` events in the last 24h for the
    (agent, conversation) tuple. Walks the in-memory tail; cheap at
    realistic scales because ambient quotas are 1-10 per day.
    """
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    used = 0
    # tail(N) gives newest-first; we want last 24h so a generous
    # tail covers the worst case (a maxed-out heavy rate is 10/day
    # so ~10 entries at most per tuple).
    for entry in audit_chain.tail(500) or []:
        if entry.event_type != "ambient_nudge":
            continue
        if entry.timestamp < cutoff_iso:
            break  # rest are older than 24h
        d = entry.event_data or {}
        if d.get("instance_id") == instance_id and d.get("conversation_id") == conversation_id:
            used += 1
    return used


@router.post(
    "/{conversation_id}/ambient/nudge",
    response_model=AmbientNudgeResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_writes_enabled)],
)
async def ambient_nudge(
    conversation_id: str,
    body:            AmbientNudgeRequest,
    request:         Request,
    registry:        Registry      = Depends(get_registry),
    audit:           AuditChain    = Depends(get_audit_chain),
    write_lock:      threading.Lock = Depends(get_write_lock),
    tool_dispatcher = Depends(get_tool_dispatcher),
) -> AmbientNudgeResponse:
    """Y5: dispatch a proactive agent turn into a conversation.

    Two structural gates before anything dispatches:
      - agent's constitution must opt in via interaction_modes.ambient_opt_in
      - operator's ambient_rate quota for (instance_id, conversation_id) in
        the last 24h must not be exhausted

    On success, emits ``ambient_nudge`` audit event BEFORE the
    agent's turn lands so an operator inspecting the chain sees the
    nudge first, then the resulting turn — same ordering the
    dispatcher uses for ``tool_call_dispatched`` → ``tool_call_succeeded``.
    """
    rate = (getattr(request.app.state, "ambient_rate", None) or "minimal").lower()
    if rate not in AMBIENT_QUOTA_BY_RATE:
        rate = "minimal"
    quota_max = AMBIENT_QUOTA_BY_RATE[rate]

    # 1. Validate conversation + agent exist + agent is a participant.
    try:
        conv = registry.conversations.get_conversation(conversation_id)
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail=f"conversation {conversation_id!r} not found")
    if conv.status == "archived":
        raise HTTPException(status_code=409, detail="conversation is archived; ambient nudges refused")
    try:
        agent = registry.get_agent(body.instance_id)
    except UnknownAgentError:
        raise HTTPException(status_code=404, detail=f"agent {body.instance_id!r} not found")

    participants = registry.conversations.list_participants(conversation_id)
    if not any(p.instance_id == body.instance_id for p in participants):
        raise HTTPException(
            status_code=400,
            detail=(
                f"agent {body.instance_id!r} is not a participant in this room — "
                "add via /participants or /bridge first"
            ),
        )

    # 2. Constitution opt-in.
    constitution_path = Path(agent.constitution_path)
    if not _read_ambient_opt_in(constitution_path):
        raise HTTPException(
            status_code=403,
            detail=(
                f"agent {agent.agent_name!r} has not opted into ambient mode. "
                "Set interaction_modes.ambient_opt_in=true in the constitution."
            ),
        )

    # 3. Quota check.
    used_before = _ambient_quota_used(
        audit_chain=audit,
        instance_id=body.instance_id,
        conversation_id=conversation_id,
    )
    if used_before >= quota_max:
        raise HTTPException(
            status_code=429,
            detail=(
                f"ambient quota exhausted: rate={rate} (max {quota_max}/day) and "
                f"this agent has {used_before} ambient turns in this room in the "
                "last 24h. Raise FSF_AMBIENT_RATE or wait until quota window rolls."
            ),
        )

    # 4. Build prompt — recent history + ambient framing.
    recent_turns = registry.conversations.list_turns(
        conversation_id, limit=body.history_limit, offset=0,
    )
    prompt = _build_ambient_prompt(
        agent_name=agent.agent_name,
        agent_role=agent.role,
        domain=conv.domain,
        nudge_kind=body.nudge_kind,
        turns=recent_turns,
    )

    # 5. Dispatch llm_think + append turn (single-writer lock).
    provider = _resolve_active_provider(request)
    from forest_soul_forge.tools.dispatcher import (
        DispatchSucceeded as _DispatchSucceeded,
    )

    with write_lock:
        # 5a. Emit ambient_nudge BEFORE dispatch so the chain shows
        # nudge → tool_call_dispatched → tool_call_succeeded → turn.
        try:
            audit.append(
                "ambient_nudge",
                {
                    "conversation_id": conversation_id,
                    "instance_id":     body.instance_id,
                    "agent_name":      agent.agent_name,
                    "operator_id":     body.operator_id,
                    "nudge_kind":      body.nudge_kind,
                    "rate":            rate,
                    "quota_used_before": used_before,
                    "quota_max":       quota_max,
                },
                agent_dna=agent.dna,
            )
        except Exception:
            pass

        try:
            outcome = await tool_dispatcher.dispatch(
                instance_id=agent.instance_id,
                agent_dna=agent.dna,
                role=agent.role,
                genre=None,
                session_id=f"ambient-{conversation_id}",
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
        except Exception as e:
            raise HTTPException(
                status_code=502,
                detail=f"llm_think dispatch failed: {type(e).__name__}: {e}",
            )

        if not isinstance(outcome, _DispatchSucceeded):
            raise HTTPException(
                status_code=502,
                detail=f"llm_think returned {type(outcome).__name__} — see audit chain",
            )

        result_output = outcome.result.output or {}
        response_text = (result_output.get("response") or "").strip()
        if not response_text:
            raise HTTPException(status_code=502, detail="empty ambient response")

        agent_row = registry.conversations.append_turn(
            conversation_id=conversation_id,
            speaker=agent.instance_id,
            body=response_text,
            addressed_to=None,
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
                    "ambient":            True,
                    "nudge_kind":         body.nudge_kind,
                    "dispatch_audit_seq": outcome.audit_seq,
                },
                agent_dna=agent.dna,
            )
        except Exception:
            pass

    return AmbientNudgeResponse(
        agent_turn=_turn_out(agent_row),
        quota_used=used_before + 1,
        quota_max=quota_max,
        rate=rate,
    )


def _build_ambient_prompt(
    *,
    agent_name: str,
    agent_role: str,
    domain:     str,
    nudge_kind: str,
    turns:      list,
) -> str:
    """Build a prompt that asks the agent to surface SOMETHING NEW
    rather than re-answer the latest operator turn. The framing
    distinguishes ambient mode from reactive Y2/Y3 dispatches."""
    lines: list[str] = []
    lines.append(
        f"You are {agent_name}, an agent in role '{agent_role}' "
        f"participating in a conversation in domain '{domain}'. "
        f"This is an AMBIENT nudge of kind '{nudge_kind}' — the operator "
        "has invited you to proactively surface something useful, NOT "
        "to answer a recent message."
    )
    lines.append("")
    lines.append("Recent conversation (oldest → newest):")
    for t in turns:
        body_or_summary = t.body if t.body is not None else (
            f"[summarized] {t.summary or '(content purged)'}"
        )
        lines.append(f"  {t.speaker}: {body_or_summary}")
    lines.append("")
    lines.append(
        f"Surface ONE concise new contribution as {agent_name}: a follow-up "
        "question, a check-in, an observation, an open thread the room hasn't "
        "addressed yet. Keep it 1-3 sentences. Don't summarize what's already "
        "been said. Don't pretend to be another participant."
    )
    return "\n".join(lines)


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
