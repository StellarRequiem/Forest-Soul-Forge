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

    Y1 only supports operator-spoken turns (the operator typed
    something into the room). Agent turns are appended by the
    orchestrator, which is Y2+ work.
    """

    speaker:      str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Who spoke. Operator id for Y1; instance_id for agents in Y2+.",
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
