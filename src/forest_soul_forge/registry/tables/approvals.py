"""ApprovalsTable — accessor for ``tool_call_pending_approvals``.

ADR-0019 T3: persists tool calls that hit ``requires_human_approval=True``
and are waiting for an operator decision. The dispatcher writes one row
per gated call; the endpoints (list/detail/approve/reject) read and
mutate them. All under the daemon's write lock.

R4: extracted from registry.py.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from forest_soul_forge.registry._errors import DuplicateInstanceError

__all__ = ["DuplicateInstanceError", "ApprovalsTable"]


class ApprovalsTable:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def record_pending_approval(
        self,
        *,
        ticket_id: str,
        instance_id: str,
        session_id: str,
        tool_key: str,
        args_json: str,
        side_effects: str,
        pending_audit_seq: int,
        created_at: str,
    ) -> None:
        """Insert one pending-approval row.

        Raises :class:`DuplicateInstanceError` (we reuse the type for
        the queue too) on ticket_id collision — shouldn't happen
        because ticket_ids are derived from the audit-chain seq, but
        defensive.
        """
        try:
            self._conn.execute(
                """
                INSERT INTO tool_call_pending_approvals (
                    ticket_id, instance_id, session_id, tool_key,
                    args_json, side_effects, status,
                    pending_audit_seq, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?);
                """,
                (
                    ticket_id, instance_id, session_id, tool_key,
                    args_json, side_effects,
                    pending_audit_seq, created_at,
                ),
            )
        except sqlite3.IntegrityError as e:
            raise DuplicateInstanceError(
                f"pending-approval ticket {ticket_id!r}: {e}"
            ) from e

    def get_pending_approval(self, ticket_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM tool_call_pending_approvals WHERE ticket_id=?;",
            (ticket_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def list_pending_approvals(
        self,
        instance_id: str,
        *,
        status: str | None = "pending",
    ) -> list[dict[str, Any]]:
        """Return queued approvals for an agent.

        ``status='pending'`` (default) lists only undecided tickets —
        the operator's typical "what needs my attention" view.
        Pass ``status=None`` for the full history.
        """
        if status is None:
            rows = self._conn.execute(
                "SELECT * FROM tool_call_pending_approvals "
                "WHERE instance_id=? ORDER BY created_at ASC;",
                (instance_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM tool_call_pending_approvals "
                "WHERE instance_id=? AND status=? ORDER BY created_at ASC;",
                (instance_id, status),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_approval_decided(
        self,
        ticket_id: str,
        *,
        status: str,
        decided_audit_seq: int,
        decided_by: str,
        decision_reason: str | None,
        decided_at: str,
    ) -> bool:
        """Move a ticket out of pending. Returns True if exactly one row
        was updated; False otherwise (already decided / unknown ticket).

        Caller checks status before calling — the WHERE clause guards
        against accidentally re-deciding an already-decided ticket.
        """
        cur = self._conn.execute(
            """
            UPDATE tool_call_pending_approvals
            SET status=?, decided_audit_seq=?, decided_by=?,
                decision_reason=?, decided_at=?
            WHERE ticket_id=? AND status='pending';
            """,
            (status, decided_audit_seq, decided_by,
             decision_reason, decided_at, ticket_id),
        )
        return cur.rowcount == 1
