"""PluginGrantsTable — accessor for ``agent_plugin_grants``
(ADR-0043 follow-up #2, Burst 113; ADR-0053 T2 per-tool extension,
Burst 237).

Post-birth grants of MCP plugin access without rebirthing the agent.
The constitution_hash is immutable per agent (CLAUDE.md architectural
invariant) so we add an explicit augmentation layer rather than
mutate the constitution.

ADR-0053 (B235 T1 schema, B237 T2 surface) extends grants with
optional per-tool granularity. ``tool_name=None`` is a plugin-level
grant (the original ADR-0043 semantic — covers all tools the
plugin's manifest declares). ``tool_name`` non-None is a per-tool
grant covering only that one tool inside the plugin.

Effective allowed_mcp_servers at dispatch time =
  constitution.allowed_mcp_servers
    ∪ {plugin for any active grant row in agent_plugin_grants}
The per-tool NARROWING (specificity-wins) happens in ADR-0053 T4
inside the dispatcher's grants-step. T2 just exposes the column
on the surface; T4 wires the resolver.

The dispatcher merges this set into ``ctx.constraints["allowed_mcp_servers"]``
before mcp_call.v1 runs. mcp_call.v1's existing allowlist check then
sees the union without needing changes.

Audit emission is the caller's responsibility — this table is a pure
SQL surface. The two audit event types (``agent_plugin_granted`` and
``agent_plugin_revoked``) emit from the writes/ endpoint that wraps
these calls. ADR-0053 D4 adds an optional ``tool_name`` field to
the event_data on those events (additive; caller-set).

trust_tier is ADR-0045 (Agent Posture / Trust-Light System) storage.
The PostureGateStep and ADR-0060 GrantPolicy both consult it.

## SQLite NULL handling note

Per-tool / plugin-level distinction lives in a nullable column.
SQLite's standard ``=`` operator returns NULL (not TRUE) when either
operand is NULL, which breaks the ``WHERE tool_name = ?`` pattern
when ``?`` is None. We use SQLite's ``IS`` operator instead — per
the docs, ``IS`` behaves like ``=`` except both NULL operands
compare equal, which is exactly what we want for the "plugin-level
row" lookup.
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
    """One row in agent_plugin_grants — active or historical.

    ``tool_name`` is None for plugin-level grants (the ADR-0043
    original semantic, covering all tools the manifest declares)
    and non-None for per-tool grants (covering only the named tool).
    """
    instance_id: str
    plugin_name: str
    tool_name: str | None
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

    @property
    def is_plugin_level(self) -> bool:
        """True for ADR-0043-shape grants (covers all manifest tools)."""
        return self.tool_name is None

    @property
    def is_per_tool(self) -> bool:
        """True for ADR-0053-shape grants (covers only ``self.tool_name``)."""
        return self.tool_name is not None


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
        tool_name: str | None = None,
        granted_by: str | None = None,
        reason: str | None = None,
        when: str | None = None,
    ) -> None:
        """Insert or replace a grant for the (instance_id, plugin_name,
        tool_name) triple.

        Pass ``tool_name=None`` (default) for a plugin-level grant —
        backward-compatible with the ADR-0043 semantic. Pass a
        non-None ``tool_name`` for a per-tool grant.

        Idempotent: re-granting an already-active triple is a no-op
        side-effect-wise (the row stays). Re-granting after a revoke
        OVERWRITES the prior row at the same triple — the revoked
        record is replaced with a fresh active grant. Caller decides
        whether to emit an audit event for the duplicate-grant case
        (typically yes, for traceability).

        Note: plugin-level and per-tool grants for the same
        (instance_id, plugin_name) coexist — they're different rows
        keyed by tool_name. The dispatcher's specificity-wins
        resolver (ADR-0053 T4, queued) chooses which one applies at
        dispatch time.
        """
        if trust_tier not in ("green", "yellow", "red"):
            raise ValueError(
                f"trust_tier must be one of green/yellow/red, got {trust_tier!r}"
            )
        with transaction(self._conn):
            self._conn.execute(
                """
                INSERT OR REPLACE INTO agent_plugin_grants (
                    instance_id, plugin_name, tool_name, trust_tier,
                    granted_at_seq, granted_by, granted_at,
                    revoked_at_seq, revoked_at, revoked_by, reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?);
                """,
                (
                    instance_id, plugin_name, tool_name, trust_tier,
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
        tool_name: str | None = None,
        revoked_by: str | None = None,
        reason: str | None = None,
        when: str | None = None,
    ) -> bool:
        """Mark an active grant as revoked. Returns True if a row was
        affected, False if no active grant existed at the (instance_id,
        plugin_name, tool_name) triple (caller decides whether to
        surface as 404 or treat as idempotent).

        ``tool_name=None`` revokes the plugin-level grant; passing a
        non-None ``tool_name`` revokes only that per-tool grant and
        leaves the plugin-level grant (if any) intact.

        Sets revoked_at_seq, revoked_at, revoked_by — the grant row
        STAYS for historical audit. A subsequent grant() overwrites
        via INSERT OR REPLACE on the same triple.
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
                  AND tool_name IS ?
                  AND revoked_at_seq IS NULL;
                """,
                (
                    revoked_at_seq,
                    when or utc_now_iso(),
                    revoked_by,
                    reason,
                    instance_id,
                    plugin_name,
                    tool_name,
                ),
            )
            return cur.rowcount > 0

    # -- reads ----------------------------------------------------------

    def list_active(self, instance_id: str) -> list[PluginGrant]:
        """Active grants only (revoked_at_seq IS NULL).

        Returns BOTH plugin-level and per-tool grants. The dispatcher
        calls this on every mcp_call.v1 dispatch (T4) to compute the
        effective per-(plugin, tool) constraint set — must be cheap.
        The idx_plugin_grants_active partial index covers it.
        """
        rows = self._conn.execute(
            """
            SELECT instance_id, plugin_name, tool_name, trust_tier,
                   granted_at_seq, granted_by, granted_at,
                   revoked_at_seq, revoked_at, revoked_by, reason
            FROM agent_plugin_grants
            WHERE instance_id = ?
              AND revoked_at_seq IS NULL
            ORDER BY plugin_name, tool_name;
            """,
            (instance_id,),
        ).fetchall()
        return [_row_to_grant(r) for r in rows]

    def list_active_for_plugin(
        self, instance_id: str, plugin_name: str,
    ) -> list[PluginGrant]:
        """Active grants for ONE plugin — both plugin-level and per-tool.

        The dispatcher's T4 specificity-wins resolver uses this to
        decide which grant tier applies for a (plugin, tool) pair.
        Returns rows ordered (plugin-level first, then per-tool
        alphabetical) so the resolver can short-circuit on the
        first matching row.
        """
        rows = self._conn.execute(
            """
            SELECT instance_id, plugin_name, tool_name, trust_tier,
                   granted_at_seq, granted_by, granted_at,
                   revoked_at_seq, revoked_at, revoked_by, reason
            FROM agent_plugin_grants
            WHERE instance_id = ?
              AND plugin_name = ?
              AND revoked_at_seq IS NULL
            ORDER BY (tool_name IS NULL) DESC, tool_name;
            """,
            (instance_id, plugin_name),
        ).fetchall()
        return [_row_to_grant(r) for r in rows]

    def list_all(self, instance_id: str) -> list[PluginGrant]:
        """Every row including revoked, for audit/UI history views."""
        rows = self._conn.execute(
            """
            SELECT instance_id, plugin_name, tool_name, trust_tier,
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
        self,
        instance_id: str,
        plugin_name: str,
        tool_name: str | None = None,
    ) -> PluginGrant | None:
        """Look up the active grant at the (instance_id, plugin_name,
        tool_name) triple.

        ``tool_name=None`` (default) returns the plugin-level grant
        if one exists — backward-compatible with the ADR-0043 surface.
        Passing a non-None ``tool_name`` returns only the per-tool
        grant at that exact key (does NOT fall back to the plugin-
        level row; that's the resolver's job at T4).
        """
        row = self._conn.execute(
            """
            SELECT instance_id, plugin_name, tool_name, trust_tier,
                   granted_at_seq, granted_by, granted_at,
                   revoked_at_seq, revoked_at, revoked_by, reason
            FROM agent_plugin_grants
            WHERE instance_id = ?
              AND plugin_name = ?
              AND tool_name IS ?
              AND revoked_at_seq IS NULL;
            """,
            (instance_id, plugin_name, tool_name),
        ).fetchone()
        return _row_to_grant(row) if row else None

    def active_plugin_names(self, instance_id: str) -> set[str]:
        """The set of plugin names with any active grant — both
        plugin-level and per-tool count. The cheap union input the
        dispatcher needs at dispatch time. Returns a fresh set on
        every call so the dispatcher can mutate it.

        Per-tool grants count here because owning a per-tool grant
        means the agent has access to *something* inside that
        plugin — the manifest allowlist still admits the plugin
        before mcp_call.v1's tool-name check kicks in. T4's
        specificity-wins resolver then narrows to the right
        trust_tier for the specific tool being dispatched.
        """
        rows = self._conn.execute(
            """
            SELECT DISTINCT plugin_name
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
        tool_name=row[2],
        trust_tier=row[3],
        granted_at_seq=row[4],
        granted_by=row[5],
        granted_at=row[6],
        revoked_at_seq=row[7],
        revoked_at=row[8],
        revoked_by=row[9],
        reason=row[10],
    )
