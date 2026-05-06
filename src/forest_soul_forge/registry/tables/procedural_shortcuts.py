"""ProceduralShortcutsTable — accessor for
``memory_procedural_shortcuts`` (ADR-0054 T1, Burst 178).

Each row is one (situation → action) pattern the agent has stored
for fast recall. The dispatcher's ProceduralShortcutStep (T3)
consults this table BEFORE firing llm_think; on a high-confidence
match, the recorded action is returned directly + a
tool_call_shortcut audit event lands in the chain (T4).

Per ADR-0054 Decision 2 (match algorithm):
    cosine(situation_embedding, query_embedding) >= 0.92  AND
    (success_count - failure_count) >= 2

Per ADR-0001 D2 identity invariance: this table holds per-instance
STATE, not identity. constitution_hash + DNA stay immutable; only
what the agent KNOWS evolves. Operators can rebuild this table
freely without touching the agent's identity.

Embedding storage: float32 little-endian BLOB. NumPy array.tobytes()
on write; np.frombuffer(blob, dtype=np.float32) on read. Vector
dimension is whatever the operator's embedding model produces
(nomic-embed-text → 768). Mixed-dimension rows would be a
configuration error; the search helper rejects them at runtime
rather than corrupting cosine math.

Audit emission is the caller's responsibility — this table is a
pure SQL surface. The new tool_call_shortcut event type emits
from the dispatcher's ProceduralShortcutStep (T4).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

import numpy as np

from forest_soul_forge.registry.tables._helpers import (
    transaction,
    utc_now_iso,
)


# ---- public dataclass --------------------------------------------------

@dataclass(frozen=True)
class ProceduralShortcut:
    """One row in memory_procedural_shortcuts."""
    shortcut_id: str
    instance_id: str
    created_at: str
    last_matched_at: str | None
    last_matched_seq: int | None

    situation_text: str
    situation_embedding: np.ndarray  # float32 1-D

    action_kind: str       # 'response' | 'tool_call' | 'no_op'
    action_payload: dict   # decoded JSON

    success_count: int
    failure_count: int

    learned_from_seq: int
    learned_from_kind: str  # 'auto' | 'operator_tagged'

    @property
    def reinforcement_score(self) -> int:
        """Net positive matches. Negative = candidate for soft-delete."""
        return self.success_count - self.failure_count


# ---- table accessor ----------------------------------------------------

class ProceduralShortcutsTable:
    """SQL surface over memory_procedural_shortcuts. Pure data layer;
    audit + governance live in the dispatcher's ProceduralShortcutStep
    (T3) and reinforcement tools (T5)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # -- writes ---------------------------------------------------------

    def put(
        self,
        *,
        shortcut_id: str,
        instance_id: str,
        situation_text: str,
        situation_embedding: np.ndarray,
        action_kind: str,
        action_payload: dict,
        learned_from_seq: int,
        learned_from_kind: str = "auto",
        when: str | None = None,
    ) -> ProceduralShortcut:
        """Insert a new shortcut row. Each call inserts a fresh row;
        if the operator wants to re-train an existing pattern they
        delete + re-insert, or use the strengthen/weaken helpers
        below. Idempotency on shortcut_id is the caller's
        responsibility."""
        if action_kind not in ("response", "tool_call", "no_op"):
            raise ValueError(
                f"action_kind must be one of "
                f"('response', 'tool_call', 'no_op'); got {action_kind!r}"
            )
        if learned_from_kind not in ("auto", "operator_tagged"):
            raise ValueError(
                f"learned_from_kind must be 'auto' or 'operator_tagged'; "
                f"got {learned_from_kind!r}"
            )
        emb = _encode_embedding(situation_embedding)
        created = when or utc_now_iso()
        with transaction(self._conn):
            self._conn.execute(
                """
                INSERT INTO memory_procedural_shortcuts (
                    shortcut_id, instance_id, created_at,
                    situation_text, situation_embedding,
                    action_kind, action_payload,
                    success_count, failure_count,
                    learned_from_seq, learned_from_kind
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?);
                """,
                (
                    shortcut_id, instance_id, created,
                    situation_text, emb,
                    action_kind, json.dumps(action_payload),
                    learned_from_seq, learned_from_kind,
                ),
            )
        # Re-read so the returned dataclass reflects exactly what
        # SQLite stored (defensive against future trigger logic).
        return self.get(shortcut_id)

    def strengthen(self, shortcut_id: str, *, by: int = 1) -> None:
        """Increment success_count by ``by`` (default 1).

        Called by ProceduralShortcutStep when an operator tags a
        turn good (T5) or — once auto-strengthen lands — when a
        match fires + the operator doesn't follow up with a
        correction within a threshold window.
        """
        if by <= 0:
            raise ValueError("strengthen by must be positive")
        with transaction(self._conn):
            self._conn.execute(
                "UPDATE memory_procedural_shortcuts "
                "SET success_count = success_count + ? "
                "WHERE shortcut_id = ?;",
                (by, shortcut_id),
            )

    def weaken(self, shortcut_id: str, *, by: int = 1) -> None:
        """Increment failure_count by ``by`` (default 1).

        Called when an operator tags a turn bad. If failure_count
        exceeds success_count, the row stays in the table but the
        match path soft-deletes it (skips during search).
        """
        if by <= 0:
            raise ValueError("weaken by must be positive")
        with transaction(self._conn):
            self._conn.execute(
                "UPDATE memory_procedural_shortcuts "
                "SET failure_count = failure_count + ? "
                "WHERE shortcut_id = ?;",
                (by, shortcut_id),
            )

    def record_match(self, shortcut_id: str, *, at_seq: int, when: str | None = None) -> None:
        """Update last_matched_at + last_matched_seq when the
        dispatcher's ProceduralShortcutStep selects this row.
        Separate from strengthen/weaken — recording a match is
        bookkeeping; whether it counts as success/failure is the
        operator's tag (T5)."""
        ts = when or utc_now_iso()
        with transaction(self._conn):
            self._conn.execute(
                "UPDATE memory_procedural_shortcuts "
                "SET last_matched_at = ?, last_matched_seq = ? "
                "WHERE shortcut_id = ?;",
                (ts, at_seq, shortcut_id),
            )

    def delete(self, shortcut_id: str) -> None:
        """Hard-delete one shortcut. Operator-driven (no-op on
        unknown id)."""
        with transaction(self._conn):
            self._conn.execute(
                "DELETE FROM memory_procedural_shortcuts "
                "WHERE shortcut_id = ?;",
                (shortcut_id,),
            )

    # -- reads ----------------------------------------------------------

    def get(self, shortcut_id: str) -> ProceduralShortcut:
        """Fetch one row by id. Raises KeyError if absent."""
        cur = self._conn.execute(
            """
            SELECT shortcut_id, instance_id, created_at,
                   last_matched_at, last_matched_seq,
                   situation_text, situation_embedding,
                   action_kind, action_payload,
                   success_count, failure_count,
                   learned_from_seq, learned_from_kind
              FROM memory_procedural_shortcuts
             WHERE shortcut_id = ?;
            """,
            (shortcut_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise KeyError(f"no shortcut with id {shortcut_id!r}")
        return _row_to_dataclass(row)

    def list_by_instance(
        self,
        instance_id: str,
        *,
        include_negative: bool = False,
    ) -> list[ProceduralShortcut]:
        """List all shortcuts for an agent. Default excludes
        soft-deleted (failure_count > success_count) entries —
        pass include_negative=True for forensic / management
        views."""
        if include_negative:
            cur = self._conn.execute(
                "SELECT * FROM memory_procedural_shortcuts "
                "WHERE instance_id = ? "
                "ORDER BY created_at DESC;",
                (instance_id,),
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM memory_procedural_shortcuts "
                "WHERE instance_id = ? AND success_count >= failure_count "
                "ORDER BY created_at DESC;",
                (instance_id,),
            )
        return [_row_to_dataclass(r) for r in cur.fetchall()]

    def search_by_cosine(
        self,
        instance_id: str,
        query_embedding: np.ndarray,
        *,
        cosine_floor: float = 0.92,
        reinforcement_floor: int = 2,
        top_k: int = 1,
    ) -> list[tuple[ProceduralShortcut, float]]:
        """Return shortcuts matching ``query_embedding`` above the
        cosine + reinforcement floors, sorted by combined score
        (cosine + 0.05·log(success_count + 1)) descending.

        Returns at most ``top_k`` tuples of (shortcut, cosine_score).
        Empty list when nothing qualifies — the dispatcher's caller
        falls through to llm_think in that case.

        Brute-force scan over the agent's rows. At single-operator
        scale (hundreds of entries) this completes in single-digit
        ms. If a deployment grows past a few thousand rows per
        agent, swap in an ANN index without changing this signature.
        """
        if cosine_floor < 0 or cosine_floor > 1:
            raise ValueError("cosine_floor must be in [0, 1]")
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        q = _normalize(query_embedding)

        # Pull only the eligible-by-reinforcement rows so we don't
        # waste cosine math on soft-deleted entries.
        cur = self._conn.execute(
            "SELECT * FROM memory_procedural_shortcuts "
            "WHERE instance_id = ? "
            "  AND (success_count - failure_count) >= ? ;",
            (instance_id, reinforcement_floor),
        )
        ranked: list[tuple[ProceduralShortcut, float, float]] = []
        for row in cur.fetchall():
            entry = _row_to_dataclass(row)
            if entry.situation_embedding.shape != q.shape:
                # Mixed embedding dimension — caller misconfigured.
                # Skip (don't crash the whole search) but make the
                # mismatch visible.
                continue
            stored_norm = _normalize(entry.situation_embedding)
            cos = float(np.dot(q, stored_norm))
            if cos < cosine_floor:
                continue
            # Combined score per ADR-0054 D2.
            import math
            score = cos + 0.05 * math.log(entry.success_count + 1)
            ranked.append((entry, cos, score))

        ranked.sort(key=lambda t: t[2], reverse=True)
        return [(e, cos) for (e, cos, _score) in ranked[:top_k]]

    def count_by_instance(self, instance_id: str) -> int:
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM memory_procedural_shortcuts "
            "WHERE instance_id = ?;",
            (instance_id,),
        )
        return int(cur.fetchone()[0])


# ---- internals ---------------------------------------------------------

def _encode_embedding(arr: np.ndarray) -> bytes:
    """float32 little-endian BLOB. Forces dtype + contiguity so the
    on-disk format is stable across NumPy versions and platforms."""
    if arr.ndim != 1:
        raise ValueError(f"embedding must be 1-D; got shape {arr.shape}")
    if not np.issubdtype(arr.dtype, np.floating):
        raise ValueError(f"embedding must be a float array; got dtype {arr.dtype}")
    return np.ascontiguousarray(arr, dtype=np.float32).tobytes()


def _decode_embedding(blob: bytes) -> np.ndarray:
    """Inverse of _encode_embedding."""
    return np.frombuffer(blob, dtype=np.float32).copy()
    # .copy() so the consumer can mutate without aliasing the SQLite
    # buffer (which is read-only in some SQLite builds).


def _normalize(arr: np.ndarray) -> np.ndarray:
    """L2-normalize for cosine similarity. Returns a fresh array."""
    arr = arr.astype(np.float32, copy=False)
    norm = np.linalg.norm(arr)
    if norm == 0:
        # Zero vector — cosine undefined. Return as-is; the search
        # path's cosine check will produce 0 against any non-zero
        # query, naturally excluding it from results.
        return arr
    return arr / norm


def _row_to_dataclass(row) -> ProceduralShortcut:
    """sqlite3.Row → ProceduralShortcut. Tolerates both Row and
    plain tuple rows so test code can use either."""
    # Index by name when possible (Row), else positional (tuple).
    if hasattr(row, "keys"):
        get = lambda k: row[k]
    else:
        # Positional order matches the SELECT * column order in DDL.
        keys = (
            "shortcut_id", "instance_id", "created_at",
            "last_matched_at", "last_matched_seq",
            "situation_text", "situation_embedding",
            "action_kind", "action_payload",
            "success_count", "failure_count",
            "learned_from_seq", "learned_from_kind",
        )
        idx = {k: i for i, k in enumerate(keys)}
        get = lambda k: row[idx[k]]

    return ProceduralShortcut(
        shortcut_id=get("shortcut_id"),
        instance_id=get("instance_id"),
        created_at=get("created_at"),
        last_matched_at=get("last_matched_at"),
        last_matched_seq=get("last_matched_seq"),
        situation_text=get("situation_text"),
        situation_embedding=_decode_embedding(get("situation_embedding")),
        action_kind=get("action_kind"),
        action_payload=json.loads(get("action_payload")),
        success_count=get("success_count"),
        failure_count=get("failure_count"),
        learned_from_seq=get("learned_from_seq"),
        learned_from_kind=get("learned_from_kind"),
    )
