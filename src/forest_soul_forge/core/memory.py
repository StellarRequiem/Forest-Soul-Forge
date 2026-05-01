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

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
LAYERS = ("episodic", "semantic", "procedural")
SCOPES = ("private", "lineage", "realm", "consented")

# v0.2 (ADR-0033 / ADR-0022 v0.2) recall modes — control how widely
# the reader can see across other agents' memory stores.
#
#   private   — owner-only, scope='private'. Default. Equivalent to v0.1.
#   lineage   — owner's private+lineage entries + lineage_chain peers'
#               scope='lineage' entries. The swarm's escalation path
#               (security_low → security_mid → security_high).
#   consented — lineage + scope='consented' entries the reader has an
#               active grant for in memory_consents.
#
# `realm` is unreachable until federation lands (Horizon 3); deliberately
# omitted from RECALL_MODES so an attempt to use it raises a clear error
# instead of silently returning empty results.
RECALL_MODES = ("private", "lineage", "consented")

# Genre privacy floors per ADR-0027 §5. The mapping is keyed by genre
# name and gives the **widest scope the genre is allowed to write**.
# Companion is the strictest. Genres absent from this map default to
# "consented" (no enforced ceiling beyond the four scopes themselves).
GENRE_CEILINGS: dict[str, str] = {
    "companion":   "private",
    "observer":    "lineage",
    "investigator": "lineage",
    "researcher":  "consented",
    "communicator": "realm",
    # actuator + guardian are operator-defined per deployment;
    # default to "consented" until explicitly tightened.
}

_SCOPE_RANK = {"private": 0, "lineage": 1, "realm": 2, "consented": 3}

# ADR-0027-amendment §7.1 — six-class enum for ``claim_type``. Schema-level
# CHECK constraint enforces these values; this Python tuple is the source
# of truth for write-time validation. The default 'observation' is the
# safest classification (immutable, high-confidence by default).
CLAIM_TYPES: tuple[str, ...] = (
    "observation",      # direct event log; high reliability
    "user_statement",   # operator-stated; reliability bounded by operator
    "agent_inference",  # agent-derived; explicitly NOT operator's stated word
    "preference",       # operator's stated preference
    "promise",          # operator's stated commitment with implicit deadline
    "external_fact",    # claim sourced outside the agent-operator dyad
)
_CLAIM_TYPE_SET = frozenset(CLAIM_TYPES)

# ADR-0027-amendment §7.2 — three-state confidence. Float confidence
# invites agents to rationalize precision they don't have ("0.73") that
# means nothing the operator can interpret. Three-state aligns with UI.
CONFIDENCE_LEVELS: tuple[str, ...] = ("low", "medium", "high")
_CONFIDENCE_SET = frozenset(CONFIDENCE_LEVELS)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class MemoryError(Exception):
    """Base class for memory subsystem failures."""


class MemoryScopeViolation(MemoryError):
    """Raised when a write exceeds the genre's ceiling.

    ADR-0027 §5 — genre privacy floors are HARD CEILINGS, not
    defaults. The caller must narrow the scope or the operator must
    explicitly override (with the override hitting the audit chain
    as ``memory_scope_override``).
    """


class UnknownLayerError(MemoryError):
    pass


class UnknownClaimTypeError(MemoryError):
    """Raised when a write specifies an unknown claim_type. v11 addition
    (ADR-0027-amendment §7.1)."""


class UnknownConfidenceError(MemoryError):
    """Raised when a write specifies a confidence outside the three-state
    enum (low/medium/high). v11 addition (ADR-0027-amendment §7.2)."""


class UnknownScopeError(MemoryError):
    pass


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MemoryEntry:
    """One row from the memory_entries table.

    The trailing three fields (added in schema v7 / ADR-0022 v0.2) are
    populated only on **disclosed-copy** rows on the recipient's side
    per ADR-0027 §4 minimum-disclosure rule. On originating-side rows
    they are ``None``.

    A row with ``disclosed_from_entry`` set means: "this is a reference
    copy I was told about by another agent, not an original observation
    of mine." Tools that surface memory to operators or LLMs should
    distinguish the two — the summary string is intentionally narrower
    than the original entry's content.
    """

    entry_id: str
    instance_id: str
    agent_dna: str
    layer: str
    scope: str
    content: str
    content_digest: str
    tags: tuple[str, ...]
    consented_to: tuple[str, ...]
    created_at: str
    deleted_at: str | None = None
    disclosed_from_entry: str | None = None
    disclosed_summary: str | None = None
    disclosed_at: str | None = None
    # v11 additions (ADR-0027-amendment §7) — epistemic metadata.
    # Defaults match the schema CHECK column DEFAULTs so that v10-shape
    # in-memory test fixtures (which still use the old append() args)
    # land on the safe classification.
    claim_type: str = "observation"
    confidence: str = "medium"
    last_challenged_at: str | None = None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def is_disclosed_copy(self) -> bool:
        """True iff this row is a disclosed copy on a recipient store
        (not an original observation). Useful for UI rendering and for
        the audit trail summary."""
        return self.disclosed_from_entry is not None


# ---------------------------------------------------------------------------
# Memory class
# ---------------------------------------------------------------------------
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

    def get(self, entry_id: str) -> MemoryEntry | None:
        row = self.conn.execute(
            "SELECT * FROM memory_entries WHERE entry_id=?;",
            (entry_id,),
        ).fetchone()
        return _row_to_entry(row) if row is not None else None

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(s: str) -> str:
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()


def _row_to_entry(row) -> MemoryEntry:
    # The v7 disclosed_* columns and v11 claim_type/confidence/
    # last_challenged_at columns may be absent on a row from an older
    # in-memory test fixture or a registry that hasn't been migrated
    # yet. Defensively probe via row.keys() so this helper works on
    # every shape — important for Memory unit tests that build their
    # own SQLite without going through Registry.bootstrap.
    keys = row.keys() if hasattr(row, "keys") else ()
    return MemoryEntry(
        entry_id=row["entry_id"],
        instance_id=row["instance_id"],
        agent_dna=row["agent_dna"],
        layer=row["layer"],
        scope=row["scope"],
        content=row["content"],
        content_digest=row["content_digest"],
        tags=tuple(json.loads(row["tags_json"] or "[]")),
        consented_to=tuple(json.loads(row["consented_to_json"] or "[]")),
        created_at=row["created_at"],
        deleted_at=row["deleted_at"],
        disclosed_from_entry=row["disclosed_from_entry"]
            if "disclosed_from_entry" in keys else None,
        disclosed_summary=row["disclosed_summary"]
            if "disclosed_summary" in keys else None,
        disclosed_at=row["disclosed_at"]
            if "disclosed_at" in keys else None,
        # v11 — defensive: pre-migration rows lack these columns. Defaults
        # match the schema CHECK column DEFAULTs ('observation', 'medium')
        # so a v10-shape row reads as an observation at medium confidence.
        claim_type=row["claim_type"]
            if "claim_type" in keys else "observation",
        confidence=row["confidence"]
            if "confidence" in keys else "medium",
        last_challenged_at=row["last_challenged_at"]
            if "last_challenged_at" in keys else None,
    )
