"""ADR-003Y Y1 — Pydantic schemas for the conversations router.

Mirrors the registry table dataclasses but with Pydantic validation
for the HTTP boundary. Only the request models add validation
constraints (lengths, enum values); response models are passthroughs
that adapt the registry's typed dataclasses to JSON.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enum aliases — reused for retention_policy and status. Single source of
# truth; the registry table validates against the same set.
# ---------------------------------------------------------------------------
RetentionPolicy = Literal["full_7d", "full_30d", "full_indefinite"]
ConversationStatus = Literal["active", "idle", "archived"]


# ---------------------------------------------------------------------------
# Conversation CRUD
# ---------------------------------------------------------------------------
class ConversationCreateRequest(BaseModel):
    """Body for ``POST /conversations``."""

    domain:           str = Field(
        ...,
        min_length=1,
        max_length=64,
        description=(
            "Operator-defined free-text domain. Recommended seeds: "
            "therapy, coding, builders, admin. Used as the bridge "
            "boundary in Y4 cross-domain invitations."
        ),
    )
    operator_id:      str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Operator identifier (free-form; e.g. 'alex' or 'live-test').",
    )
    retention_policy: RetentionPolicy = Field(
        default="full_7d",
        description=(
            "How long raw turn bodies live before Y7 lazy summarization. "
            "full_indefinite is operator-deliberate and surfaces in the UI."
        ),
    )


class ConversationOut(BaseModel):
    """Response shape for ``POST /conversations`` and GET endpoints."""

    conversation_id:  str
    domain:           str
    operator_id:      str
    created_at:       str
    last_turn_at:     str | None = None
    status:           ConversationStatus
    retention_policy: RetentionPolicy


class ConversationListOut(BaseModel):
    """Response for ``GET /conversations``. Paginated; limit/offset reflect
    what was supplied (so the client can detect end-of-list without an
    extra count query)."""

    conversations: list[ConversationOut]
    limit:         int
    offset:        int


class ConversationStatusUpdateRequest(BaseModel):
    """Body for ``POST /conversations/{id}/status``."""

    status: ConversationStatus = Field(
        ...,
        description="Target status. Caller must reason about idempotency.",
    )
    reason: str | None = Field(
        default=None,
        max_length=512,
        description="Optional rationale; lands in audit event_data.",
    )


class RetentionPolicyUpdateRequest(BaseModel):
    """Body for ``POST /conversations/{id}/retention``."""

    policy: RetentionPolicy
    reason: str | None = Field(
        default=None,
        max_length=512,
        description=(
            "Operator note. Required for full_indefinite to encourage "
            "deliberate use, but the runtime accepts None."
        ),
    )


# ---------------------------------------------------------------------------
# Participants
# ---------------------------------------------------------------------------
class ParticipantAddRequest(BaseModel):
    """Body for ``POST /conversations/{id}/participants``."""

    instance_id:  str = Field(..., min_length=1)
    bridged_from: str | None = Field(
        default=None,
        description=(
            "Source domain when this is a Y4 cross-domain bridge. "
            "Same-domain joins leave this NULL. Recorded in the "
            "participant row and surfaced via /participants list."
        ),
    )


class ParticipantOut(BaseModel):
    conversation_id: str
    instance_id:     str
    joined_at:       str
    bridged_from:    str | None = None


class ParticipantListOut(BaseModel):
    participants: list[ParticipantOut]


# ---------------------------------------------------------------------------
# Turns
# ---------------------------------------------------------------------------
class TurnAppendRequest(BaseModel):
    """Body for ``POST /conversations/{id}/turns``.

    Y1 supported operator-spoken turns only. Y2 adds the optional
    ``auto_respond`` flag that triggers single-agent orchestration:
    after appending the operator's turn, the router dispatches
    ``llm_think.v1`` to the conversation's sole agent participant
    and appends the response as the next turn before returning.

    Multi-agent rooms (>1 agent participant) currently 400 when
    auto_respond=True; Y3 lifts that constraint via @mention +
    suggest_agent fallback resolution.
    """

    speaker:      str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Who spoke. Operator id for the operator; instance_id for agents.",
    )
    body:         str = Field(
        ...,
        min_length=1,
        description="Raw turn text. SHA-256 stored as body_hash.",
    )
    addressed_to: list[str] | None = Field(
        default=None,
        description=(
            "Optional list of instance_ids the turn is directed at. "
            "Empty/None means 'whole room'. Stored as comma-joined string."
        ),
    )
    token_count:  int | None = Field(
        default=None,
        ge=0,
        description="Optional token count from the speaker's tokenizer.",
    )
    model_used:   str | None = Field(
        default=None,
        max_length=128,
        description="Model tag when speaker is an agent; None for operator.",
    )
    auto_respond: bool = Field(
        default=False,
        description=(
            "Y2 single-agent orchestration. When True AND the conversation "
            "has exactly 1 agent participant, the router dispatches "
            "llm_think.v1 to that agent with prior conversation history as "
            "context, appends the agent's response as a follow-up turn, and "
            "returns both turns. False (default) preserves Y1 behavior — "
            "just append this turn and return."
        ),
    )
    history_limit: int = Field(
        default=20,
        ge=1, le=100,
        description=(
            "Y2: cap on prior turns included in the agent's prompt context. "
            "Default 20 keeps prompts under typical 32K-token model limits "
            "while preserving recent conversation flow."
        ),
    )
    max_response_tokens: int = Field(
        default=400,
        ge=1, le=8192,
        description="Y2: max_tokens passed to llm_think.v1 for the agent response.",
    )


class TurnOut(BaseModel):
    turn_id:         str
    conversation_id: str
    speaker:         str
    addressed_to:    str | None = None
    body:            str | None = None
    summary:         str | None = None
    body_hash:       str
    token_count:     int | None = None
    timestamp:       str
    model_used:      str | None = None


class TurnListOut(BaseModel):
    turns:  list[TurnOut]
    limit:  int
    offset: int


class TurnDispatchResponse(BaseModel):
    """Response shape for ``POST /conversations/{id}/turns``.

    Y1 just returned a single TurnOut. Y2 wraps it so the
    auto_respond path can return BOTH turns (operator + agent) in
    one response, while the legacy auto_respond=False path returns
    operator_turn only with agent_turn=None.

    A 400 is raised before any turn is written when auto_respond=True
    fails the "exactly 1 agent participant" precondition; the
    operator turn isn't persisted in that error case to avoid
    half-state.
    """

    operator_turn: TurnOut
    agent_turn:    TurnOut | None = Field(
        default=None,
        description=(
            "The agent's response when auto_respond=True succeeded. "
            "None when auto_respond=False was set, or when "
            "orchestration was skipped because the room had 0 agent "
            "participants (rare — caller should check)."
        ),
    )
    agent_dispatch_failed: bool = Field(
        default=False,
        description=(
            "True when auto_respond=True succeeded the precondition "
            "but the llm_think dispatch itself returned a non-success "
            "status (provider error, refused, etc.). Operator can "
            "inspect the audit chain by recent tool_call_failed events "
            "to diagnose. The operator turn IS still persisted in this "
            "case — the operator's input lands; only the agent's "
            "response did not."
        ),
    )
