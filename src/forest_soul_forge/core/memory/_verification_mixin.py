"""``_VerificationMixin`` — Iron Gate verified-memory tier. ADR-003X K1.

Extracted from the Memory god-object per ADR-0040 §7 mixin pattern
(Burst 74, 2026-05-02). The methods here operate on the
``memory_verifications`` table.

Trust surface: verified-memory promotion (the K1 "Iron Gate"
primitive). An external human verifier promotes a memory entry to
verified status; ``memory_recall.v1``'s K1 fold reads the table and
surfaces ``confidence='high'`` when an entry is actively verified.
ADR-0027-am §7.3 reuses the same table indirectly via the
``unresolved_contradictions_for`` recall surface (T7 of ADR-0036).

Why a dedicated table (not memory_consents):
``memory_consents.recipient_instance`` has a FK to
``agents(instance_id)``, but the verifier identifier is a human
handle (operator handle / public-key fingerprint / signing handle),
not a registered agent. The K1 schema introduced
``memory_verifications`` so the verifier identifier is a free-form
string with no agent-FK requirement.

Lifecycle:
- ``mark_verified``: promote entry to verified (idempotent;
  re-verification refreshes timestamp + clears prior revocation).
- ``unmark_verified``: revoke verification (sets revoked_at +
  revoked_by; the row stays so audit-trail queries can still answer
  "who verified this and when?").
- ``is_verified`` / ``get_verifier``: read paths used by recall and
  operator review surfaces.

Audit-chain emission:
``memory_verified`` / ``memory_verification_revoked`` events fire
from the runtime, not from this mixin. The mixin records the row
state; the audit chain captures the operator's intent.

Trust-surface scope (per ADR-0040 §1):
An agent given ``allowed_paths: [".../memory/_verification_mixin.py"]``
can extend the Iron Gate model — for instance, multi-signature
verification, or per-verifier reputation tracking — without
inheriting access to consent grants, contradictions flagging, or
core memory writes. That separation is the file-grained governance
ADR-0040 §1 identifies as the value of decomposing non-cohesive
god objects.
"""
from __future__ import annotations

from forest_soul_forge.core.memory._helpers import _now_iso


class _VerificationMixin:
    """Mixin for the Memory class — Iron Gate verified-memory tier."""

    def mark_verified(
        self, *, entry_id: str, verifier_id: str, seal_note: str | None = None,
    ) -> None:
        """Promote ``entry_id`` to verified. ``verifier_id`` is the human
        verifier's identifier (operator handle, public key fingerprint,
        signing handle). Idempotent — re-verification updates the
        timestamp, clears any prior revocation, and replaces seal_note.
        """
        self.conn.execute(
            """
            INSERT INTO memory_verifications (
                entry_id, verifier_id, verified_at, seal_note,
                revoked_at, revoked_by
            ) VALUES (?, ?, ?, ?, NULL, NULL)
            ON CONFLICT(entry_id) DO UPDATE SET
                verifier_id = excluded.verifier_id,
                verified_at = excluded.verified_at,
                seal_note   = excluded.seal_note,
                revoked_at  = NULL,
                revoked_by  = NULL;
            """,
            (entry_id, verifier_id, _now_iso(), seal_note),
        )

    def unmark_verified(
        self, *, entry_id: str, revoker_id: str = "operator",
    ) -> bool:
        """Revoke verification on ``entry_id``. Returns True if a row
        was updated. The row stays — only ``revoked_at`` + ``revoked_by``
        are set — so the audit-trail of who verified and when stays
        queryable.
        """
        cur = self.conn.execute(
            """
            UPDATE memory_verifications
            SET revoked_at = ?, revoked_by = ?
            WHERE entry_id = ? AND revoked_at IS NULL;
            """,
            (_now_iso(), revoker_id, entry_id),
        )
        return cur.rowcount > 0

    def is_verified(self, *, entry_id: str) -> bool:
        """True iff ``entry_id`` has an active (non-revoked) verification."""
        row = self.conn.execute(
            """
            SELECT 1 FROM memory_verifications
            WHERE entry_id = ? AND revoked_at IS NULL
            LIMIT 1;
            """,
            (entry_id,),
        ).fetchone()
        return row is not None

    def get_verifier(self, *, entry_id: str) -> str | None:
        """Return the verifier_id for the active verification on
        ``entry_id``, or None if not verified. Operators want to know
        "who signed off on this" when reviewing the chain — this is
        the lookup.
        """
        row = self.conn.execute(
            """
            SELECT verifier_id FROM memory_verifications
            WHERE entry_id = ? AND revoked_at IS NULL
            LIMIT 1;
            """,
            (entry_id,),
        ).fetchone()
        return row["verifier_id"] if row else None
