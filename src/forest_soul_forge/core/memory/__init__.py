"""Memory subsystem v0.1 — ADR-0022 implementation under ADR-0027.

Per-agent memory store. Three layers (episodic | semantic |
procedural) and four scopes (only `private` is reachable in v0.1
per ADR-0027 §1).

Read path:
    Memory(registry).recall(instance_id, layer=..., query=..., limit=...)

Write path:
    Memory(registry).append(instance_id, agent_dna, content, layer,
                            tags=..., scope=..., genre=...)

The genre check enforces ADR-0027 §5 — Companion-genre agents
cannot write any scope wider than `private`. Hard ceiling, not a
default. If the caller asks for a wider scope on a Companion the
write raises :class:`MemoryScopeViolation` and refuses.

Hard / soft delete (ADR-0027 §3):
    Memory(...).soft_delete(entry_id)  -> tombstone, audit-evident
    Memory(...).purge(entry_id)        -> hard remove + memory_purged

The runtime emits per-operation audit events: memory_written,
memory_deleted, memory_purged, memory_scope_override, etc. The chain
is the source of truth for who saw what.
"""
from __future__ import annotations

# ADR-0040 T2.1 — package layout. The Memory class lives here at
# package level so existing imports (`from forest_soul_forge.core.memory
# import Memory`) keep working. Module-level constants, dataclass,
# errors, and pure helpers live in `_helpers.py` so agent allowed_paths
# can target the read-only surface separately from the class.
#
# Per-trust-surface mixin extractions (T2.2+) follow in subsequent
# bursts: _consents_mixin.py, _verification_mixin.py, _challenge_mixin.py,
# _contradictions_mixin.py, _core_mixin.py.
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from forest_soul_forge.core.memory._helpers import (
    # Constants
    LAYERS, SCOPES, RECALL_MODES, GENRE_CEILINGS,
    CLAIM_TYPES, CONFIDENCE_LEVELS,
    _SCOPE_RANK, _CLAIM_TYPE_SET, _CONFIDENCE_SET,
    # Errors
    MemoryError, MemoryScopeViolation,
    UnknownLayerError, UnknownClaimTypeError,
    UnknownConfidenceError, UnknownScopeError,
    # Dataclass
    MemoryEntry,
    # Helpers
    _now_iso, _sha256, _row_to_entry,
    _OVERLAP_STOPWORDS, _tokenize_for_overlap,
)

__all__ = [
    "Memory",
    "MemoryEntry",
    "MemoryError", "MemoryScopeViolation",
    "UnknownLayerError", "UnknownClaimTypeError",
    "UnknownConfidenceError", "UnknownScopeError",
    "LAYERS", "SCOPES", "RECALL_MODES", "GENRE_CEILINGS",
    "CLAIM_TYPES", "CONFIDENCE_LEVELS",
    "_now_iso", "_sha256", "_tokenize_for_overlap",
]


@dataclass
class Memory:
    """Memory API surface. Constructed with a registry connection
    (single-writer SQLite discipline preserved). The runtime holds
    one Memory instance on app.state and routes per-agent calls
    through it.
    """

    conn: sqlite3.Connection

    # ---- write path ----------------------------------------------------
    def append(
        self,
        *,
        instance_id: str,
        agent_dna: str,
        content: str,
        layer: str,
        tags: tuple[str, ...] = (),
        scope: str = "private",
        genre: str | None = None,
        consented_to: tuple[str, ...] = (),
        # v11 — ADR-0027-amendment §7. Optional with safe defaults so
        # existing callers (every memory_write tool, every test) keep
        # working without an explicit claim_type.
        claim_type: str = "observation",
        confidence: str = "medium",
    ) -> MemoryEntry:
        """Insert one entry. Validates layer + scope + genre ceiling +
        claim_type + confidence before touching the table.

        Raises :class:`MemoryScopeViolation` if the scope exceeds the
        genre's ceiling. Caller (the runtime) emits ``memory_written``
        on the audit chain after a successful write.

        ``claim_type`` defaults to ``"observation"`` (the safest
        classification — directly logged events). Inferences should
        be tagged ``"agent_inference"`` so they don't silently surface
        as user-stated facts. ``confidence`` defaults to ``"medium"``;
        observations and user_statements should typically be ``"high"``,
        agent inferences ``"low"`` (ADR-0027-amendment §7.2 — full
        per-claim-type defaulting is deferred to T7's reclassify pass).
        """
        if layer not in LAYERS:
            raise UnknownLayerError(
                f"layer must be one of {list(LAYERS)}; got {layer!r}"
            )
        if scope not in SCOPES:
            raise UnknownScopeError(
                f"scope must be one of {list(SCOPES)}; got {scope!r}"
            )
        if claim_type not in _CLAIM_TYPE_SET:
            raise UnknownClaimTypeError(
                f"claim_type must be one of {list(CLAIM_TYPES)}; got {claim_type!r}"
            )
        if confidence not in _CONFIDENCE_SET:
            raise UnknownConfidenceError(
                f"confidence must be one of {list(CONFIDENCE_LEVELS)}; got {confidence!r}"
            )
        if genre is not None:
            ceiling = GENRE_CEILINGS.get(genre.lower(), "consented")
            if _SCOPE_RANK[scope] > _SCOPE_RANK[ceiling]:
                raise MemoryScopeViolation(
                    f"genre {genre!r} ceiling is {ceiling!r}; cannot "
                    f"write scope {scope!r}. Operator override required."
                )

        entry_id = str(uuid.uuid4())
        digest = _sha256(content)
        created_at = _now_iso()
        self.conn.execute(
            """
            INSERT INTO memory_entries (
                entry_id, instance_id, agent_dna, layer, scope,
                content, content_digest, tags_json, consented_to_json,
                created_at, claim_type, confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                entry_id, instance_id, agent_dna, layer, scope,
                content, digest,
                json.dumps(list(tags), separators=(",", ":")),
                json.dumps(list(consented_to), separators=(",", ":")),
                created_at, claim_type, confidence,
            ),
        )
        return MemoryEntry(
            entry_id=entry_id,
            instance_id=instance_id,
            agent_dna=agent_dna,
            layer=layer,
            scope=scope,
            content=content,
            content_digest=digest,
            tags=tuple(tags),
            consented_to=tuple(consented_to),
            created_at=created_at,
            claim_type=claim_type,
            confidence=confidence,
        )

    # ---- read path -----------------------------------------------------
    def recall(
        self,
        *,
        instance_id: str,
        layer: str | None = None,
        query: str | None = None,
        limit: int = 50,
        include_deleted: bool = False,
    ) -> list[MemoryEntry]:
        """Return entries matching the filters, newest first.

        ``query`` is a substring match against content + tags. v0.1
        uses LIKE — no full-text search yet (a later tranche adds
        FTS5 once we know the access patterns).

        Same-agent self-reads are NOT audited (ADR-0027 §6 — too
        noisy and the memory is already in scope). The runtime is
        responsible for cross-agent audit emission when memory
        crosses an agent boundary.
        """
        clauses = ["instance_id = ?"]
        params: list[Any] = [instance_id]
        if layer is not None:
            if layer not in LAYERS:
                raise UnknownLayerError(
                    f"layer must be one of {list(LAYERS)}; got {layer!r}"
                )
            clauses.append("layer = ?")
            params.append(layer)
        if not include_deleted:
            clauses.append("deleted_at IS NULL")
        if query:
            clauses.append("(content LIKE ? OR tags_json LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like])
        sql = (
            "SELECT * FROM memory_entries WHERE "
            + " AND ".join(clauses)
            # ORDER BY created_at DESC, rowid DESC — the rowid
            # tiebreaker makes "newest first" airtight. ISO-8601
            # timestamps from _now_iso() collide when two entries are
            # appended in the same microsecond (multi-agent skills,
            # rapid-fire writes); without the tiebreaker SQLite returns
            # ties in undefined order which in practice means oldest-
            # first, silently inverting the documented contract.
            + " ORDER BY created_at DESC, rowid DESC LIMIT ?;"
        )
        params.append(int(limit))
        rows = self.conn.execute(sql, params).fetchall()
        return [_row_to_entry(r) for r in rows]

    def recall_visible_to(
        self,
        *,
        reader_instance_id: str,
        mode: str = "private",
        lineage_chain: tuple[str, ...] = (),
        layer: str | None = None,
        query: str | None = None,
        limit: int = 50,
        include_deleted: bool = False,
    ) -> list[MemoryEntry]:
        """Recall from the reader's perspective, honoring scope visibility.

        ADR-0033 + ADR-0022 v0.2 cross-agent disclosure path. Three modes:

          mode='private'  — equivalent to v0.1 ``recall(reader)``: only
                             reader's own ``scope='private'`` rows.
          mode='lineage'  — reader's own private+lineage entries, PLUS
                             any ``scope='lineage'`` row whose owner is
                             in ``lineage_chain``. Caller computes the
                             chain (parent + descendants) from the
                             agent_ancestry table; this method just
                             filters.
          mode='consented' — lineage's set, PLUS any ``scope='consented'``
                             row the reader has an active grant for in
                             memory_consents (revoked_at IS NULL).

        ``lineage_chain`` may include the reader's own instance_id; the
        OR semantics tolerate that (rows aren't double-counted).

        ``include_deleted=False`` skips tombstoned rows (default). Soft-
        deleted entries are still in the table per ADR-0027 §3 but
        excluded from default reads.

        ``query`` is a substring match against content + tags +
        disclosed_summary so disclosed copies are findable by the same
        terms an operator would use against an original entry.

        ``realm`` mode is intentionally not supported — federation
        (Horizon 3) hasn't landed and ``realm`` scope is unreachable.
        Asking for ``mode='realm'`` raises :class:`UnknownScopeError`
        rather than silently returning empty results.
        """
        if mode not in RECALL_MODES:
            raise UnknownScopeError(
                f"recall mode must be one of {list(RECALL_MODES)}; "
                f"got {mode!r}. ('realm' scope is reserved for H3 "
                f"federation and unreachable today.)"
            )
        if layer is not None and layer not in LAYERS:
            raise UnknownLayerError(
                f"layer must be one of {list(LAYERS)}; got {layer!r}"
            )

        # Build the visibility predicate. Each clause is an OR-ed
        # condition over (instance_id, scope). Parameters are
        # accumulated in the same order so positional binding works
        # cleanly.
        visibility_clauses: list[str] = []
        params: list[Any] = []

        # Always: reader's own private entries (every mode).
        visibility_clauses.append(
            "(instance_id = ? AND scope = 'private')"
        )
        params.append(reader_instance_id)

        # `lineage` and `consented` modes also see lineage entries.
        if mode in ("lineage", "consented"):
            # Reader's own scope='lineage' entries.
            visibility_clauses.append(
                "(instance_id = ? AND scope = 'lineage')"
            )
            params.append(reader_instance_id)
            # Lineage chain peers' scope='lineage' entries. Use a
            # placeholder list of the right cardinality. Empty chain →
            # skip the clause entirely (no peers, no extra visibility).
            chain = tuple(set(lineage_chain) - {reader_instance_id})
            if chain:
                placeholders = ",".join("?" for _ in chain)
                visibility_clauses.append(
                    f"(instance_id IN ({placeholders}) AND scope = 'lineage')"
                )
                params.extend(chain)

        # `consented` also sees consented rows the reader has a grant for.
        if mode == "consented":
            # Reader's own scope='consented' entries.
            visibility_clauses.append(
                "(instance_id = ? AND scope = 'consented')"
            )
            params.append(reader_instance_id)
            # Cross-agent consented entries: the reader has an active
            # grant in memory_consents.
            visibility_clauses.append(
                "(scope = 'consented' AND entry_id IN ("
                "  SELECT entry_id FROM memory_consents "
                "  WHERE recipient_instance = ? AND revoked_at IS NULL"
                "))"
            )
            params.append(reader_instance_id)

        clauses = ["(" + " OR ".join(visibility_clauses) + ")"]
        if layer is not None:
            clauses.append("layer = ?")
            params.append(layer)
        if not include_deleted:
            clauses.append("deleted_at IS NULL")
        if query:
            # Match content, tags, OR disclosed_summary so disclosed
            # copies surface for the same terms.
            clauses.append(
                "(content LIKE ? OR tags_json LIKE ? "
                "OR disclosed_summary LIKE ?)"
            )
            like = f"%{query}%"
            params.extend([like, like, like])

        sql = (
            "SELECT * FROM memory_entries WHERE "
            + " AND ".join(clauses)
            # ORDER BY created_at DESC, rowid DESC — the rowid
            # tiebreaker makes "newest first" airtight. ISO-8601
            # timestamps from _now_iso() collide when two entries are
            # appended in the same microsecond (multi-agent skills,
            # rapid-fire writes); without the tiebreaker SQLite returns
            # ties in undefined order which in practice means oldest-
            # first, silently inverting the documented contract.
            + " ORDER BY created_at DESC, rowid DESC LIMIT ?;"
        )
        params.append(int(limit))
        rows = self.conn.execute(sql, params).fetchall()
        return [_row_to_entry(r) for r in rows]

    # ---- consent path (ADR-0022 v0.2) ----------------------------------
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

    # ---- verification path (ADR-003X K1 — Iron Gate equivalent) --------
    # Reuses the consent-grant SEMANTIC (idempotent promote + revoke,
    # external party stamps standing on an entry) but stores it in a
    # dedicated memory_verifications table because the
    # memory_consents FK on recipient_instance → agents would reject
    # the verifier identifier (which is a human handle, not an
    # agent). One row per entry; re-verification updates in place;
    # revocation sets revoked_at + revoked_by. Caller emits
    # ``memory_verified`` / ``memory_verification_revoked`` on the
    # audit chain.

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

    # ---- challenge path (ADR-0027-amendment §7.4) -------------------------
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

    def get(self, entry_id: str) -> MemoryEntry | None:
        row = self.conn.execute(
            "SELECT * FROM memory_entries WHERE entry_id=?;",
            (entry_id,),
        ).fetchone()
        return _row_to_entry(row) if row is not None else None

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
        from datetime import datetime, timedelta, timezone
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

    def count(
        self, instance_id: str, *, include_deleted: bool = False,
    ) -> int:
        """Convenience for character-sheet stats."""
        if include_deleted:
            sql = "SELECT COUNT(*) FROM memory_entries WHERE instance_id=?;"
        else:
            sql = (
                "SELECT COUNT(*) FROM memory_entries "
                "WHERE instance_id=? AND deleted_at IS NULL;"
            )
        row = self.conn.execute(sql, (instance_id,)).fetchone()
        return int(row[0]) if row else 0

    # ---- delete path ---------------------------------------------------
    def soft_delete(self, entry_id: str) -> bool:
        """Mark an entry as deleted (tombstone). Returns True if a row
        was updated. ADR-0027 §3 — soft delete keeps the row in the
        table with content cleared; audit-chain integrity preserved.
        """
        cur = self.conn.execute(
            """
            UPDATE memory_entries
            SET deleted_at=?, content='', content_digest=''
            WHERE entry_id=? AND deleted_at IS NULL;
            """,
            (_now_iso(), entry_id),
        )
        return cur.rowcount > 0

    def purge(self, entry_id: str) -> bool:
        """Hard delete — remove the row entirely. ADR-0027 §3 says
        this must be paired with a memory_purged audit-chain entry by
        the caller. The Memory class doesn't write to the chain — that's
        the runtime's job (separation of concerns mirrors how the tool
        runtime emits its own events on top of the dispatcher's
        operations)."""
        cur = self.conn.execute(
            "DELETE FROM memory_entries WHERE entry_id=?;",
            (entry_id,),
        )
        return cur.rowcount > 0


