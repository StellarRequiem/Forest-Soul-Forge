"""``_ChallengeMixin`` — staleness pressure + operator scrutiny.
ADR-0027-amendment §7.4.

Extracted from the Memory god-object per ADR-0040 §7 mixin pattern
(Burst 75, 2026-05-02).

Trust surface: explicit operator scrutiny on a memory entry without
writing a competing entry. Distinct from a contradiction (which has
two competing entries on the same topic — that's the
_ContradictionsMixin's surface). A challenge is the operator
saying "this entry is being scrutinized" — it bumps
``last_challenged_at`` so ``memory_recall.v1``'s staleness flag
surfaces the entry on next read.

Methods owned by this mixin:
- ``mark_challenged``: writer. Stamps ``last_challenged_at`` to
  NOW. Idempotent in shape (always overwrites). Caller emits
  ``memory_challenged`` on the audit chain.
- ``is_entry_stale``: predicate. Returns True iff the entry's
  last-touch timestamp (last_challenged_at or created_at) is older
  than ``threshold_days``. Used by ``memory_recall.v1`` to flag
  entries that haven't been re-asserted in a while.

Trust-surface scope (per ADR-0040 §1):
An agent given ``allowed_paths: [".../memory/_challenge_mixin.py"]``
can extend the staleness model — for instance, per-claim-type
threshold defaults, or layered staleness bands — without inheriting
the ability to grant consents, mark verifications, flag
contradictions, or write core memory rows.

Why challenge is its own surface (not folded into contradictions):
Per ADR-0027-am §7.4, the operator can challenge an entry WITHOUT
yet writing the competing entry that would form a contradiction.
Sometimes the operator just wants to flag "I'm not sure about this"
without committing to a replacement. The two surfaces are
genuinely distinct.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from forest_soul_forge.core.memory._helpers import MemoryEntry, _now_iso


class _ChallengeMixin:
    """Mixin for the Memory class — challenge stamping + staleness check."""

    def mark_challenged(self, *, entry_id: str) -> str:
        """Stamp ``last_challenged_at`` on ``entry_id`` to the current
        UTC time and return the timestamp written.

        Per ADR-0027-amendment §7.4, a challenge is an explicit operator
        signal that an entry is in question — distinct from a
        contradiction (which has a competing later entry). The challenge
        itself doesn't change the entry's content or claim_type; it
        just records "this is being scrutinized."

        Idempotent in shape (always overwrites with NOW), but each call
        produces a fresh timestamp + a fresh audit-chain event when the
        caller emits ``memory_challenged``. Operators reviewing history
        see every challenge in the chain.

        Returns the ISO-8601 timestamp written so the caller can include
        it in the audit-event payload without a follow-up read.
        """
        ts = _now_iso()
        self.conn.execute(
            "UPDATE memory_entries SET last_challenged_at = ? WHERE entry_id = ?;",
            (ts, entry_id),
        )
        return ts

    def is_entry_stale(
        self,
        entry: "MemoryEntry",
        *,
        threshold_days: int,
        now_iso: str | None = None,
    ) -> bool:
        """ADR-0027-amendment §7.4 — staleness pressure check.

        An entry is stale when:
          - its ``last_challenged_at`` is older than ``threshold_days``
            (the entry was last touched / verified / contradicted that
            long ago), OR
          - its ``last_challenged_at`` is NULL AND its ``created_at``
            is older than ``threshold_days`` (the entry has never been
            challenged but is older than the threshold).

        Threshold is in days. Caller chooses per-claim-type defaults
        (memory_recall.v1 does this); 30 days is a reasonable default
        for ``agent_inference`` per ADR §7.4.

        ``now_iso`` injectable for deterministic testing. Defaults to
        the current UTC time. ISO-8601 string comparison is correct
        here because both sides use the same _now_iso() format.
        """
        if threshold_days <= 0:
            return False
        # Last touch is the latest-of(last_challenged_at, created_at).
        # If last_challenged_at is None we fall back to created_at — an
        # entry that's never been touched since creation IS as old as
        # its creation.
        last_touch = entry.last_challenged_at or entry.created_at
        if not last_touch:
            return False
        # Comparison via ISO-8601 string sort (lexicographic == temporal).
        if now_iso is None:
            now_iso = _now_iso()
        # _now_iso uses ISO-8601 'YYYY-MM-DDTHH:MM:SSZ' format (T
        # separator). Parse + subtract threshold; fail-open (not stale)
        # if the parse fails. The lexicographic compare is correct
        # because both timestamps use the same format and the format
        # sorts chronologically as a string.
        try:
            now = datetime.strptime(now_iso, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc,
            )
        except ValueError:
            # Tolerate either T-separator or space-separator on the
            # input timestamp. Tests + older fixtures may use either.
            try:
                now = datetime.strptime(
                    now_iso, "%Y-%m-%d %H:%M:%SZ"
                ).replace(tzinfo=timezone.utc)
            except ValueError:
                return False
        cutoff = now - timedelta(days=threshold_days)
        # Format the cutoff to match `last_touch` separator. Try
        # T-separator first (the canonical _now_iso shape); fall back
        # to space if the entry's timestamp uses the older shape.
        if "T" in last_touch:
            cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            cutoff_iso = cutoff.strftime("%Y-%m-%d %H:%M:%SZ")
        return last_touch < cutoff_iso
