"""Registry — SQLite index over canonical Forest Soul Forge artifacts.

See :mod:`forest_soul_forge.registry` package docstring and
``docs/decisions/ADR-0006-registry-as-index.md`` for the design.

Threading / concurrency: the registry is **single-writer**. The
FastAPI daemon (ADR-0007) serializes writes via ``asyncio.Lock``.
Multiple reader connections against WAL-mode SQLite are fine. This
module does not add its own locking — it would be a false reassurance.

R4 architecture (post-split):
  - This file owns connection lifecycle (``bootstrap``, schema
    install/verify/migrate, ``close``) and composes per-table
    accessors as ``self.agents``, ``self.idempotency``,
    ``self.tool_counters``, ``self.approvals``, ``self.secrets``.
  - Every method that existed on the pre-R4 Registry stays here as a
    one-line back-compat delegate to the appropriate accessor. New
    code should prefer ``registry.agents.register_birth(...)`` over
    ``registry.register_birth(...)`` so the call site documents which
    table is being touched, but BOTH continue to work — every router
    in the codebase calls the flat methods, and changing those en
    masse was explicitly out of scope for R4.
  - All error classes (RegistryError, SchemaMismatchError,
    UnknownAgentError, DuplicateInstanceError, IdempotencyMismatchError)
    are RE-EXPORTED here so existing
    ``from forest_soul_forge.registry.registry import UnknownAgentError``
    callers keep working. Their definitions live in
    :mod:`registry._errors`.
  - Result dataclasses (AgentRow, AuditRow, RebuildReport) are re-exported
    from :mod:`registry.tables.agents`.
"""
from __future__ import annotations

import binascii as _binascii
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

from forest_soul_forge.registry import schema
from forest_soul_forge.registry.ingest import (
    ParsedAuditEntry,
    ParsedSoul,
)
from forest_soul_forge.registry._errors import (
    DuplicateInstanceError,
    IdempotencyMismatchError,
    RegistryError,
    SchemaMismatchError,
    UnknownAgentError,
)


class RegistryEncryptionError(RegistryError):
    """Raised when SQLCipher setup fails — ADR-0050 T2.

    Possible causes:
      - ``sqlcipher3-binary`` not installed (operator enabled
        FSF_AT_REST_ENCRYPTION but didn't install the daemon
        extras)
      - SQLCipher build linked against vanilla SQLite (PRAGMA
        cipher_version returns empty)
      - Master key doesn't match the one the DB was created with
        (operator rotated the key without re-keying the DB)
      - Existing DB is plaintext and operator enabled encryption
        without running the migration tool (T8)

    The daemon's lifespan catches this and produces a startup_
    diagnostics entry; the operator decides whether to fall back
    to plaintext (turn off the env var) or migrate the DB.
    """
from forest_soul_forge.registry.tables import (
    AgentRow,
    AgentsTable,
    ApprovalsTable,
    ConversationsTable,
    AuditRow,
    IdempotencyTable,
    RebuildReport,
    SecretsTable,
    ToolCountersTable,
)
from forest_soul_forge.registry.tables.plugin_grants import (
    PluginGrantsTable,
)
from forest_soul_forge.registry.tables.catalog_grants import (
    CatalogGrantsTable,
)
from forest_soul_forge.registry.tables.reality_anchor_corrections import (
    RealityAnchorCorrectionsTable,
)
from forest_soul_forge.registry.tables._helpers import transaction as _transaction

REGISTRY_SCHEMA_VERSION: int = schema.SCHEMA_VERSION


__all__ = [
    # Errors (re-exported from _errors)
    "RegistryError",
    "SchemaMismatchError",
    "UnknownAgentError",
    "DuplicateInstanceError",
    "IdempotencyMismatchError",
    # Result dataclasses (re-exported from tables.agents)
    "AgentRow",
    "AuditRow",
    "RebuildReport",
    # Façade
    "Registry",
    "REGISTRY_SCHEMA_VERSION",
]


# ---------------------------------------------------------------------------
# B143: per-thread SQLite connection proxy
# ---------------------------------------------------------------------------
class _ThreadLocalConn:
    """Per-thread sqlite3.Connection proxy. Fixes SQLITE_MISUSE under
    concurrent reads (B143, surfaced live 2026-05-05).

    Why: Python's sqlite3 module reports ``threadsafety=1``, meaning
    connections cannot be shared across threads at the DB-API level
    even with ``check_same_thread=False`` (which only disables
    Python's own safety check, not the DB-API contract). Sharing
    one connection across the FastAPI threadpool produces
    ``sqlite3.InterfaceError: bad parameter or other API misuse``
    under concurrent access — confirmed live 2026-05-05 in the chat
    tab's ``GET /conversations/{id}/turns`` path while scheduled
    tasks were dispatching concurrently. Bug surfaces as a 422 to
    the chat client because subsequent reads on the corrupted
    connection return all-None rows that fail Pydantic validation
    (``ConversationOut`` literal-type checks).

    What: implement just the bits of ``sqlite3.Connection`` Forest's
    table accessors actually use (execute / executemany /
    executescript / cursor / commit / rollback / close) and dispatch
    each to a thread-local underlying connection. Each thread gets
    its own real ``sqlite3.Connection`` on first access; WAL mode on
    the file lets multiple connections coexist safely (which is what
    the registry.py docstring already promised — "Multiple reader
    connections against WAL-mode SQLite are fine").

    Lifecycle: connections are opened lazily per thread. ``close()``
    only closes the *current* thread's connection — sibling threads'
    connections leak until process exit, which is acceptable for the
    daemon's one-process-per-host model. If you need to close ALL
    thread connections (e.g., shutting down the daemon cleanly),
    iterate ``_get_all_conns()`` (test-only helper).

    Transaction safety: ``BEGIN``/``COMMIT``/``ROLLBACK`` (via the
    ``transaction()`` context manager in tables/_helpers.py) all run
    on the same calling thread, which means they all hit the same
    per-thread connection. Cross-thread transactions are not
    supported and were never used in Forest's design.
    """

    def __init__(
        self,
        db_path: str,
        pragmas: tuple[str, ...],
        *,
        master_key: bytes | None = None,
    ) -> None:
        self._db_path = db_path
        self._pragmas = pragmas
        # ADR-0050 T2 (B267): when set, connections open via sqlcipher3
        # and run ``PRAGMA key`` BEFORE any other statement so the file
        # decrypts cleanly. None = legacy stdlib sqlite3 path
        # (bit-identical pre-T2 behavior).
        self._master_key = master_key
        self._local = threading.local()
        # All-thread tracker for diagnostics + close(). NOT used for
        # serialization — that's the point of this class.
        self._all_conns: list[sqlite3.Connection] = []
        self._all_conns_lock = threading.Lock()

    def _get(self) -> sqlite3.Connection:
        """Return this thread's connection, opening one if needed.

        ADR-0050 T2 (B267): when ``self._master_key`` is set, opens
        the connection via ``sqlcipher3.dbapi2`` instead of stdlib
        ``sqlite3``, and runs ``PRAGMA key = "x'<hex>'"`` as the FIRST
        statement on the connection. SQLCipher requires the key be
        established before any other statement; PRAGMA key on a
        non-keyed connection (or on a plaintext DB with the wrong key)
        leaves the connection unusable, and the first real read
        raises ``SQLCipherError: file is not a database``. We surface
        that as ``RegistryEncryptionError`` so the daemon-level
        lifespan can produce a clean diagnostic.
        """
        c = getattr(self._local, "conn", None)
        if c is None:
            if self._master_key is not None:
                # Lazy import — sqlcipher3 is in the ``daemon`` extras,
                # not the base deps. Test envs and lightweight
                # operators that don't enable at-rest encryption never
                # need it installed.
                try:
                    import sqlcipher3.dbapi2 as _sqlcipher
                except ImportError as e:
                    raise RegistryEncryptionError(
                        "FSF_AT_REST_ENCRYPTION enabled but sqlcipher3 "
                        "is not installed. Install via: "
                        "pip install -e '.[daemon]' (or "
                        "pip install sqlcipher3-binary directly)."
                    ) from e
                c = _sqlcipher.connect(
                    self._db_path,
                    isolation_level=None,
                    check_same_thread=False,
                )
                # ``hexlify`` constrains the output to [0-9a-f] so the
                # quoted-hex literal has zero injection surface despite
                # the f-string. ``master_key`` is already validated to
                # be exactly 32 bytes by security.master_key.
                hex_key = _binascii.hexlify(self._master_key).decode("ascii")
                try:
                    c.execute(f'PRAGMA key = "x\'{hex_key}\'"')
                    # Probe — SQLCipher delays validation until first
                    # read; querying cipher_version forces the key
                    # check + surfaces a clean error if the file's
                    # plaintext or the key's wrong.
                    cipher_version = c.execute(
                        "PRAGMA cipher_version;"
                    ).fetchone()
                    if not cipher_version or not cipher_version[0]:
                        raise RegistryEncryptionError(
                            "sqlcipher3 connected but PRAGMA "
                            "cipher_version returned empty — likely "
                            "linked against a non-SQLCipher build of "
                            "SQLite."
                        )
                except Exception as e:
                    try:
                        c.close()
                    except Exception:  # noqa: BLE001
                        pass
                    if isinstance(e, RegistryEncryptionError):
                        raise
                    raise RegistryEncryptionError(
                        "could not key the SQLCipher connection — DB "
                        "may be plaintext (legacy) or the master key "
                        "doesn't match the one the DB was created with."
                    ) from e
            else:
                c = sqlite3.connect(
                    self._db_path,
                    isolation_level=None,
                    check_same_thread=False,
                )
            c.row_factory = sqlite3.Row
            for p in self._pragmas:
                c.execute(p)
            self._local.conn = c
            with self._all_conns_lock:
                self._all_conns.append(c)
        return c

    # ---- proxied sqlite3.Connection surface ----------------------------
    def execute(self, *args, **kwargs):
        return self._get().execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        return self._get().executemany(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        return self._get().executescript(*args, **kwargs)

    def cursor(self, *args, **kwargs):
        return self._get().cursor(*args, **kwargs)

    def commit(self) -> None:
        return self._get().commit()

    def rollback(self) -> None:
        return self._get().rollback()

    def close(self) -> None:
        """Close the *current* thread's connection. Sibling threads'
        connections leak until process exit (acceptable for daemon's
        one-process-per-host model)."""
        c = getattr(self._local, "conn", None)
        if c is not None:
            try:
                c.close()
            finally:
                del self._local.conn
                with self._all_conns_lock:
                    try:
                        self._all_conns.remove(c)
                    except ValueError:
                        pass

    def _close_all(self) -> None:
        """Test-only: close every thread's connection. Don't use in
        production code paths — connection lifecycles are per-thread."""
        with self._all_conns_lock:
            for c in self._all_conns:
                try:
                    c.close()
                except Exception:  # noqa: BLE001
                    pass
            self._all_conns.clear()


class Registry:
    """Thin façade over per-table accessors.

    Lifecycle: construct with :meth:`bootstrap`, do work, call
    :meth:`close` (or use it as a context manager).

    Threading: the underlying connection is opened with
    ``check_same_thread=False`` so it can be shared across the threads
    FastAPI dispatches sync route handlers onto, and between the
    lifespan thread (where ``bootstrap`` runs) and the threadpool.
    Read safety is provided by WAL mode (PRAGMA journal_mode=WAL, set
    at bootstrap); write safety is the caller's problem. In the
    daemon, writes are serialized through ``app.state.write_lock``
    (ADR-0007). Do not issue concurrent writes against a single
    Registry instance without an external lock.

    Per-table accessors (added in R4): ``self.agents``,
    ``self.idempotency``, ``self.tool_counters``, ``self.approvals``,
    ``self.secrets``. New code should prefer these over the flat
    delegate methods because the call site then documents WHICH table
    is being touched.
    """

    def __init__(self, db_path: Path, conn: sqlite3.Connection) -> None:
        self._db_path = db_path
        self._conn = conn
        # Per-table accessors — all share the same connection. This is
        # safe because the daemon serializes writes via its own lock;
        # see class docstring.
        self.agents = AgentsTable(conn)
        self.idempotency = IdempotencyTable(conn)
        self.tool_counters = ToolCountersTable(conn)
        self.approvals = ApprovalsTable(conn)
        self.secrets = SecretsTable(conn)
        # ADR-003Y Y1: conversation runtime substrate.
        self.conversations = ConversationsTable(conn)
        # ADR-0043 follow-up #2 (Burst 113): post-birth plugin grants.
        # Augments constitution.allowed_mcp_servers without mutating
        # constitution_hash. The dispatcher unions grants() with the
        # constitution-declared list before mcp_call.v1's allowlist
        # check runs.
        self.plugin_grants = PluginGrantsTable(conn)
        # ADR-0060 T1 (Burst 219): post-birth catalog-tool grants.
        # Sister of plugin_grants — augments the constitution's allowed
        # tool list without mutating constitution_hash. The dispatcher
        # (T2, queued) consults this on constitution-check miss.
        self.catalog_grants = CatalogGrantsTable(conn)
        # ADR-0063 T6 (Burst 255): Reality Anchor correction memory.
        # One row per unique hallucinated claim ever caught. The
        # dispatcher gate (T3) + conversation hook (T5) both bump
        # this on contradicted findings and emit
        # reality_anchor_repeat_offender once a claim crosses 2.
        self.reality_anchor_corrections = RealityAnchorCorrectionsTable(conn)

    # ============ construction / teardown ===================================
    @classmethod
    def bootstrap(
        cls,
        db_path: Path,
        *,
        master_key: bytes | None = None,
    ) -> "Registry":
        """Open (or create) a registry DB at ``db_path``.

        - If the file is new: apply full schema, insert initial metadata.
        - If the file exists and matches our schema version: no-op.
        - If the file exists at an older schema version: apply the
          registered forward migrations in order (see
          ``schema.MIGRATIONS``) until it matches.
        - If the file exists at a newer schema version: raise
          :class:`SchemaMismatchError`. Refusing to downgrade is safer
          than silently dropping columns the old code doesn't know about.
        - If a forward migration is missing for the gap we're crossing,
          raise :class:`SchemaMismatchError` rather than skip silently.

        ADR-0050 T2 (B267): when ``master_key`` is set, every per-thread
        SQLite connection opens via ``sqlcipher3.dbapi2`` and is keyed
        with the 32-byte master key before any other statement. This
        encrypts the DB file (including indices, journals, WAL) using
        SQLCipher's AES-256-CBC page-level cipher. New DBs are
        encrypted from creation; existing plaintext DBs cannot be
        opened with a key (T8 ships a migration tool). When
        ``master_key`` is None (default), behavior is bit-identical
        to pre-T2 — stdlib ``sqlite3`` + plaintext file.
        """
        db_path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not db_path.exists()
        # B143 (2026-05-05): per-thread connection proxy. Replaces a
        # single shared connection that worked at the SQLite C level
        # (check_same_thread=False + WAL) but violated Python's
        # sqlite3 DB-API contract (threadsafety=1 → connections may
        # not be shared across threads). The bug manifested as
        # SQLITE_MISUSE on chat reads under concurrent scheduled-task
        # dispatches.
        #
        # Each thread that calls into the registry gets its own real
        # sqlite3.Connection lazily on first execute. WAL mode on the
        # file (set via CONNECTION_PRAGMAS) lets the connections
        # coexist safely — which is what this module's docstring
        # always promised: "Multiple reader connections against
        # WAL-mode SQLite are fine."
        #
        # The schema install/verify below runs on THIS (lifespan)
        # thread, using its per-thread connection. FastAPI worker
        # threads each get their own connection on first request.
        conn = _ThreadLocalConn(
            str(db_path),
            schema.CONNECTION_PRAGMAS,
            master_key=master_key,
        )

        if is_new:
            cls._install_schema(conn)
        else:
            cls._verify_schema_version(conn)

        return cls(db_path, conn)

    @staticmethod
    def _install_schema(conn: sqlite3.Connection) -> None:
        with _transaction(conn):
            for stmt in schema.DDL_STATEMENTS:
                conn.execute(stmt)
            for key, value in schema.INITIAL_METADATA:
                conn.execute(
                    "INSERT OR REPLACE INTO registry_meta (key, value) VALUES (?, ?);",
                    (key, value),
                )

    @staticmethod
    def _verify_schema_version(conn: sqlite3.Connection) -> None:
        # If the table doesn't exist we treat it as new-shaped: install
        # schema. This covers the case of an empty file created by a
        # crashed bootstrap.
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='registry_meta';"
        ).fetchone()
        if row is None:
            Registry._install_schema(conn)
            return
        row = conn.execute(
            "SELECT value FROM registry_meta WHERE key='schema_version';"
        ).fetchone()
        if row is None:
            raise SchemaMismatchError(
                "registry_meta exists but has no schema_version row"
            )
        v = int(row["value"])
        if v == schema.SCHEMA_VERSION:
            return
        if v > schema.SCHEMA_VERSION:
            # Downgrade: the DB was last touched by newer code than is
            # running now. Refusing is safer than silently dropping
            # tables or columns the old code doesn't know about.
            raise SchemaMismatchError(
                f"registry schema version is newer than this code: "
                f"file={v} code={schema.SCHEMA_VERSION}. "
                f"Update the code or rebuild the registry from artifacts."
            )
        # Forward migration: apply each registered step in order.
        Registry._migrate_forward(conn, v, schema.SCHEMA_VERSION)

    @staticmethod
    def _migrate_forward(
        conn: sqlite3.Connection, from_v: int, to_v: int
    ) -> None:
        """Walk the version gap and apply each registered migration.

        One transaction per version step. A partial failure rolls back
        that step, leaving the DB at the previous version — which means
        retrying a failed migration is safe (the whole step re-runs).

        A gap with no registered migration is a hard error: we refuse
        to jump a version silently because that risks masking a
        breaking change someone forgot to flag.
        """
        for target in range(from_v + 1, to_v + 1):
            steps = schema.MIGRATIONS.get(target)
            if steps is None:
                raise SchemaMismatchError(
                    f"no forward migration registered for schema version "
                    f"{target}. Either add schema.MIGRATIONS[{target}] or "
                    f"rebuild the registry from artifacts."
                )
            with _transaction(conn):
                for stmt in steps:
                    conn.execute(stmt)
                conn.execute(
                    "UPDATE registry_meta SET value=? WHERE key='schema_version';",
                    (str(target),),
                )

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> "Registry":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ============ introspection =============================================
    @property
    def db_path(self) -> Path:
        return self._db_path

    def schema_version(self) -> int:
        row = self._conn.execute(
            "SELECT value FROM registry_meta WHERE key='schema_version';"
        ).fetchone()
        return int(row["value"])

    # ========================================================================
    # Back-compat delegates — every public method that existed on the
    # pre-R4 Registry continues to work. New code should prefer the
    # accessor (``registry.agents.X``, ``registry.idempotency.X``, etc.)
    # so the call site documents which table is being touched, but
    # routers and tests written against the flat surface keep running.
    # ========================================================================

    # ---- agents / ancestry / audit_events mirror ----
    def register_birth(
        self,
        soul: ParsedSoul,
        *,
        audit_entry: Optional[ParsedAuditEntry] = None,
        instance_id: Optional[str] = None,
        status: str = "active",
        sibling_index: Optional[int] = None,
    ) -> str:
        return self.agents.register_birth(
            soul,
            audit_entry=audit_entry,
            instance_id=instance_id,
            status=status,
            sibling_index=sibling_index,
        )

    def next_sibling_index(self, dna: str) -> int:
        return self.agents.next_sibling_index(dna)

    def register_audit_event(
        self,
        entry: ParsedAuditEntry,
        *,
        instance_id: Optional[str] = None,
    ) -> None:
        self.agents.register_audit_event(entry, instance_id=instance_id)

    def update_status(self, instance_id: str, status: str) -> None:
        self.agents.update_status(instance_id, status)

    def rebuild_from_artifacts(
        self, artifacts_dir: Path, audit_chain_path: Path
    ) -> RebuildReport:
        return self.agents.rebuild_from_artifacts(artifacts_dir, audit_chain_path)

    def list_agents(
        self, *, role: str | None = None, status: str | None = None
    ) -> list[AgentRow]:
        return self.agents.list_agents(role=role, status=status)

    def get_agent(self, instance_id: str) -> AgentRow:
        return self.agents.get_agent(instance_id)

    def get_agent_by_dna(self, dna: str) -> list[AgentRow]:
        return self.agents.get_agent_by_dna(dna)

    def get_ancestors(self, instance_id: str) -> list[AgentRow]:
        return self.agents.get_ancestors(instance_id)

    def get_descendants(self, instance_id: str) -> list[AgentRow]:
        return self.agents.get_descendants(instance_id)

    def audit_tail(self, n: int = 100) -> list[AuditRow]:
        return self.agents.audit_tail(n)

    def audit_for_agent(
        self, *, dna: str | None = None, instance_id: str | None = None
    ) -> list[AuditRow]:
        return self.agents.audit_for_agent(dna=dna, instance_id=instance_id)

    # ---- idempotency cache ----
    def lookup_idempotency_key(
        self, key: str, endpoint: str, request_hash: str
    ) -> tuple[int, str] | None:
        return self.idempotency.lookup_idempotency_key(key, endpoint, request_hash)

    def store_idempotency_key(
        self,
        key: str,
        endpoint: str,
        request_hash: str,
        status_code: int,
        response_json: str,
        created_at: str,
    ) -> None:
        self.idempotency.store_idempotency_key(
            key, endpoint, request_hash, status_code, response_json, created_at
        )

    # ---- tool counters + per-call accounting ----
    def get_tool_call_count(self, instance_id: str, session_id: str) -> int:
        return self.tool_counters.get_tool_call_count(instance_id, session_id)

    def increment_tool_call_count(
        self, instance_id: str, session_id: str, when_iso: str
    ) -> int:
        return self.tool_counters.increment_tool_call_count(
            instance_id, session_id, when_iso
        )

    def record_tool_call(
        self,
        *,
        audit_seq: int,
        instance_id: str,
        session_id: str,
        tool_key: str,
        status: str,
        tokens_used: int | None,
        cost_usd: float | None,
        side_effect_summary: str | None,
        finished_at: str,
    ) -> None:
        self.tool_counters.record_tool_call(
            audit_seq=audit_seq,
            instance_id=instance_id,
            session_id=session_id,
            tool_key=tool_key,
            status=status,
            tokens_used=tokens_used,
            cost_usd=cost_usd,
            side_effect_summary=side_effect_summary,
            finished_at=finished_at,
        )

    def aggregate_tool_calls(self, instance_id: str) -> dict[str, Any]:
        return self.tool_counters.aggregate_tool_calls(instance_id)

    # ---- approvals queue ----
    def record_pending_approval(
        self,
        *,
        ticket_id: str,
        instance_id: str,
        session_id: str,
        tool_key: str,
        args_json: str,
        side_effects: str,
        pending_audit_seq: int,
        created_at: str,
    ) -> None:
        self.approvals.record_pending_approval(
            ticket_id=ticket_id,
            instance_id=instance_id,
            session_id=session_id,
            tool_key=tool_key,
            args_json=args_json,
            side_effects=side_effects,
            pending_audit_seq=pending_audit_seq,
            created_at=created_at,
        )

    def get_pending_approval(self, ticket_id: str) -> dict[str, Any] | None:
        return self.approvals.get_pending_approval(ticket_id)

    def list_pending_approvals(
        self, instance_id: str, *, status: str | None = "pending"
    ) -> list[dict[str, Any]]:
        return self.approvals.list_pending_approvals(instance_id, status=status)

    def mark_approval_decided(
        self,
        ticket_id: str,
        *,
        status: str,
        decided_audit_seq: int,
        decided_by: str,
        decision_reason: str | None,
        decided_at: str,
    ) -> bool:
        return self.approvals.mark_approval_decided(
            ticket_id,
            status=status,
            decided_audit_seq=decided_audit_seq,
            decided_by=decided_by,
            decision_reason=decision_reason,
            decided_at=decided_at,
        )

    # ---- secrets ----
    def set_secret(
        self,
        instance_id: str,
        name: str,
        plaintext: str,
        *,
        master_key,
        when: str | None = None,
    ) -> None:
        self.secrets.set_secret(
            instance_id, name, plaintext, master_key=master_key, when=when
        )

    def get_secret(
        self,
        instance_id: str,
        name: str,
        *,
        master_key,
        when: str | None = None,
    ) -> str:
        return self.secrets.get_secret(
            instance_id, name, master_key=master_key, when=when
        )

    def list_secret_names(self, instance_id: str) -> list[str]:
        return self.secrets.list_secret_names(instance_id)

    def delete_secret(self, instance_id: str, name: str) -> bool:
        return self.secrets.delete_secret(instance_id, name)
