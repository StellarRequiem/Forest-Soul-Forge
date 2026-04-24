"""Registry — SQLite index over canonical Forest Soul Forge artifacts.

See :mod:`forest_soul_forge.registry` package docstring and
``docs/decisions/ADR-0006-registry-as-index.md`` for the design.

Threading / concurrency: the registry is **single-writer**. The FastAPI daemon
(ADR-0007) serializes writes via ``asyncio.Lock``. Multiple reader connections
against WAL-mode SQLite are fine. This module does not add its own locking —
it would be a false reassurance.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

from forest_soul_forge.registry import ingest, schema
from forest_soul_forge.registry.ingest import (
    IngestError,
    ParsedAuditEntry,
    ParsedSoul,
)

REGISTRY_SCHEMA_VERSION: int = schema.SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class RegistryError(Exception):
    """Base class for registry failures."""


class SchemaMismatchError(RegistryError):
    """Raised when an existing DB file's schema_version doesn't match ours."""


class UnknownAgentError(RegistryError):
    pass


class DuplicateInstanceError(RegistryError):
    pass


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AgentRow:
    instance_id: str
    dna: str
    dna_full: str
    role: str
    agent_name: str
    parent_instance: str | None
    owner_id: str | None
    model_name: str | None
    model_version: str | None
    soul_path: str
    constitution_path: str
    constitution_hash: str
    created_at: str
    status: str
    legacy_minted: bool


@dataclass(frozen=True)
class AuditRow:
    seq: int
    timestamp: str
    agent_dna: str | None
    instance_id: str | None
    event_type: str
    event_json: str
    entry_hash: str


@dataclass(frozen=True)
class RebuildReport:
    """Returned by ``rebuild_from_artifacts`` for operator visibility."""

    agents_loaded: int
    ancestry_edges: int
    audit_events: int
    legacy_instance_ids_minted: int
    orphaned_parent_refs: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
class Registry:
    """Thin wrapper around a SQLite connection, specialized for FSF artifacts.

    Lifecycle: construct with :meth:`bootstrap`, do work, call :meth:`close`
    (or use it as a context manager).

    Threading: the underlying connection is opened with
    ``check_same_thread=False`` so it can be shared across the threads
    FastAPI dispatches sync route handlers onto, and between the lifespan
    thread (where ``bootstrap`` runs) and the threadpool. Read safety is
    provided by WAL mode (PRAGMA journal_mode=WAL, set at bootstrap);
    write safety is the caller's problem. In the daemon, writes are
    serialized through ``app.state.write_lock`` (ADR-0007). Do not issue
    concurrent writes against a single Registry instance without an
    external lock.
    """

    def __init__(self, db_path: Path, conn: sqlite3.Connection) -> None:
        self._db_path = db_path
        self._conn = conn

    # -------- construction / teardown ------------------------------------
    @classmethod
    def bootstrap(cls, db_path: Path) -> "Registry":
        """Open (or create) a registry DB at ``db_path``.

        - If the file is new: apply full schema, insert initial metadata.
        - If the file exists and matches our schema version: no-op.
        - If the file exists at a different schema version: raise
          :class:`SchemaMismatchError`. Caller's choice to rebuild.
        """
        db_path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not db_path.exists()
        # check_same_thread=False is required because FastAPI dispatches
        # sync route handlers onto a threadpool — the connection opened
        # here (on the lifespan thread) will be used from handler threads.
        # Concurrent write safety is the caller's job (see class docstring).
        conn = sqlite3.connect(
            str(db_path), isolation_level=None, check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        for pragma in schema.CONNECTION_PRAGMAS:
            conn.execute(pragma)

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
        # schema. This covers the case of an empty file created by a crashed
        # bootstrap.
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
        if v != schema.SCHEMA_VERSION:
            raise SchemaMismatchError(
                f"registry schema version mismatch: file={v} code={schema.SCHEMA_VERSION}"
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

    # -------- introspection ----------------------------------------------
    @property
    def db_path(self) -> Path:
        return self._db_path

    def schema_version(self) -> int:
        row = self._conn.execute(
            "SELECT value FROM registry_meta WHERE key='schema_version';"
        ).fetchone()
        return int(row["value"])

    # -------- write path: single-birth ingest ----------------------------
    def register_birth(
        self,
        soul: ParsedSoul,
        *,
        audit_entry: Optional[ParsedAuditEntry] = None,
        instance_id: Optional[str] = None,
        status: str = "active",
    ) -> str:
        """Register an agent from a parsed soul artifact.

        The caller is expected to have **already** written the soul +
        constitution files and appended the audit-chain entry. This method
        only mirrors the result into the registry. That ordering (ADR-0006
        sync path) is what makes rebuild-from-artifacts coherent.

        Returns the instance_id used (newly minted UUID v4 if not supplied and
        not present on the soul).
        """
        resolved_instance = (
            instance_id
            or soul.instance_id
            or str(uuid.uuid4())
        )
        legacy = bool(instance_id is None and soul.instance_id is None and soul.lineage_depth == 0 and False)
        # `legacy` above is intentionally False for register_birth — this is
        # a live birth, not a rebuild. Rebuild path sets legacy_minted=1
        # explicitly when synthesizing.

        with _transaction(self._conn):
            self._insert_agent_row(
                soul,
                instance_id=resolved_instance,
                parent_instance=soul.parent_instance,
                status=status,
                legacy_minted=False,
            )
            self._insert_ancestry_for(resolved_instance, soul.parent_instance)
            if audit_entry is not None:
                self._insert_audit_row(audit_entry, instance_id=resolved_instance)

        return resolved_instance

    def register_audit_event(
        self,
        entry: ParsedAuditEntry,
        *,
        instance_id: Optional[str] = None,
    ) -> None:
        """Mirror a single audit entry into the registry.

        Idempotent on seq: ``INSERT OR IGNORE`` so replaying the same tail
        doesn't double-insert. A real mismatch on entry_hash for the same seq
        is an integrity signal and raises.
        """
        existing = self._conn.execute(
            "SELECT entry_hash FROM audit_events WHERE seq=?;", (entry.seq,)
        ).fetchone()
        if existing is not None:
            if existing["entry_hash"] != entry.entry_hash:
                raise RegistryError(
                    f"audit seq {entry.seq}: entry_hash mismatch — DB has "
                    f"{existing['entry_hash']!r}, got {entry.entry_hash!r}"
                )
            return  # Already mirrored, consistent. No-op.
        with _transaction(self._conn):
            self._insert_audit_row(entry, instance_id=instance_id)

    def update_status(self, instance_id: str, status: str) -> None:
        """Update an agent's status (active | archived | suspended).

        Note: per ADR-0006 open question, status changes are registry-only in
        v1 — not audit events. Upgrade to auditable when a status_changed
        event type is added to the chain.
        """
        with _transaction(self._conn):
            cur = self._conn.execute(
                "UPDATE agents SET status=? WHERE instance_id=?;",
                (status, instance_id),
            )
            if cur.rowcount == 0:
                raise UnknownAgentError(instance_id)

    # -------- rebuild path -----------------------------------------------
    def rebuild_from_artifacts(
        self,
        artifacts_dir: Path,
        audit_chain_path: Path,
    ) -> RebuildReport:
        """Drop and repopulate every table from the canonical artifacts.

        Single transaction so a partial rebuild never leaves the DB in an
        inconsistent state. Returns a report for operator visibility.
        """
        souls = [ingest.parse_soul_file(p) for p in ingest.iter_soul_files(artifacts_dir)]

        # Resolve instance_id per soul — prefer explicit, else deterministic
        # legacy mint. When minting, include the soul path in the key so two
        # souls with the same trait profile and timestamp (valid case: a
        # role default and a lineage root of the same role) get distinct IDs.
        legacy_minted_count = 0
        assigned: list[tuple[ParsedSoul, str, bool]] = []
        for s in souls:
            if s.instance_id is not None:
                inst = s.instance_id
                is_legacy = False
            else:
                try:
                    rel = s.soul_path.resolve().relative_to(artifacts_dir.resolve())
                    rel_str = str(rel)
                except ValueError:
                    rel_str = s.soul_path.name
                inst = ingest.synthesize_legacy_instance_id(
                    s.dna_full, s.created_at, rel_str
                )
                is_legacy = True
                legacy_minted_count += 1
            assigned.append((s, inst, is_legacy))

        # Sort by lineage_depth so parents land before children. Same depth is
        # fine in any order because self-edges are always depth 0.
        assigned.sort(key=lambda tup: (tup[0].lineage_depth, tup[0].created_at))

        orphans: list[str] = []

        with _transaction(self._conn):
            # Truncate in FK-safe order.
            for table in schema.REBUILD_TRUNCATE_ORDER:
                self._conn.execute(f"DELETE FROM {table};")

            ancestry_edges = 0
            for soul_rec, inst, is_legacy in assigned:
                parent_inst = _resolve_parent_instance(soul_rec, assigned)
                if soul_rec.parent_dna and parent_inst is None:
                    orphans.append(f"{inst} (parent_dna={soul_rec.parent_dna})")
                self._insert_agent_row(
                    soul_rec,
                    instance_id=inst,
                    parent_instance=parent_inst,
                    status="active",
                    legacy_minted=is_legacy,
                )
                ancestry_edges += self._insert_ancestry_for(inst, parent_inst)

            # Audit events are mirrored in file order. ``instance_id`` is
            # resolved by DNA when unambiguous; if multiple agents share the
            # short DNA, leave instance_id NULL (operator can still look up
            # by ``agent_dna``).
            short_to_insts: dict[str, list[str]] = {}
            for s, inst, _ in assigned:
                short_to_insts.setdefault(s.dna, []).append(inst)

            audit_count = 0
            for e in ingest.iter_audit_entries(audit_chain_path):
                inst_hint: str | None = None
                if e.agent_dna:
                    candidates = short_to_insts.get(e.agent_dna)
                    if candidates and len(candidates) == 1:
                        inst_hint = candidates[0]
                self._insert_audit_row(e, instance_id=inst_hint)
                audit_count += 1

        return RebuildReport(
            agents_loaded=len(assigned),
            ancestry_edges=ancestry_edges,
            audit_events=audit_count,
            legacy_instance_ids_minted=legacy_minted_count,
            orphaned_parent_refs=tuple(orphans),
        )

    # -------- read path ---------------------------------------------------
    def list_agents(
        self,
        *,
        role: str | None = None,
        status: str | None = None,
    ) -> list[AgentRow]:
        clauses: list[str] = []
        params: list[Any] = []
        if role is not None:
            clauses.append("role = ?")
            params.append(role)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM agents{where} ORDER BY created_at;", params
        ).fetchall()
        return [_row_to_agent(r) for r in rows]

    def get_agent(self, instance_id: str) -> AgentRow:
        row = self._conn.execute(
            "SELECT * FROM agents WHERE instance_id = ?;", (instance_id,)
        ).fetchone()
        if row is None:
            raise UnknownAgentError(instance_id)
        return _row_to_agent(row)

    def get_agent_by_dna(self, dna: str) -> list[AgentRow]:
        """All incarnations matching the given (short or full) DNA.

        Multiple rows are legitimate: same trait profile can be birthed more
        than once. Returned in creation order.
        """
        col = "dna_full" if len(dna) > 12 else "dna"
        rows = self._conn.execute(
            f"SELECT * FROM agents WHERE {col}=? ORDER BY created_at;", (dna,)
        ).fetchall()
        return [_row_to_agent(r) for r in rows]

    def get_ancestors(self, instance_id: str) -> list[AgentRow]:
        """Ancestors (excluding self) ordered from parent outward."""
        rows = self._conn.execute(
            """
            SELECT a.* FROM agents a
            JOIN agent_ancestry anc ON anc.ancestor_id = a.instance_id
            WHERE anc.instance_id = ? AND anc.depth > 0
            ORDER BY anc.depth ASC;
            """,
            (instance_id,),
        ).fetchall()
        return [_row_to_agent(r) for r in rows]

    def get_descendants(self, instance_id: str) -> list[AgentRow]:
        """Descendants (excluding self) ordered by depth then created_at."""
        rows = self._conn.execute(
            """
            SELECT a.*, anc.depth AS _depth FROM agents a
            JOIN agent_ancestry anc ON anc.instance_id = a.instance_id
            WHERE anc.ancestor_id = ? AND anc.depth > 0
            ORDER BY anc.depth ASC, a.created_at ASC;
            """,
            (instance_id,),
        ).fetchall()
        return [_row_to_agent(r) for r in rows]

    def audit_tail(self, n: int = 100) -> list[AuditRow]:
        rows = self._conn.execute(
            "SELECT * FROM audit_events ORDER BY seq DESC LIMIT ?;", (n,)
        ).fetchall()
        return [_row_to_audit(r) for r in rows]

    def audit_for_agent(
        self, *, dna: str | None = None, instance_id: str | None = None
    ) -> list[AuditRow]:
        if dna is None and instance_id is None:
            raise ValueError("audit_for_agent requires dna or instance_id")
        clauses: list[str] = []
        params: list[Any] = []
        if dna is not None:
            clauses.append("agent_dna = ?")
            params.append(dna)
        if instance_id is not None:
            clauses.append("instance_id = ?")
            params.append(instance_id)
        where = " WHERE " + " AND ".join(clauses)
        rows = self._conn.execute(
            f"SELECT * FROM audit_events{where} ORDER BY seq ASC;", params
        ).fetchall()
        return [_row_to_audit(r) for r in rows]

    # -------- internal insert helpers ------------------------------------
    def _insert_agent_row(
        self,
        soul: ParsedSoul,
        *,
        instance_id: str,
        parent_instance: str | None,
        status: str,
        legacy_minted: bool,
    ) -> None:
        try:
            self._conn.execute(
                """
                INSERT INTO agents (
                    instance_id, dna, dna_full, role, agent_name,
                    parent_instance, owner_id, model_name, model_version,
                    soul_path, constitution_path, constitution_hash,
                    created_at, status, legacy_minted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    instance_id,
                    soul.dna,
                    soul.dna_full,
                    soul.role,
                    soul.agent_name,
                    parent_instance,
                    soul.owner_id,
                    soul.model_name,
                    soul.model_version,
                    str(soul.soul_path),
                    str(soul.constitution_path),
                    soul.constitution_hash,
                    soul.created_at,
                    status,
                    1 if legacy_minted else 0,
                ),
            )
        except sqlite3.IntegrityError as e:
            raise DuplicateInstanceError(
                f"insert agent {instance_id}: {e}"
            ) from e

    def _insert_ancestry_for(
        self, instance_id: str, parent_instance: str | None
    ) -> int:
        """Populate closure table rows for this agent.

        Self-edge at depth 0 plus one edge per ancestor reachable through
        ``parent_instance``. Returns the number of rows inserted.
        """
        inserted = 0
        self._conn.execute(
            "INSERT OR IGNORE INTO agent_ancestry (instance_id, ancestor_id, depth) VALUES (?, ?, 0);",
            (instance_id, instance_id),
        )
        inserted += 1
        if parent_instance is None:
            return inserted
        parent_chain = self._conn.execute(
            "SELECT ancestor_id, depth FROM agent_ancestry WHERE instance_id=? ORDER BY depth ASC;",
            (parent_instance,),
        ).fetchall()
        for row in parent_chain:
            self._conn.execute(
                "INSERT OR IGNORE INTO agent_ancestry (instance_id, ancestor_id, depth) VALUES (?, ?, ?);",
                (instance_id, row["ancestor_id"], row["depth"] + 1),
            )
            inserted += 1
        return inserted

    def _insert_audit_row(
        self,
        entry: ParsedAuditEntry,
        *,
        instance_id: str | None,
    ) -> None:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO audit_events (
                seq, timestamp, agent_dna, instance_id,
                event_type, event_json, entry_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (
                entry.seq,
                entry.timestamp,
                entry.agent_dna,
                instance_id,
                entry.event_type,
                entry.event_json,
                entry.entry_hash,
            ),
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
@contextmanager
def _transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Explicit BEGIN/COMMIT. isolation_level=None means autocommit off when
    we open BEGIN ourselves."""
    conn.execute("BEGIN;")
    try:
        yield
    except Exception:
        conn.execute("ROLLBACK;")
        raise
    else:
        conn.execute("COMMIT;")


def _resolve_parent_instance(
    soul: ParsedSoul,
    assigned: list[tuple[ParsedSoul, str, bool]],
) -> str | None:
    """Return parent instance_id if resolvable, else None.

    Order of attempts:
      1. Explicit ``parent_instance`` on the soul (modern births).
      2. ``parent_dna`` → lookup against the scanned souls. If exactly one
         match, use it. If multiple:
         a. If the child carries ``spawned_by``, prefer a candidate whose
            ``agent_name`` equals ``spawned_by``. Resolves the case where two
            agents legitimately share a short DNA and birth timestamp
            (e.g. a role default + a lineage root of the same role).
         b. Otherwise fall back to a temporal tie-break: only consider
            candidates whose ``created_at`` is ≤ the child's, pick the most
            recent. If no temporal match, return None (orphan, reported).
    """
    if soul.parent_instance:
        return soul.parent_instance
    if not soul.parent_dna:
        return None

    matches: list[tuple[ParsedSoul, str]] = [
        (s, inst) for s, inst, _ in assigned if s.dna == soul.parent_dna
    ]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0][1]

    # spawned_by disambiguation — the soul explicitly names its parent agent.
    if soul.spawned_by:
        by_name = [(s, inst) for s, inst in matches if s.agent_name == soul.spawned_by]
        if len(by_name) == 1:
            return by_name[0][1]
        if len(by_name) > 1:
            # Even the name collides — apply temporal tie-break within the
            # name-matched subset.
            matches = by_name

    valid = [(s, inst) for s, inst in matches if s.created_at <= soul.created_at]
    if not valid:
        return None
    valid.sort(key=lambda pair: pair[0].created_at, reverse=True)
    return valid[0][1]


def _row_to_agent(row: sqlite3.Row) -> AgentRow:
    return AgentRow(
        instance_id=row["instance_id"],
        dna=row["dna"],
        dna_full=row["dna_full"],
        role=row["role"],
        agent_name=row["agent_name"],
        parent_instance=row["parent_instance"],
        owner_id=row["owner_id"],
        model_name=row["model_name"],
        model_version=row["model_version"],
        soul_path=row["soul_path"],
        constitution_path=row["constitution_path"],
        constitution_hash=row["constitution_hash"],
        created_at=row["created_at"],
        status=row["status"],
        legacy_minted=bool(row["legacy_minted"]),
    )


def _row_to_audit(row: sqlite3.Row) -> AuditRow:
    return AuditRow(
        seq=row["seq"],
        timestamp=row["timestamp"],
        agent_dna=row["agent_dna"],
        instance_id=row["instance_id"],
        event_type=row["event_type"],
        event_json=row["event_json"],
        entry_hash=row["entry_hash"],
    )
