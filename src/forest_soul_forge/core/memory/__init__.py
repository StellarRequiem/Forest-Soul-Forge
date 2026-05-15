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

# ADR-0040 T2.2 — per-trust-surface mixins. Each mixin owns one
# trust surface; the Memory class composes them via MRO. Public
# API is exactly preserved (memory.grant_consent(...) still works).
from forest_soul_forge.core.memory._challenge_mixin import _ChallengeMixin
from forest_soul_forge.core.memory._consents_mixin import _ConsentsMixin
from forest_soul_forge.core.memory._contradictions_mixin import _ContradictionsMixin
from forest_soul_forge.core.memory._verification_mixin import _VerificationMixin

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
class Memory(
    _ConsentsMixin,
    _VerificationMixin,
    _ChallengeMixin,
    _ContradictionsMixin,
):
    """Memory API surface. Constructed with a registry connection
    (single-writer SQLite discipline preserved). The runtime holds
    one Memory instance on app.state and routes per-agent calls
    through it.

    Per ADR-0040 §7, per-trust-surface methods live in mixin classes:
    - _ConsentsMixin: grant_consent / revoke_consent / is_consented
      (cross-agent disclosure, ADR-0027 §2)
    - _VerificationMixin: mark_verified / unmark_verified /
      is_verified / get_verifier (Iron Gate, ADR-003X K1)
    - _ChallengeMixin: mark_challenged / is_entry_stale
      (operator scrutiny + staleness pressure, ADR-0027-am §7.4)
    - _ContradictionsMixin: flag_contradiction / set_contradiction_state
      / find_candidate_pairs / unresolved_contradictions_for
      (cross-entry contradiction tracking, ADR-0027-am §7.3 + ADR-0036)

    What stays in __init__.py: core CRUD trust surface
    (append / recall / get / count / soft_delete / purge) plus the
    class declaration that assembles the mixins. That residual
    surface is the 'core memory' trust surface per ADR-0040 §1
    and is intentionally not extracted — it IS the cohesive core.

    ADR-0050 T4 (B269): optional at-rest encryption. When
    ``encryption_config`` is set, :meth:`append` encrypts the
    ``content`` string before INSERT and stores
    ``content_encrypted=1``; :meth:`recall` (and ``get``)
    detect the flag and decrypt transparently. Mixed plaintext +
    encrypted entries on the same table are explicitly supported
    per ADR Decision 6 — operators turn on encryption mid-
    lifecycle, old entries stay plaintext (their flag is 0),
    new entries land encrypted (flag 1).
    """

    conn: sqlite3.Connection
    # ADR-0050 T4 (B269). None = pre-T4 plaintext path. When set,
    # all NEW memory writes encrypt content + tag the row with
    # content_encrypted=1; reads transparently decrypt flagged rows.
    encryption_config: Any = None
    # ADR-0076 T2 (B320). Optional indexer hook — when set, every
    # successful append() of a `scope='personal'` row enqueues an
    # embed task on the indexer's async queue, which lands the
    # entry's content into PersonalIndex so personal_recall.v1
    # (T4) sees it within ms-to-seconds. None = pre-T2 path: no
    # vector indexing, recall falls back to SQL LIKE alone.
    #
    # Restricted to scope='personal' on purpose: only operator-
    # context entries should leak into the operator's PersonalIndex.
    # Agent-private journal entries stay out of the cross-instance
    # vector surface. The recall tool (T4) is the one place that
    # decides "is this query asking for operator context?"; the
    # write-side gate keeps the index small + privacy-preserving.
    indexer: Any = None

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
            # ADR-0068 T3 (B313) — `personal` is off the ceiling ladder.
            # Genre must be in the explicit allow-list; the rank check
            # would otherwise refuse personal writes from companion
            # (companion's ceiling=private=0, but personal_rank=4 is
            # arithmetically "wider"). The allow-list is the right
            # semantics: personal-scope is operator-bound context,
            # eligible only for the specific genres in
            # PERSONAL_SCOPE_ALLOWED_GENRES.
            if scope == "personal":
                from forest_soul_forge.core.memory._helpers import (
                    PERSONAL_SCOPE_ALLOWED_GENRES,
                )
                if genre.lower() not in PERSONAL_SCOPE_ALLOWED_GENRES:
                    raise MemoryScopeViolation(
                        f"genre {genre!r} cannot write scope 'personal'; "
                        f"allowed: {sorted(PERSONAL_SCOPE_ALLOWED_GENRES)}"
                    )
            else:
                ceiling = GENRE_CEILINGS.get(genre.lower(), "consented")
                if _SCOPE_RANK[scope] > _SCOPE_RANK[ceiling]:
                    raise MemoryScopeViolation(
                        f"genre {genre!r} ceiling is {ceiling!r}; cannot "
                        f"write scope {scope!r}. Operator override required."
                    )

        entry_id = str(uuid.uuid4())
        # ADR-0050 T4 (B269): content_digest is computed over the
        # PLAINTEXT content, NOT the encrypted form. This preserves
        # the property that the digest is a stable identity over the
        # plaintext payload across key rotations and across the
        # plaintext-vs-encrypted opt-in. Verifiers and any callers
        # that compute their own digest on retrieved content see the
        # same value regardless of the row's at-rest encryption flag.
        digest = _sha256(content)
        created_at = _now_iso()
        # ADR-0050 T4 (B269): encrypt the content under the master
        # key + tag the row when ``encryption_config`` is set. Old
        # rows stay plaintext (flag=0); new rows tag flag=1 so reads
        # know which payload column to interpret.
        content_for_storage: str = content
        content_encrypted_flag: int = 0
        if self.encryption_config is not None:
            from forest_soul_forge.core.at_rest_encryption import (
                encrypt_text,
            )
            content_for_storage = encrypt_text(content, self.encryption_config)
            content_encrypted_flag = 1
        self.conn.execute(
            """
            INSERT INTO memory_entries (
                entry_id, instance_id, agent_dna, layer, scope,
                content, content_digest, tags_json, consented_to_json,
                created_at, claim_type, confidence, content_encrypted
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                entry_id, instance_id, agent_dna, layer, scope,
                content_for_storage, digest,
                json.dumps(list(tags), separators=(",", ":")),
                json.dumps(list(consented_to), separators=(",", ":")),
                created_at, claim_type, confidence, content_encrypted_flag,
            ),
        )
        # ADR-0076 T2 (B320). Scope-filtered indexer hook. Only
        # `personal` entries land in the PersonalIndex; the rest
        # stay out so an agent's private memory doesn't leak into
        # cross-instance semantic recall. The indexer's enqueue is
        # non-blocking — if the embed worker is unhealthy, writes
        # still land in SQL + the audit chain; recall just misses
        # this entry until the next `fsf index rebuild` (T5).
        if self.indexer is not None and scope == "personal":
            try:
                self.indexer.enqueue(
                    doc_id=entry_id,
                    text=content,
                    source=f"memory:{layer}:{scope}",
                    tags=tuple(tags) if tags else None,
                )
            except Exception:
                # Indexer enqueue must NEVER fail a memory write.
                # The chain is the source of truth; the index is
                # a derivative. Worst case: this entry isn't
                # searchable via semantic recall until rebuild.
                # We swallow the exception here rather than logging
                # because the indexer itself logs internally + the
                # status() snapshot surfaces the failure.
                pass
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
        return [_row_to_entry(r, encryption_config=self.encryption_config) for r in rows]

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

        # ADR-0068 T3 (B313) — `personal` mode is a NON-additive
        # lookup: it returns ONLY personal-scope rows, not the
        # reader's private+lineage+consented layers. The other modes
        # (private/lineage/consented) build a wider-and-wider OR
        # surface over the reader's own visibility tree; personal
        # is orthogonal (it crosses instance boundaries to surface
        # the operator's context, regardless of who wrote the row).
        # Skip the always-on private clause when personal mode is
        # active so we don't leak the reader's private surface into
        # the operator-context view.
        if mode != "personal":
            # Always: reader's own private entries (every other mode).
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

        # ADR-0068 T3 (B313) — `personal` mode returns ONLY personal-
        # scope entries across all instance_ids. The operator owns
        # all personal-scope rows; the lineage-additive layering of
        # the other modes doesn't apply here. The TOOL layer
        # (memory_recall.v1) gates this mode by genre via
        # PERSONAL_SCOPE_ALLOWED_GENRES BEFORE calling recall_visible_to
        # — the Memory class does not know about genres on the read
        # path, matching the existing layering.
        #
        # Returning all personal-scope rows means a reader-instance
        # constraint isn't applied: any companion / assistant /
        # operator_steward / domain_orchestrator can read any
        # personal-scope row regardless of which agent wrote it.
        # That's the desired semantic — personal-scope memory belongs
        # to the operator, not to the writing agent.
        if mode == "personal":
            visibility_clauses.append("(scope = 'personal')")
            # No params; the clause has no parameters.

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
        return [_row_to_entry(r, encryption_config=self.encryption_config) for r in rows]

    # ---- consent path (ADR-0022 v0.2) ----------------------------------
    # Methods extracted to _consents_mixin.py per ADR-0040 §7 (Burst 73).
    # The Memory class inherits grant_consent / revoke_consent /
    # is_consented from _ConsentsMixin via the class declaration above.

    # ---- verification path (ADR-003X K1 — Iron Gate equivalent) --------
    # Methods extracted to _verification_mixin.py per ADR-0040 §7
    # (Burst 74). The Memory class inherits mark_verified /
    # unmark_verified / is_verified / get_verifier from
    # _VerificationMixin via the class declaration above.

    # ---- challenge path (ADR-0027-amendment §7.4) -------------------------
    # mark_challenged + is_entry_stale extracted to _challenge_mixin.py
    # per ADR-0040 §7 (Burst 75). Inherited via _ChallengeMixin.

    def get(self, entry_id: str) -> MemoryEntry | None:
        row = self.conn.execute(
            "SELECT * FROM memory_entries WHERE entry_id=?;",
            (entry_id,),
        ).fetchone()
        return _row_to_entry(row, encryption_config=self.encryption_config) if row is not None else None

    # ---- v11 epistemic helpers (ADR-0027-amendment §7.3 + §7.4) -----------
    # flag_contradiction / set_contradiction_state /
    # find_candidate_pairs / unresolved_contradictions_for extracted
    # to _contradictions_mixin.py per ADR-0040 §7 (Burst 76).
    # Inherited via _ContradictionsMixin (declared on the class above).
    # The VALID_FLAGGED_STATES class attribute moves with the methods.

    # is_entry_stale extracted to _challenge_mixin.py per ADR-0040 §7
    # (Burst 75). Inherited via _ChallengeMixin.

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


