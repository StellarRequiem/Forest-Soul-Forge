"""CatalogGrantsTable — accessor for ``agent_catalog_grants``
(ADR-0060 T1, Burst 219).

Runtime grants of catalog-tool access to a born agent without
mutating the constitution. Sister table to ``agent_plugin_grants``
(ADR-0043 follow-up #2) but keyed on
``(instance_id, tool_name, tool_version)`` instead of
``(instance_id, plugin_name)``.

The constitution_hash is immutable per agent (CLAUDE.md
architectural invariant). This table is consulted alongside the
constitution at dispatch time:

    constitution lists tool        → use constitution's resolved constraints
    not listed, grant active       → use catalog defaults (T2 wiring)
    not listed, no grant           → refuse tool_not_in_constitution

This burst (T1) ships the table + accessor only. T2 wires the
dispatcher to consult ``get_active(instance_id, name, version)`` on
constitution-check miss.

Audit emission is the caller's responsibility — this table is a
pure SQL surface. The two event types
(``agent_tool_granted`` and ``agent_tool_revoked``) emit from the
endpoint that wraps these calls (T3).

trust_tier defaults to ``yellow`` per ADR-0060 D4. Operators must
explicitly pass ``green`` to grant fully-autonomous tier. T4 wires
the posture × trust_tier interaction matrix.
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
class CatalogGrant:
    """One row in agent_catalog_grants — active or historical."""
    instance_id: str
    tool_name: str
    tool_version: str
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
    def tool_key(self) -> str:
        """Canonical ``name.vversion`` rendering — matches the
        ToolCatalog key shape so callers can index uniformly."""
        return f"{self.tool_name}.v{self.tool_version}"


# ---- table accessor ----------------------------------------------------

class CatalogGrantsTable:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # -- writes ---------------------------------------------------------

    def grant(
        self,
        *,
        instance_id: str,
        tool_name: str,
        tool_version: str,
        trust_tier: str,
        granted_at_seq: int,
        granted_by: str | None = None,
        reason: str | None = None,
        when: str | None = None,
    ) -> None:
        """Insert or replace a grant for (instance_id, tool_name, tool_version).

        Idempotent: re-granting an already-active grant is a no-op
        side-effect-wise. Re-granting after a revoke OVERWRITES the
        prior row with a fresh active grant; the revoked record is
        replaced. Caller decides whether to emit an audit event for
        the duplicate-grant case (typically yes, for traceability).
        """
        if trust_tier not in ("green", "yellow", "red"):
            raise ValueError(
                f"trust_tier must be one of green/yellow/red, got {trust_tier!r}"
            )
        with transaction(self._conn):
            self._conn.execute(
                """
                INSERT OR REPLACE INTO agent_catalog_grants (
                    instance_id, tool_name, tool_version, trust_tier,
                    granted_at_seq, granted_by, granted_at,
                    revoked_at_seq, revoked_at, revoked_by, reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?);
                """,
                (
                    instance_id, tool_name, tool_version, trust_tier,
                    granted_at_seq, granted_by, when or utc_now_iso(),
                    reason,
                ),
            )

    def revoke(
        self,
        *,
        instance_id: str,
        tool_name: str,
        tool_version: str,
        revoked_at_seq: int,
        revoked_by: str | None = None,
        reason: str | None = None,
        when: str | None = None,
    ) -> bool:
        """Mark an active grant as revoked. Returns True if a row was
        affected, False if no active grant existed (caller decides
        whether to surface as 404 or treat as idempotent — ADR-0060
        D3 makes DELETE idempotent so the caller should not 404 on
        revoke-of-already-revoked).

        Sets revoked_at_seq, revoked_at, revoked_by — the grant row
        STAYS for historical audit. A subsequent grant() overwrites
        via INSERT OR REPLACE.
        """
        with transaction(self._conn):
            cur = self._conn.execute(
                """
                UPDATE agent_catalog_grants
                SET revoked_at_seq = ?,
                    revoked_at     = ?,
                    revoked_by     = ?,
                    reason         = COALESCE(?, reason)
                WHERE instance_id  = ?
                  AND tool_name    = ?
                  AND tool_version = ?
                  AND revoked_at_seq IS NULL;
                """,
                (
                    revoked_at_seq,
                    when or utc_now_iso(),
                    revoked_by,
                    reason,
                    instance_id,
                    tool_name,
                    tool_version,
                ),
            )
            return cur.rowcount > 0

    # -- reads ----------------------------------------------------------

    def list_active(self, instance_id: str) -> list[CatalogGrant]:
        """Active grants only (revoked_at_seq IS NULL).

        The dispatcher (T2) will call this on every dispatch that
        misses the constitution check — must be cheap. The
        idx_catalog_grants_active partial index covers it.
        """
        rows = self._conn.execute(
            """
            SELECT instance_id, tool_name, tool_version, trust_tier,
                   granted_at_seq, granted_by, granted_at,
                   revoked_at_seq, revoked_at, revoked_by, reason
            FROM agent_catalog_grants
            WHERE instance_id = ?
              AND revoked_at_seq IS NULL
            ORDER BY tool_name, tool_version;
            """,
            (instance_id,),
        ).fetchall()
        return [_row_to_grant(r) for r in rows]

    def list_all(self, instance_id: str) -> list[CatalogGrant]:
        """Every row including revoked, for audit/UI history views."""
        rows = self._conn.execute(
            """
            SELECT instance_id, tool_name, tool_version, trust_tier,
                   granted_at_seq, granted_by, granted_at,
                   revoked_at_seq, revoked_at, revoked_by, reason
            FROM agent_catalog_grants
            WHERE instance_id = ?
            ORDER BY granted_at_seq DESC;
            """,
            (instance_id,),
        ).fetchall()
        return [_row_to_grant(r) for r in rows]

    def get_active(
        self, instance_id: str, tool_name: str, tool_version: str,
    ) -> CatalogGrant | None:
        """Single-grant lookup the dispatcher (T2) will use. Returns
        the active CatalogGrant if one exists, else None.
        """
        row = self._conn.execute(
            """
            SELECT instance_id, tool_name, tool_version, trust_tier,
                   granted_at_seq, granted_by, granted_at,
                   revoked_at_seq, revoked_at, revoked_by, reason
            FROM agent_catalog_grants
            WHERE instance_id  = ?
              AND tool_name    = ?
              AND tool_version = ?
              AND revoked_at_seq IS NULL;
            """,
            (instance_id, tool_name, tool_version),
        ).fetchone()
        return _row_to_grant(row) if row else None

    def active_tool_keys(self, instance_id: str) -> set[str]:
        """The set of ``name.vversion`` keys with active grants for
        this agent — cheap union input the dispatcher needs for
        per-call gating decisions. Returns a fresh set per call so
        the dispatcher can mutate it.
        """
        rows = self._conn.execute(
            """
            SELECT tool_name, tool_version
            FROM agent_catalog_grants
            WHERE instance_id = ?
              AND revoked_at_seq IS NULL;
            """,
            (instance_id,),
        ).fetchall()
        return {f"{r[0]}.v{r[1]}" for r in rows}


# ---- helpers -----------------------------------------------------------

def _row_to_grant(row) -> CatalogGrant:
    return CatalogGrant(
        instance_id=row[0],
        tool_name=row[1],
        tool_version=row[2],
        trust_tier=row[3],
        granted_at_seq=row[4],
        granted_by=row[5],
        granted_at=row[6],
        revoked_at_seq=row[7],
        revoked_at=row[8],
        revoked_by=row[9],
        reason=row[10],
    )
