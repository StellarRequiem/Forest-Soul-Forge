"""Memory consent — ADR-0022 v0.2 + ADR-0027 §2 (T16).

Split out of the original 1139-line monolithic ``schemas.py`` by R1
of the post-marathon roadmap. Re-exported via ``schemas/__init__.py``
so existing ``from forest_soul_forge.daemon.schemas import X`` imports
keep working — this is purely an organizational refactor.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from forest_soul_forge.daemon.providers import ProviderStatus, TaskKind


class MemoryConsentGrantRequest(BaseModel):
    """Request body for POST /agents/{instance_id}/memory/consents.

    ``instance_id`` (URL path) is the OWNER of the memory entry —
    the agent granting consent. ``recipient_instance`` (body) is
    who's being granted access. ``entry_id`` is the memory entry
    on the owner's store. Owner cannot be its own recipient.
    """

    entry_id: str = Field(..., description="Memory entry on the owner's store.")
    recipient_instance: str = Field(
        ..., description="Agent receiving consent. Must differ from owner.",
    )

class MemoryConsentGrantResponse(BaseModel):
    """Response shape for grant + revoke endpoints — same shape so a
    client can drive both with one type."""

    owner_instance: str
    entry_id: str
    recipient_instance: str
    revoked: bool = Field(
        default=False,
        description="False on grant, True on revoke. Lets the client "
        "distinguish the operation type without re-fetching.",
    )

class MemoryConsentOut(BaseModel):
    """One row from GET /agents/{instance_id}/memory/consents."""

    entry_id: str
    recipient_instance: str
    granted_at: str
    granted_by: str
    revoked_at: str | None = None

class MemoryConsentListResponse(BaseModel):
    """Response shape for the list endpoint."""

    owner_instance: str
    count: int
    consents: list[MemoryConsentOut]
