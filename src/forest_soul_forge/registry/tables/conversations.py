"""ConversationsTable — accessor for ADR-003Y Y1 conversation tables.

Three tables managed by this accessor:

  - ``conversations`` — operator-defined rooms with retention policy.
    One row per conversation, regardless of participant count.
  - ``conversation_participants`` — many-to-many between conversations
    and agents. Composite PK (conversation_id, instance_id). Optional
    ``bridged_from`` records cross-domain invitations (Y4).
  - ``conversation_turns`` — append-only log of operator + agent turns.
    Body retention governed by ``conversations.retention_policy``;
    ``body_hash`` persists for tamper-evidence even after body is
    purged by the Y7 background pass.

The table is a pure SQL surface. Audit emission lives in the calling
layer (the conversations router or the future turn orchestrator —
Y2+) so the accessor doesn't have a circular dep on AuditChain.
``conversation_started`` / ``conversation_archived`` /
``conversation_turn`` / ``conversation_summarized`` /
``conversation_bridged`` events are emitted by the router.

R4 façade pattern: the top-level Registry exposes these methods via
delegation. Direct access through ``Registry.conversations`` is
permitted for code paths that prefer the per-table surface.
"""
from __future__ import annotations

import hashlib
import sqlite3
import uuid
from dataclasses import dataclass

from forest_soul_forge.registry.tables._helpers import transaction, utc_now_iso


# ---------------------------------------------------------------------------
# Row dataclasses — what callers receive from list/get methods.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ConversationRow:
    conversation_id:  str
    domain:           str
    operator_id:      str
    created_at:       str
    last_turn_at:     str | None
    status:           str  # 'active' | 'idle' | 'archived'
    retention_policy: str  # 'full_7d' | 'full_30d' | 'full_indefinite'


@dataclass(frozen=True)
class ParticipantRow:
    conversation_id: str
    instance_id:     str
    joined_at:       str
    bridged_from:    str | None


@dataclass(frozen=True)
class TurnRow:
    turn_id:         str
    conversation_id: str
    speaker:         str          # operator_id OR instance_id
    addressed_to:    str | None   # comma-joined instance_ids; NULL = whole room
    body:            str | None   # NULL once retention window expires
    summary:         str | None
    body_hash:       str
    token_count:     int | None
    timestamp:       str
    model_used:      str | None


# Per ADR-003Y the supported retention policies. Validated at create-time
# so a typo doesn't land a row that the Y7 background pass later trips on.
ALLOWED_RETENTION_POLICIES: frozenset[str] = frozenset(
    {"full_7d", "full_30d", "full_indefinite"}
)
ALLOWED_STATUSES: frozenset[str] = frozenset({"active", "idle", "archived"})


class ConversationNotFoundError(LookupError):
    """Raised when a conversation_id doesn't resolve to a row."""


class ConversationsTable:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ---- conversations CRUD --------------------------------------------
    def create_conversation(
        self,
        *,
        domain:           str,
        operator_id:      str,
        retention_policy: str = "full_7d",
        when:             str | None = None,
        conversation_id:  str | None = None,
    ) -> ConversationRow:
        """Insert a new conversation. Returns the resulting row.

        ``conversation_id`` is generated with uuid4 when not supplied.
        Caller emits ``conversation_started`` to the audit chain.
        """
        if retention_policy not in ALLOWED_RETENTION_POLICIES:
            raise ValueError(
                f"retention_policy must be one of "
                f"{sorted(ALLOWED_RETENTION_POLICIES)}; got {retention_policy!r}"
            )
        if not domain or not isinstance(domain, str):
            raise ValueError("domain is required and must be a non-empty string")
        if not operator_id or not isinstance(operator_id, str):
            raise ValueError("operator_id is required and must be non-empty")

        cid = conversation_id or str(uuid.uuid4())
        ts = when or utc_now_iso()
        with transaction(self._conn):
            self._conn.execute(
                """
                INSERT INTO conversations
                    (conversation_id, domain, operator_id, created_at,
                     last_turn_at, status, retention_policy)
                VALUES (?, ?, ?, ?, NULL, 'active', ?);
                """,
                (cid, domain, operator_id, ts, retention_policy),
            )
        return ConversationRow(
            conversation_id=cid,
            domain=domain,
            operator_id=operator_id,
            created_at=ts,
            last_turn_at=None,
            status="active",
            retention_policy=retention_policy,
        )

    def get_conversation(self, conversation_id: str) -> ConversationRow:
        """Return the row or raise ConversationNotFoundError."""
        row = self._conn.execute(
            """
            SELECT conversation_id, domain, operator_id, created_at,
                   last_turn_at, status, retention_policy
              FROM conversations
             WHERE conversation_id = ?;
            """,
            (conversation_id,),
        ).fetchone()
        if row is None:
            raise ConversationNotFoundError(conversation_id)
        return ConversationRow(**dict(row))

    def list_conversations(
        self,
        *,
        domain:      str | None = None,
        operator_id: str | None = None,
        status:      str | None = None,
        limit:       int = 100,
        offset:      int = 0,
    ) -> list[ConversationRow]:
        """List conversations with optional filters.

        Default sort is most-recent-turn first, with NULL last_turn_at
        falling back to created_at — operator UX expects the room they
        just touched to be at the top.
        """
        clauses: list[str] = []
        params: list = []
        if domain is not None:
            clauses.append("domain = ?")
            params.append(domain)
        if operator_id is not None:
            clauses.append("operator_id = ?")
            params.append(operator_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.extend([int(limit), int(offset)])
        rows = self._conn.execute(
            f"""
            SELECT conversation_id, domain, operator_id, created_at,
                   last_turn_at, status, retention_policy
              FROM conversations
            {where}
             ORDER BY COALESCE(last_turn_at, created_at) DESC
             LIMIT ? OFFSET ?;
            """,
            tuple(params),
        ).fetchall()
        return [ConversationRow(**dict(r)) for r in rows]

    def set_conversation_status(
        self, conversation_id: str, status: str, *, when: str | None = None,
    ) -> None:
        """Update status. Used by archive/idle transitions.

        Raises ValueError on unknown status. Caller emits the relevant
        audit event (``conversation_archived`` etc.).
        """
        if status not in ALLOWED_STATUSES:
            raise ValueError(
                f"status must be one of {sorted(ALLOWED_STATUSES)}; got {status!r}"
            )
        with transaction(self._conn):
            cur = self._conn.execute(
                "UPDATE conversations SET status=? WHERE conversation_id=?;",
                (status, conversation_id),
            )
            if cur.rowcount == 0:
                raise ConversationNotFoundError(conversation_id)

    def set_retention_policy(
        self, conversation_id: str, policy: str,
    ) -> None:
        """Update retention policy. Caller emits ``retention_policy_changed``."""
        if policy not in ALLOWED_RETENTION_POLICIES:
            raise ValueError(
                f"retention_policy must be one of "
                f"{sorted(ALLOWED_RETENTION_POLICIES)}; got {policy!r}"
            )
        with transaction(self._conn):
            cur = self._conn.execute(
                "UPDATE conversations SET retention_policy=? WHERE conversation_id=?;",
                (policy, conversation_id),
            )
            if cur.rowcount == 0:
                raise ConversationNotFoundError(conversation_id)

    # ---- participants --------------------------------------------------
    def add_participant(
        self,
        conversation_id: str,
        instance_id:     str,
        *,
        bridged_from:    str | None = None,
        when:            str | None = None,
    ) -> ParticipantRow:
        """Add an agent to the conversation. Idempotent on
        (conversation_id, instance_id) — re-adding returns the existing
        joined_at/bridged_from.
        """
        ts = when or utc_now_iso()
        with transaction(self._conn):
            self._conn.execute(
                """
                INSERT OR IGNORE INTO conversation_participants
                    (conversation_id, instance_id, joined_at, bridged_from)
                VALUES (?, ?, ?, ?);
                """,
                (conversation_id, instance_id, ts, bridged_from),
            )
        row = self._conn.execute(
            """
            SELECT conversation_id, instance_id, joined_at, bridged_from
              FROM conversation_participants
             WHERE conversation_id = ? AND instance_id = ?;
            """,
            (conversation_id, instance_id),
        ).fetchone()
        return ParticipantRow(**dict(row))

    def list_participants(self, conversation_id: str) -> list[ParticipantRow]:
        rows = self._conn.execute(
            """
            SELECT conversation_id, instance_id, joined_at, bridged_from
              FROM conversation_participants
             WHERE conversation_id = ?
             ORDER BY joined_at ASC;
            """,
            (conversation_id,),
        ).fetchall()
        return [ParticipantRow(**dict(r)) for r in rows]

    def remove_participant(
        self, conversation_id: str, instance_id: str,
    ) -> bool:
        """Remove an agent from a conversation. Returns True if removed."""
        with transaction(self._conn):
            cur = self._conn.execute(
                """
                DELETE FROM conversation_participants
                 WHERE conversation_id = ? AND instance_id = ?;
                """,
                (conversation_id, instance_id),
            )
            return cur.rowcount > 0

    # ---- turns ---------------------------------------------------------
    def append_turn(
        self,
        *,
        conversation_id: str,
        speaker:         str,
        body:            str,
        addressed_to:    str | None = None,
        token_count:     int | None = None,
        model_used:      str | None = None,
        when:            str | None = None,
        turn_id:         str | None = None,
    ) -> TurnRow:
        """Append a turn. Sets ``conversations.last_turn_at`` atomically
        so the room sort stays current.

        ``body_hash`` is SHA-256 of the body bytes (utf-8). Computed
        here so callers can't desync the hash from the body.
        """
        if not isinstance(body, str):
            raise ValueError("body must be a string")
        tid = turn_id or str(uuid.uuid4())
        ts = when or utc_now_iso()
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        with transaction(self._conn):
            # Verify the conversation exists; FK does this implicitly
            # but the explicit check gives a clearer error.
            exists = self._conn.execute(
                "SELECT 1 FROM conversations WHERE conversation_id=?;",
                (conversation_id,),
            ).fetchone()
            if exists is None:
                raise ConversationNotFoundError(conversation_id)
            self._conn.execute(
                """
                INSERT INTO conversation_turns
                    (turn_id, conversation_id, speaker, addressed_to,
                     body, summary, body_hash, token_count, timestamp,
                     model_used)
                VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?);
                """,
                (tid, conversation_id, speaker, addressed_to,
                 body, body_hash, token_count, ts, model_used),
            )
            self._conn.execute(
                "UPDATE conversations SET last_turn_at=? WHERE conversation_id=?;",
                (ts, conversation_id),
            )
        return TurnRow(
            turn_id=tid,
            conversation_id=conversation_id,
            speaker=speaker,
            addressed_to=addressed_to,
            body=body,
            summary=None,
            body_hash=body_hash,
            token_count=token_count,
            timestamp=ts,
            model_used=model_used,
        )

    def list_turns(
        self,
        conversation_id: str,
        *,
        limit:  int = 100,
        offset: int = 0,
    ) -> list[TurnRow]:
        """List turns in chronological order (oldest first).

        Returns post-purge state — turns past their retention window
        have NULL body but populated summary; callers render either.
        """
        rows = self._conn.execute(
            """
            SELECT turn_id, conversation_id, speaker, addressed_to,
                   body, summary, body_hash, token_count, timestamp,
                   model_used
              FROM conversation_turns
             WHERE conversation_id = ?
             ORDER BY timestamp ASC, turn_id ASC
             LIMIT ? OFFSET ?;
            """,
            (conversation_id, int(limit), int(offset)),
        ).fetchall()
        return [TurnRow(**dict(r)) for r in rows]

    def summarize_and_purge_body(
        self, turn_id: str, summary: str,
    ) -> bool:
        """Y7 background pass: write summary, NULL the body. Idempotent
        on already-summarized turns (returns False if the body was
        already NULL — nothing to do).
        """
        if not isinstance(summary, str) or not summary:
            raise ValueError("summary must be a non-empty string")
        with transaction(self._conn):
            cur = self._conn.execute(
                """
                UPDATE conversation_turns
                   SET summary = ?, body = NULL
                 WHERE turn_id = ? AND body IS NOT NULL;
                """,
                (summary, turn_id),
            )
            return cur.rowcount > 0
