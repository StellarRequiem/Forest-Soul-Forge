"""IdempotencyTable — accessor for the ``idempotency_keys`` table.

ADR-0007: every mutating endpoint honors X-Idempotency-Key. We store
``(key, endpoint, request_hash)`` so a replay with the same key AND
same body returns the cached response verbatim; a replay with the same
key but a different body is rejected (409) instead of silently served
from cache. The daemon calls these methods inside the write lock so
the check-then-insert is atomic against concurrent identical
submissions.

R4: extracted from registry.py. Public method names preserved exactly
so the Registry façade's back-compat delegates can pass through 1:1.
"""
from __future__ import annotations

import sqlite3

from forest_soul_forge.registry._errors import IdempotencyMismatchError

__all__ = ["IdempotencyMismatchError", "IdempotencyTable"]


class IdempotencyTable:
    """Accessor for ``idempotency_keys``."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def lookup_idempotency_key(
        self, key: str, endpoint: str, request_hash: str
    ) -> tuple[int, str] | None:
        """Return ``(status_code, response_json)`` for a cached key, or None.

        Raises :class:`IdempotencyMismatchError` when the key exists for
        this endpoint but with a different request hash — that's a
        client bug, not a cache miss, and we want the caller to surface
        it as a 409 rather than re-execute.
        """
        row = self._conn.execute(
            "SELECT endpoint, request_hash, status_code, response_json "
            "FROM idempotency_keys WHERE key=?;",
            (key,),
        ).fetchone()
        if row is None:
            return None
        if row["endpoint"] != endpoint or row["request_hash"] != request_hash:
            raise IdempotencyMismatchError(key, endpoint)
        return int(row["status_code"]), row["response_json"]

    def store_idempotency_key(
        self,
        key: str,
        endpoint: str,
        request_hash: str,
        status_code: int,
        response_json: str,
        created_at: str,
    ) -> None:
        """Cache a successful response for future replays of ``key``.

        ``INSERT OR IGNORE`` is deliberate: if two concurrent requests
        with the same key race past the lookup (shouldn't happen under
        the daemon's write lock, but defensive), the first write wins
        and the second is a no-op — both callers already computed the
        same response, so the cache row is the same either way.
        """
        self._conn.execute(
            """
            INSERT OR IGNORE INTO idempotency_keys (
                key, endpoint, request_hash, status_code, response_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?);
            """,
            (key, endpoint, request_hash, status_code, response_json, created_at),
        )
