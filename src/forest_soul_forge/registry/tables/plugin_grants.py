"""PluginGrantsTable — accessor for ``agent_plugin_grants``
(ADR-0043 follow-up #2, Burst 113).

Post-birth grants of MCP plugin access without rebirthing the agent.
The constitution_hash is immutable per agent (CLAUDE.md architectural
invariant) so we add an explicit augmentation layer rather than
mutate the constitution.

Effective allowed_mcp_servers at dispatch time =
  constitution.allowed_mcp_servers ∪ {grants where revoked_at_seq IS NULL}

The dispatcher merges this set into ``ctx.constraints["allowed_mcp_servers"]``
before mcp_call.v1 runs. mcp_call.v1's existing allowlist check then
sees the union without needing changes.

Audit emission is the caller's responsibility — this table is a pure
SQL surface. The two audit event types (``agent_plugin_granted`` and
``agent_plugin_revoked``) emit from the writes/ endpoint that wraps
these calls.

trust_tier is forward-compatible storage for ADR-0045 (Agent Posture
/ Trust-Light System, queued next). Burst 113 records the value;
ADR-0045's PostureGateStep will start consulting it once filed.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from forest_soul_forge.registry.tables._helpers import (
    transaction,
    utc_now_iso,
)


# ---- public dataclass --------------------------------------------------

@dataclass(frozen=True)
class PluginGrant:
    """One row in agent_plugin_grants — active or historical."""
    instance_id: str
    plugin_name: str
    trust_tier: str
    granted_at_seq: int
    granted_by: str | None
    granted_at: str
    revoked_at_seq: int | None
    revoked_at: str | None
    revoked_by: str | None
    reason: str | None

    @property
    def is_active(self) -> bool:
        return self.revoked_at_seq is None


# ---- table accessor ----------------------------------------------------

class PluginGrantsTable:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # -- writes ---------------------------------------------------------

    def grant(
        self,
        *,
        instance_id: str,
        plugin_name: str,
        trust_tier: str,
        granted_at_seq: int,
        granted_by: str | None = None,
        reason: str | None = None,
        when: str | None = None,
    ) -> None:
        """Insert or replace a grant for (instance_id, plugin_name).

        Idempotent: re-granting an already-active grant is a no-op
        side-effect-wise (the row stays). Re-granting after a revoke
        OVERWRITES the prior row — the revoked record is replaced
        with a fresh active grant. Caller decides whether to emit an
        audit event for the duplicate-grant case (typically yes,
        for traceability).
        """
        if trust_tier not in ("green", "yellow", "red"):
            raise ValueError(
                f"trust_tier must be one of green/yellow/red, got {trust_tier!r}"
            )
        with transaction(self._conn):
            self._conn.execute(
                """
                INSERT OR REPLACE INTO agent_plugin_grants (
                    instance_id, plugin_name, trust_tier,
                    granted_at_seq, granted_by, granted_at,
                    revoked_at_seq, revoked_at, revoked_by, reason
                )
                VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?);
                """,
                (
                    instance_id, plugin_name, trust_tier,
                    granted_at_seq, granted_by, when or utc_now_iso(),
                    reason,
                ),
            )

    def revoke(
        self,
        *,
        instance_id: str,
        plugin_name: str,
        revoked_at_seq: int,
        revoked_by: str | None = None,
        reason: str | None = None,
        when: str | None = None,
    ) -> bool:
        """Mark an active grant as revoked. Returns True if a row was
        affected, False if no active grant existed (caller decides
        whether to surface as 404 or treat as idempotent).

        Sets revoked_at_seq, revoked_at, revoked_by — the grant row
        STAYS for historical audit. A subsequent grant() overwrites
        via INSERT OR REPLACE.
        """
        with transaction(self._conn):
            cur = self._conn.execute(
                """
                UPDATE agent_plugin_grants
                SET revoked_at_seq = ?,
                    revoked_at     = ?,
                    revoked_by     = ?,
                    reason         = COALESCE(?, reason)
                WHERE instance_id = ?
                  AND plugin_name = ?
                  AND revoked_at_seq IS NULL;
                """,
                (
                    revoked_at_seq,
                    when or utc_now_iso(),
                    revoked_by,
                    reason,
                    instance_id,
                    plugin_name,
                ),
            )
            return cur.rowcount > 0

    # -- reads ----------------------------------------------------------

    def list_active(self, instance_id: str) -> list[PluginGrant]:
        """Active grants only (revoked_at_seq IS NULL).

        The dispatcher calls this on every mcp_call.v1 dispatch to
        compute effective allowed_mcp_servers — must be cheap. The
        idx_plugin_grants_active partial index covers it.
        """
        rows = self._conn.execute(
            """
            SELECT instance_id, plugin_name, trust_tier,
                   granted_at_seq, granted_by, granted_at,
                   revoked_at_seq, revoked_at, revoked_by, reason
            FROM agent_plugin_grants
            WHERE instance_id = ?
              AND revoked_at_seq IS NULL
            ORDER BY plugin_name;
            """,
            (instance_id,),
        ).fetchall()
        return [_row_to_grant(r) for r in rows]

    def list_all(self, instance_id: str) -> list[PluginGrant]:
        """Every row including revoked, for audit/UI history views."""
        rows = self._conn.execute(
            """
            SELECT instance_id, plugin_name, trust_tier,
                   granted_at_seq, granted_by, granted_at,
                   revoked_at_seq, revoked_at, revoked_by, reason
            FROM agent_plugin_grants
            WHERE instance_id = ?
            ORDER BY granted_at_seq DESC;
            """,
            (instance_id,),
        ).fetchall()
        return [_row_to_grant(r) for r in rows]

    def get_active(
        self, instance_id: str, plugin_name: str,
    ) -> PluginGrant | None:
        row = self._conn.execute(
            """
            SELECT instance_id, plugin_name, trust_tier,
                   granted_at_seq, granted_by, granted_at,
                   revoked_at_seq, revoked_at, revoked_by, reason
            FROM agent_plugin_grants
            WHERE instance_id = ?
              AND plugin_name = ?
              AND revoked_at_seq IS NULL;
            """,
            (instance_id, plugin_name),
        ).fetchone()
        return _row_to_grant(row) if row else None

    def active_plugin_names(self, instance_id: str) -> set[str]:
        """The set of plugin names with active grants — the cheap
        union input the dispatcher needs at dispatch time. Returns
        a fresh set on every call so the dispatcher can mutate it."""
        rows = self._conn.execute(
            """
            SELECT plugin_name
            FROM agent_plugin_grants
            WHERE instance_id = ?
              AND revoked_at_seq IS NULL;
            """,
            (instance_id,),
        ).fetchall()
        return {r[0] for r in rows}


# ---- helpers -----------------------------------------------------------

def _row_to_grant(row) -> PluginGrant:
    return PluginGrant(
        instance_id=row[0],
        plugin_name=row[1],
        trust_tier=row[2],
        granted_at_seq=row[3],
        granted_by=row[4],
        granted_at=row[5],
        revoked_at_seq=row[6],
        revoked_at=row[7],
        revoked_by=row[8],
        reason=row[9],
    )
