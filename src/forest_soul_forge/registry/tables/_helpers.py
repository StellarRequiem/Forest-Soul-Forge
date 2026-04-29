"""Shared connection-level helpers for the registry table accessors.

R4 split this out of registry.py so each per-table accessor can ``from
.._helpers import _transaction`` without a circular import back through
the Registry façade.

Nothing here is part of the public API — leading underscore, internal
to the registry package.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Explicit BEGIN/COMMIT around a multi-statement operation.

    The Registry opens its connection with ``isolation_level=None``,
    which in sqlite3 terms means *autocommit mode*: every individual
    statement is its own transaction unless we explicitly open one.
    Single-statement updates don't need this wrapper. Anything that
    has to read-then-write atomically (counter increment, secret
    encrypt-then-store, agent insert + ancestry insert + audit insert)
    does. This was named ``_transaction`` in the pre-R4 monolith;
    the single-underscore prefix is dropped here because it's already
    inside an underscored module.
    """
    conn.execute("BEGIN;")
    try:
        yield
    except Exception:
        conn.execute("ROLLBACK;")
        raise
    else:
        conn.execute("COMMIT;")


def utc_now_iso() -> str:
    """Fixed-width UTC ISO-8601 timestamp.

    Same format as core/audit_chain.py + core/memory.py — keep them
    aligned so timestamps sort lexicographically across subsystems.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
