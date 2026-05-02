"""``_ConsentsMixin`` — per-event memory consent grants. ADR-0027 §2.

Extracted from the Memory god-object per ADR-0040 §7 mixin pattern
(Burst 73, 2026-05-02). The methods here operate on the
``memory_consents`` table and govern cross-agent disclosure.

Trust surface: cross-agent disclosure boundary. An agent given
``allowed_paths: [".../memory/_consents_mixin.py"]`` can extend
the consent grant model — for instance, adding TTL / expiry on
grants or per-recipient permission scopes — without inheriting
the ability to flag contradictions, mark verifications, or write
core memory rows. That's the file-grained governance ADR-0040 §1
identifies as the value of decomposing non-cohesive god objects.

The mixin assumes an underlying ``self.conn`` (sqlite3.Connection
or compatible) populated by the Memory class's ``__init__``. No
internal state is held in the mixin itself — calls flow through
the connection.

Audit-chain emission: each grant / revoke is the operator's
responsibility (the Memory class records the row; the runtime
emits ``memory_consent_granted`` / ``memory_consent_revoked`` on
the chain). ADR-0027 §6 — one event per information-flow boundary
crossing.
"""
from __future__ import annotations

from forest_soul_forge.core.memory._helpers import _now_iso


class _ConsentsMixin:
    """Mixin for the Memory class — consent grants on memory entries."""

    def grant_consent(
        self,
        *,
        entry_id: str,
        recipient_instance: str,
        granted_by: str,
    ) -> None:
        """Record a per-event consent grant from the entry's owner to
        ``recipient_instance``. Idempotent on the (entry_id, recipient)
        pair — re-granting an already-granted consent updates the
        ``granted_at`` timestamp and clears any ``revoked_at`` so a
        previously revoked consent can be re-granted cleanly.

        Caller is responsible for emitting ``memory_consent_granted``
        on the audit chain.
        """
        self.conn.execute(
            """
            INSERT INTO memory_consents (
                entry_id, recipient_instance, granted_at, granted_by, revoked_at
            ) VALUES (?, ?, ?, ?, NULL)
            ON CONFLICT(entry_id, recipient_instance) DO UPDATE SET
                granted_at = excluded.granted_at,
                granted_by = excluded.granted_by,
                revoked_at = NULL;
            """,
            (entry_id, recipient_instance, _now_iso(), granted_by),
        )

    def revoke_consent(
        self,
        *,
        entry_id: str,
        recipient_instance: str,
    ) -> bool:
        """Revoke a previously granted consent. Returns True if a row
        was updated. Per ADR-0027 §2 — withdrawal does NOT propagate to
        copies the recipient already disclosed; that's the deletion
        contract's job. The Memory class records the revocation; the
        runtime emits ``memory_consent_revoked`` on the chain.
        """
        cur = self.conn.execute(
            """
            UPDATE memory_consents
            SET revoked_at = ?
            WHERE entry_id = ? AND recipient_instance = ?
              AND revoked_at IS NULL;
            """,
            (_now_iso(), entry_id, recipient_instance),
        )
        return cur.rowcount > 0

    def is_consented(
        self, *, entry_id: str, recipient_instance: str,
    ) -> bool:
        """True iff ``recipient_instance`` has an active (non-revoked)
        consent grant on ``entry_id``."""
        row = self.conn.execute(
            """
            SELECT 1 FROM memory_consents
            WHERE entry_id = ? AND recipient_instance = ?
              AND revoked_at IS NULL
            LIMIT 1;
            """,
            (entry_id, recipient_instance),
        ).fetchone()
        return row is not None
