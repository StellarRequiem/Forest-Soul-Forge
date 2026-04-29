"""Audit chain read schemas + operator-emitted ceremony events.

Split out of the original 1139-line monolithic ``schemas.py`` by R1
of the post-marathon roadmap. Re-exported via ``schemas/__init__.py``
so existing ``from forest_soul_forge.daemon.schemas import X`` imports
keep working — this is purely an organizational refactor.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from forest_soul_forge.daemon.providers import ProviderStatus, TaskKind


class AuditEventOut(BaseModel):
    seq: int
    timestamp: str
    agent_dna: str | None
    instance_id: str | None
    event_type: str
    event_json: str
    entry_hash: str

class AuditListOut(BaseModel):
    count: int
    events: list[AuditEventOut]

# ADR-003X K2 — operator-emitted ceremony events. Distinct from
# tool-emitted events because the EMITTER is a human, not an agent.
# Used to mark milestones, identity events, governance decisions
# that don't fit any tool call (Iron Gate ceremony, agent retirement,
# operator-acknowledged transition, etc.).
class CeremonyEmitRequest(BaseModel):
    ceremony_name: str       # operator-chosen label; e.g. "iron_gate", "first_birth"
    summary: str             # one-line human-readable description
    operator_id: str         # who is emitting (handle, key fingerprint)
    metadata: dict | None = None   # optional structured payload

class CeremonyEmitResponse(BaseModel):
    seq: int
    timestamp: str
    entry_hash: str
    event_type: str
    ceremony_name: str
