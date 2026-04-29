"""ToolCountersTable — accessor for ``tool_call_counters`` + ``tool_calls``.

Two related tables, one accessor:

  - ``tool_call_counters``  (ADR-0019 T2): per-session call budget for
                            max_calls_per_session enforcement. The
                            dispatcher reads, decides, then increments —
                            all under the daemon's write lock so the
                            read-then-write window is atomic against
                            concurrent invocations of the same
                            (instance_id, session_id).
  - ``tool_calls``          (ADR-0019 T4): per-call denormalized view
                            over the audit chain. Dispatcher writes
                            one row per terminating event
                            (succeeded/failed) under the daemon write
                            lock, alongside the chain entry. Reads are
                            aggregations for the character-sheet stats
                            endpoint.

R4: extracted from registry.py.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from forest_soul_forge.registry.tables._helpers import transaction


class ToolCountersTable:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ---- per-session call counters ----
    def get_tool_call_count(self, instance_id: str, session_id: str) -> int:
        """Return the current per-session call count, or 0 if no row yet."""
        row = self._conn.execute(
            "SELECT calls FROM tool_call_counters WHERE instance_id=? AND session_id=?;",
            (instance_id, session_id),
        ).fetchone()
        return int(row["calls"]) if row is not None else 0

    def increment_tool_call_count(
        self, instance_id: str, session_id: str, when_iso: str
    ) -> int:
        """Increment the counter and return the post-increment value.

        Uses INSERT ... ON CONFLICT to fold the create-or-update into a
        single statement — no read-then-write window. Caller still
        holds the daemon write lock for the broader dispatch
        transaction so the new value can be reasoned about consistently
        with audit emission. Returns the post-increment count.
        """
        with transaction(self._conn):
            self._conn.execute(
                """
                INSERT INTO tool_call_counters (instance_id, session_id, calls, last_call_at)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(instance_id, session_id) DO UPDATE SET
                    calls = calls + 1,
                    last_call_at = excluded.last_call_at;
                """,
                (instance_id, session_id, when_iso),
            )
            row = self._conn.execute(
                "SELECT calls FROM tool_call_counters WHERE instance_id=? AND session_id=?;",
                (instance_id, session_id),
            ).fetchone()
        return int(row["calls"])

    # ---- per-call accounting + aggregates ----
    def record_tool_call(
        self,
        *,
        audit_seq: int,
        instance_id: str,
        session_id: str,
        tool_key: str,
        status: str,
        tokens_used: int | None,
        cost_usd: float | None,
        side_effect_summary: str | None,
        finished_at: str,
    ) -> None:
        """Insert one tool_calls row.

        ``audit_seq`` is the primary key — it points at the
        succeeded/failed audit-chain entry. INSERT OR IGNORE so a
        dispatcher retry that lands the same chain entry twice (which
        shouldn't happen under the write lock, but defensive) is a
        no-op rather than an integrity error.
        """
        self._conn.execute(
            """
            INSERT OR IGNORE INTO tool_calls (
                audit_seq, instance_id, session_id, tool_key, status,
                tokens_used, cost_usd, side_effect_summary, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                audit_seq, instance_id, session_id, tool_key, status,
                tokens_used, cost_usd, side_effect_summary, finished_at,
            ),
        )

    def aggregate_tool_calls(self, instance_id: str) -> dict[str, Any]:
        """Roll up tool_calls for one agent into character-sheet stats.

        Returns a dict with: total_invocations, failed_invocations,
        total_tokens_used (None when no calls used tokens),
        total_cost_usd (None when no calls had cost),
        last_active_at (None when no calls), per_tool list of
        (tool_key, count, tokens, cost).

        None totals (vs. zero) distinguish "no LLM-wrapping tool ever
        ran" from "LLM-wrapping tools ran but reported zero" — the UI
        can render the difference.
        """
        rows = self._conn.execute(
            """
            SELECT status, tokens_used, cost_usd, finished_at, tool_key
            FROM tool_calls WHERE instance_id=?;
            """,
            (instance_id,),
        ).fetchall()

        total = len(rows)
        failed = sum(1 for r in rows if r["status"] == "failed")
        total_tokens: int | None = None
        total_cost: float | None = None
        last_active: str | None = None
        per_tool: dict[str, dict[str, Any]] = {}
        for r in rows:
            if r["tokens_used"] is not None:
                total_tokens = (total_tokens or 0) + int(r["tokens_used"])
            if r["cost_usd"] is not None:
                total_cost = (total_cost or 0.0) + float(r["cost_usd"])
            if r["finished_at"] and (last_active is None or r["finished_at"] > last_active):
                last_active = r["finished_at"]
            slot = per_tool.setdefault(
                r["tool_key"],
                {"count": 0, "tokens": None, "cost": None},
            )
            slot["count"] += 1
            if r["tokens_used"] is not None:
                slot["tokens"] = (slot["tokens"] or 0) + int(r["tokens_used"])
            if r["cost_usd"] is not None:
                slot["cost"] = (slot["cost"] or 0.0) + float(r["cost_usd"])
        per_tool_list = [
            {"tool_key": k, **v} for k, v in sorted(per_tool.items())
        ]
        return {
            "total_invocations": total,
            "failed_invocations": failed,
            "total_tokens_used": total_tokens,
            "total_cost_usd": total_cost,
            "last_active_at": last_active,
            "per_tool": per_tool_list,
        }
