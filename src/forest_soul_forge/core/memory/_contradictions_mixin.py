"""``_ContradictionsMixin`` — memory contradictions surface.
ADR-0027-amendment §7.3 + ADR-0036.

Extracted from the Memory god-object per ADR-0040 §7 mixin pattern
(Burst 76, 2026-05-02). Final mixin extraction in T2 — closes the
ADR-0040 T2 decomposition queue for core/memory.py.

Trust surface: cross-entry contradiction tracking. Verifier agents
(ADR-0036) flag contradictions; operators ratify them through the
flagged_state lifecycle (Burst 70's T6+T7 work). The four methods
here own the entire contradictions table:

- ``flag_contradiction``: writer. Stamps a row naming both sides
  of a contradiction (earlier + later entries) with the kind enum
  from ADR-0027-am §7.3.
- ``set_contradiction_state``: writer. Operator ratification path
  (flagged_unreviewed -> flagged_confirmed / flagged_rejected /
  auto_resolved per ADR-0036 §4.3).
- ``find_candidate_pairs``: read-only. Pre-filter for the Verifier
  Loop scan (ADR-0036 §2.1) — returns memory entries that share
  enough vocabulary to plausibly be talking about the same topic.
  Uses the ``_tokenize_for_overlap`` helper from _helpers.py.
- ``unresolved_contradictions_for``: read-only. The recall surface
  (ADR-0036 T7). Surfaces flagged_state per row; default-filters
  flagged_rejected so a known-false flag stops surfacing on
  every recall. ``include_rejected=True`` overrides.

Trust-surface scope (per ADR-0040 §1):
An agent given ``allowed_paths: [".../memory/_contradictions_mixin.py"]``
can extend the contradiction model — for instance, adding new
contradiction_kind values, alternative pre-filter heuristics
(embedding similarity once that lands at v0.4), or new state
transitions in the lifecycle — without inheriting the ability to
grant consents, mark verifications, mark challenges, or write
core memory rows. That separation is the file-grained governance
ADR-0040 §1 identifies as the value of decomposing non-cohesive
god objects.

The Verifier role (ADR-0036 T1) lists this file as its primary
allowed_paths target for memory-mutating work.
"""
from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from forest_soul_forge.core.memory._helpers import (
    _now_iso,
    _tokenize_for_overlap,
)


class _ContradictionsMixin:
    """Mixin for the Memory class — memory_contradictions surface."""

    # ---- v11 epistemic helpers (ADR-0027-amendment §7.3 + §7.4) -----------
    def flag_contradiction(
        self,
        *,
        earlier_entry_id: str,
        later_entry_id: str,
        contradiction_kind: str,
        detected_by: str,
    ) -> tuple[str, str]:
        """Stamp a row into ``memory_contradictions``. ADR-0036 T2.

        The Verifier Loop (ADR-0036) and operator-driven manual flag
        paths both land here. ``detected_by`` is the calling agent's
        instance_id (or operator handle for manual flags). The new
        row's ``resolved_at`` is NULL — operators ratify / reject /
        resolve through admin tools (deferred to v0.3+).

        ``contradiction_kind`` MUST be one of {direct, updated,
        qualified, retracted} per the §7.3 CHECK constraint. Caller
        validates the value before calling — the SQLite CHECK is a
        defense in depth but a clean validation error is preferable
        to a sqlite3.IntegrityError surfacing through the dispatcher.

        Returns (contradiction_id, detected_at_iso) so the caller can
        surface both in the audit-event payload without a follow-up
        read.

        Raises sqlite3.IntegrityError if either entry_id is missing
        from memory_entries (the FK enforces existence — the tool
        layer should validate up-front for a friendlier error).
        """
        contradiction_id = f"contra_{uuid.uuid4().hex[:16]}"
        detected_at = _now_iso()
        # ADR-0036 §4.3 + T6: new rows land at flagged_unreviewed
        # so operators see Verifier flags as proposals, not findings.
        # The default value comes from the column DEFAULT but we set
        # it explicitly here to keep the SQL self-documenting and
        # work on schemas that don't have the column yet (the
        # try/except handles v11-shape DBs gracefully).
        try:
            self.conn.execute(
                """
                INSERT INTO memory_contradictions (
                    contradiction_id, earlier_entry_id, later_entry_id,
                    contradiction_kind, detected_at, detected_by,
                    resolved_at, resolution_summary, flagged_state
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 'flagged_unreviewed');
                """,
                (
                    contradiction_id, earlier_entry_id, later_entry_id,
                    contradiction_kind, detected_at, detected_by,
                ),
            )
        except sqlite3.OperationalError:
            # v11-shape DB without the flagged_state column. Defensive
            # for in-memory tests that don't migrate; production DBs
            # are migrated to v12 at lifespan.
            self.conn.execute(
                """
                INSERT INTO memory_contradictions (
                    contradiction_id, earlier_entry_id, later_entry_id,
                    contradiction_kind, detected_at, detected_by,
                    resolved_at, resolution_summary
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL);
                """,
                (
                    contradiction_id, earlier_entry_id, later_entry_id,
                    contradiction_kind, detected_at, detected_by,
                ),
            )
        return contradiction_id, detected_at

    # ADR-0036 §4.3 + T6 — operator ratification path.
    VALID_FLAGGED_STATES = (
        "flagged_unreviewed", "flagged_confirmed",
        "flagged_rejected", "auto_resolved",
    )

    def set_contradiction_state(
        self, *, contradiction_id: str, new_state: str,
    ) -> bool:
        """Move a contradiction through the ratification lifecycle.

        ADR-0036 §4.3 — operators move Verifier-flagged rows from
        ``flagged_unreviewed`` → ``flagged_confirmed`` /
        ``flagged_rejected`` after review. ``auto_resolved`` is reserved
        for v0.4 system-driven resolution paths.

        Returns True if a row was updated, False if the
        ``contradiction_id`` doesn't exist. Raises ValueError on an
        invalid state value.
        """
        if new_state not in self.VALID_FLAGGED_STATES:
            raise ValueError(
                f"new_state must be one of {self.VALID_FLAGGED_STATES}; "
                f"got {new_state!r}"
            )
        try:
            cur = self.conn.execute(
                "UPDATE memory_contradictions "
                "SET flagged_state = ? WHERE contradiction_id = ?;",
                (new_state, contradiction_id),
            )
        except sqlite3.OperationalError:
            return False
        return cur.rowcount > 0

    def find_candidate_pairs(
        self,
        *,
        instance_id: str,
        since_iso: str | None = None,
        max_pairs: int = 20,
        min_overlap: int = 2,
    ) -> list[dict[str, Any]]:
        """Find candidate pairs for the Verifier Loop's classification
        step. ADR-0036 §2.1.

        Returns a list of pairs (earlier first by created_at) of
        memory entries on the same ``instance_id`` that satisfy:

          * Both ``claim_type`` ∈ {preference, user_statement,
            agent_inference}. Observations and external_facts aren't
            paired — they're directly logged events / external
            authority and contradictions among them are operator
            review territory, not Verifier classification territory.
          * Their content has at least ``min_overlap`` non-stopword
            tokens in common (cheap word-overlap heuristic; embedding
            similarity deferred to v0.4 per ADR-0036's "trade-offs"
            section).
          * NOT already in ``memory_contradictions`` (avoids re-
            flagging resolved or already-flagged-pending pairs).

        Optional ``since_iso`` narrows to entries created after that
        timestamp (useful for incremental scans on a cadence — only
        consider what's new since last scan).

        Capped at ``max_pairs`` (default 20 per ADR-0036 §3
        "conservative defaults"). Pairs sorted by descending overlap
        (most-similar first) so the LLM-classification step gets the
        best candidates within the budget.

        Each entry shape::

            {
              "earlier_entry_id":  str,    # the older of the two
              "later_entry_id":    str,    # the newer of the two
              "earlier_claim_type": str,
              "later_claim_type":   str,
              "shared_words":      list[str],   # the overlap tokens
              "overlap_size":      int,
            }

        Returns empty list when no pairs match — Verifier writes a
        "scanned, nothing flagged" audit event in that case.

        Implementation notes: this is a pure-Python in-memory join.
        A separate-table inverted index would be cheaper at scale
        (10k+ entries per agent), but at v0.3 cadences (daily scan,
        max_pairs=20, typical agent has ~hundreds of entries) the
        full scan is well within budget. v0.4 may revisit if a
        concrete operator surfaces with > 1k entries / agent.
        """
        if max_pairs < 1:
            return []
        if min_overlap < 1:
            min_overlap = 1

        # 1. Pull all eligible entries for this agent.
        eligible_kinds = ("preference", "user_statement", "agent_inference")
        placeholders = ",".join("?" * len(eligible_kinds))
        sql = (
            "SELECT entry_id, content, claim_type, created_at "
            "FROM memory_entries "
            f"WHERE instance_id = ? AND claim_type IN ({placeholders})"
        )
        params: list[Any] = [instance_id, *eligible_kinds]
        if since_iso is not None:
            # Strictly-after semantic: callers pass last_scan_at and
            # want to exclude entries already considered in that prior
            # scan. ``>`` (not ``>=``) drops the boundary entry cleanly.
            sql += " AND created_at > ?"
            params.append(since_iso)
        rows = self.conn.execute(sql, params).fetchall()
        entries = [dict(row) for row in rows]
        if len(entries) < 2:
            return []

        # 2. Pull existing contradictions on this agent's entries to
        # build a dedup set. We exclude any pair whose two entry_ids
        # both appear in any single contradictions row (regardless of
        # which side they were on). Resolved AND unresolved both block
        # — operators don't want re-flag noise on a row they already
        # rejected/resolved.
        try:
            cont_rows = self.conn.execute(
                "SELECT earlier_entry_id, later_entry_id "
                "FROM memory_contradictions",
            ).fetchall()
            dedup: set[frozenset[str]] = {
                frozenset((r["earlier_entry_id"], r["later_entry_id"]))
                for r in cont_rows
            }
        except sqlite3.OperationalError:
            # v10-shape DB without the table — no dedup needed.
            dedup = set()

        # 3. Tokenize each entry's content into stopword-filtered
        # lowercased words. Two entries can be paired if their token
        # sets share >= min_overlap distinct words.
        tokenized: list[tuple[dict[str, Any], frozenset[str]]] = []
        for e in entries:
            tokens = _tokenize_for_overlap(e["content"])
            if len(tokens) >= min_overlap:
                tokenized.append((e, tokens))

        # 4. All-pairs scan. O(n²) but n is bounded; we cap at
        # max_pairs after sorting so worst-case is small.
        pairs: list[dict[str, Any]] = []
        for i, (a, ta) in enumerate(tokenized):
            for b, tb in tokenized[i + 1:]:
                if a["entry_id"] == b["entry_id"]:
                    continue
                shared = ta & tb
                if len(shared) < min_overlap:
                    continue
                key = frozenset((a["entry_id"], b["entry_id"]))
                if key in dedup:
                    continue
                # Order earlier→later by created_at for stable output.
                if a["created_at"] <= b["created_at"]:
                    earlier, later = a, b
                else:
                    earlier, later = b, a
                pairs.append({
                    "earlier_entry_id":   earlier["entry_id"],
                    "later_entry_id":     later["entry_id"],
                    "earlier_claim_type": earlier["claim_type"],
                    "later_claim_type":   later["claim_type"],
                    "shared_words":       sorted(shared),
                    "overlap_size":       len(shared),
                })

        # 5. Sort by descending overlap (most-similar first) so the
        # downstream LLM call spends its budget on the best candidates.
        pairs.sort(key=lambda p: p["overlap_size"], reverse=True)
        return pairs[:max_pairs]

    def unresolved_contradictions_for(
        self, entry_id: str,
        *,
        include_rejected: bool = False,
    ) -> list[dict[str, Any]]:
        """Return all open (unresolved) contradictions where ``entry_id``
        appears as either the earlier or later side.

        Each entry shape (v12+):
          {
            "contradiction_id":   str,
            "earlier_entry_id":   str,
            "later_entry_id":     str,
            "contradiction_kind": "direct"|"updated"|"qualified"|"retracted",
            "detected_at":        ISO timestamp,
            "detected_by":        str,
            "flagged_state":      "flagged_unreviewed"|"flagged_confirmed"|
                                   "flagged_rejected"|"auto_resolved",
          }

        Empty list = no open contradictions. Resolved contradictions are
        explicitly excluded — the recall surface only shows what's still
        open. Operators reviewing resolved history go through a separate
        admin-grade query.

        ADR-0036 §4.3 + T7: ``flagged_rejected`` rows are filtered out
        by default so a known-false flag stops surfacing on every
        recall. Pass ``include_rejected=True`` to see them (operator-
        review surface, audit-trail queries).

        v0.2 callers: memory_recall.v1's surface_contradictions=True
        path. The helper is also useful directly from operator-driven
        admin tools.
        """
        # v12 query — selects flagged_state. The except branch handles
        # v11-shape DBs that don't have the column yet.
        try:
            rows = self.conn.execute(
                """
                SELECT contradiction_id, earlier_entry_id, later_entry_id,
                       contradiction_kind, detected_at, detected_by,
                       flagged_state
                FROM memory_contradictions
                WHERE (earlier_entry_id = ? OR later_entry_id = ?)
                  AND resolved_at IS NULL;
                """,
                (entry_id, entry_id),
            ).fetchall()
            has_state = True
        except sqlite3.OperationalError:
            # Either the table is missing entirely (v10-shape) or the
            # flagged_state column doesn't exist (v11-shape). Try
            # the v11-compatible query.
            try:
                rows = self.conn.execute(
                    """
                    SELECT contradiction_id, earlier_entry_id, later_entry_id,
                           contradiction_kind, detected_at, detected_by
                    FROM memory_contradictions
                    WHERE (earlier_entry_id = ? OR later_entry_id = ?)
                      AND resolved_at IS NULL;
                    """,
                    (entry_id, entry_id),
                ).fetchall()
                has_state = False
            except sqlite3.OperationalError:
                # No table at all — v10 or earlier.
                return []
        out: list[dict[str, Any]] = []
        for r in rows:
            state = r["flagged_state"] if has_state else "flagged_unreviewed"
            if not include_rejected and state == "flagged_rejected":
                continue
            out.append({
                "contradiction_id":   r["contradiction_id"],
                "earlier_entry_id":   r["earlier_entry_id"],
                "later_entry_id":     r["later_entry_id"],
                "contradiction_kind": r["contradiction_kind"],
                "detected_at":        r["detected_at"],
                "detected_by":        r["detected_by"],
                "flagged_state":      state,
            })
        return out

