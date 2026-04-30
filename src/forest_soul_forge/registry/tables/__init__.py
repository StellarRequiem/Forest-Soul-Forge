"""Per-table accessor classes for Registry (R4 split).

Each accessor takes a sqlite3.Connection in __init__ and provides typed
methods for one logical concern (agents/ancestry/audit, idempotency
cache, tool counters, approvals queue, secrets).

Composed by Registry — see ``registry/registry.py``. New code should
prefer ``registry.agents.X`` over the back-compat ``registry.X``
delegate, but both work.
"""
from __future__ import annotations

from forest_soul_forge.registry.tables.agents import (
    AgentRow,
    AgentsTable,
    AuditRow,
    RebuildReport,
)
from forest_soul_forge.registry.tables.approvals import ApprovalsTable
from forest_soul_forge.registry.tables.conversations import (
    ConversationNotFoundError,
    ConversationRow,
    ConversationsTable,
    ParticipantRow,
    TurnRow,
)
from forest_soul_forge.registry.tables.idempotency import IdempotencyTable
from forest_soul_forge.registry.tables.secrets import SecretsTable
from forest_soul_forge.registry.tables.tool_counters import ToolCountersTable

__all__ = [
    "AgentsTable",
    "ApprovalsTable",
    "ConversationsTable",
    "IdempotencyTable",
    "SecretsTable",
    "ToolCountersTable",
    # Result dataclasses re-exported so callers don't have to know
    # the exact file boundary.
    "AgentRow",
    "AuditRow",
    "ConversationNotFoundError",
    "ConversationRow",
    "ParticipantRow",
    "RebuildReport",
    "TurnRow",
]
