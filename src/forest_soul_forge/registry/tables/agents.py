"""AgentsTable — accessor for the ``agents`` + ``agent_ancestry`` +
``audit_events`` mirror tables.

These three tables are the agent-lifecycle core: every birth touches
all three (insert agent row → insert ancestry closure rows → mirror
the chain entry). Splitting them across separate accessors would
fragment the register_birth orchestration; keeping them together here
matches how callers reason about the data.

Pre-R4 these methods + the rebuild_from_artifacts orchestration lived
on Registry directly. R4 moved them into this accessor; the Registry
façade still has a back-compat delegate for each public method, so
``registry.register_birth(...)`` and ``registry.agents.register_birth(...)``
both work.

Module-level helpers ``_resolve_parent_instance``, ``_row_to_agent``,
``_row_to_audit`` were also private to registry.py and moved here
because they're only used by AgentsTable.
"""
from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from forest_soul_forge.registry import ingest, schema
from forest_soul_forge.registry.ingest import (
    ParsedAuditEntry,
    ParsedSoul,
)
from forest_soul_forge.registry._errors import (
    DuplicateInstanceError,
    RegistryError,
    UnknownAgentError,
)
from forest_soul_forge.registry.tables._helpers import transaction


# ---------------------------------------------------------------------------
# Result dataclasses (re-exported via registry.registry for back-compat)
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
    sibling_index: int = 1


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
# AgentsTable
# ---------------------------------------------------------------------------
class AgentsTable:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # -------- write path: single-birth ingest ----------------------------
    def register_birth(
        self,
        soul: ParsedSoul,
        *,
        audit_entry: Optional[ParsedAuditEntry] = None,
        instance_id: Optional[str] = None,
        status: str = "active",
        sibling_index: Optional[int] = None,
    ) -> str:
        """Register an agent from a parsed soul artifact.

        The caller is expected to have **already** written the soul +
        constitution files and appended the audit-chain entry. This
        method only mirrors the result into the registry. That ordering
        (ADR-0006 sync path) is what makes rebuild-from-artifacts
        coherent.

        ``sibling_index`` disambiguates twins (same DNA, different
        births). When omitted, falls back to ``soul.sibling_index``
        (defaulting to 1 for legacy souls). Callers on the live write
        path should pass this explicitly — the daemon computes it under
        its write lock via :meth:`next_sibling_index`.

        Returns the instance_id used (newly minted UUID v4 if not
        supplied and not present on the soul).
        """
        resolved_instance = (
            instance_id
            or soul.instance_id
            or str(uuid.uuid4())
        )
        # sibling_index fallback chain: explicit arg → frontmatter → 1.
        # 1 is the right default because most births ARE the first of
        # their DNA line; twins are a minority case. Legacy / pre-v2
        # souls never carry a sibling_index at all and fall through to 1.
        if sibling_index is not None:
            resolved_sibling = sibling_index
        elif soul.sibling_index is not None:
            resolved_sibling = soul.sibling_index
        else:
            resolved_sibling = 1

        with transaction(self._conn):
            self._insert_agent_row(
                soul,
                instance_id=resolved_instance,
                parent_instance=soul.parent_instance,
                status=status,
                legacy_minted=False,
                sibling_index=resolved_sibling,
            )
            self._insert_ancestry_for(resolved_instance, soul.parent_instance)
            if audit_entry is not None:
                self._insert_audit_row(audit_entry, instance_id=resolved_instance)

        return resolved_instance

    def next_sibling_index(self, dna: str) -> int:
        """Return the next sibling slot for this DNA (1-based, stable).

        Twins (two births that land on the same trait profile) share a
        DNA. The sibling_index makes their instance_ids unique and
        human-readable: ``role_abc123abc123``,
        ``role_abc123abc123_2``, ``role_abc123abc123_3``. Slots never
        get reused — archiving the original doesn't free the 1.

        Callers must hold the daemon's write lock when combining this
        with the subsequent insert — otherwise two concurrent births
        with the same DNA would both read the same "next" index.
        Inside the lock, read-then-write is safe.
        """
        # Short form (12-char) is the indexed column. Accept full form
        # by truncating.
        short = dna[:12]
        row = self._conn.execute(
            "SELECT MAX(sibling_index) AS max_idx FROM agents WHERE dna=?;",
            (short,),
        ).fetchone()
        current = row["max_idx"] if row is not None else None
        return (int(current) + 1) if current is not None else 1

    def register_audit_event(
        self,
        entry: ParsedAuditEntry,
        *,
        instance_id: Optional[str] = None,
    ) -> None:
        """Mirror a single audit entry into the registry.

        Idempotent on seq: ``INSERT OR IGNORE`` so replaying the same
        tail doesn't double-insert. A real mismatch on entry_hash for
        the same seq is an integrity signal and raises.
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
        with transaction(self._conn):
            self._insert_audit_row(entry, instance_id=instance_id)

    def update_status(self, instance_id: str, status: str) -> None:
        """Update an agent's status (active | archived | suspended).

        Note: per ADR-0006 open question, status changes are
        registry-only in v1 — not audit events. Upgrade to auditable
        when a status_changed event type is added to the chain.
        """
        with transaction(self._conn):
            cur = self._conn.execute(
                "UPDATE agents SET status=? WHERE instance_id=?;",
                (status, instance_id),
            )
            if cur.rowcount == 0:
                raise UnknownAgentError(instance_id)

    # -------- rebuild path -----------------------------------------------
    def rebuild_from_artifacts(
        self,
        artifacts_dir,
        audit_chain_path,
    ) -> RebuildReport:
        """Drop and repopulate every table from the canonical artifacts.

        Single transaction so a partial rebuild never leaves the DB in
        an inconsistent state. Returns a report for operator visibility.
        """
        souls = [ingest.parse_soul_file(p) for p in ingest.iter_soul_files(artifacts_dir)]

        # Resolve instance_id per soul — prefer explicit, else
        # deterministic legacy mint. When minting, include the soul
        # path in the key so two souls with the same trait profile and
        # timestamp (valid case: a role default and a lineage root of
        # the same role) get distinct IDs.
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

        # Sort by lineage_depth so parents land before children. Same
        # depth is fine in any order because self-edges are always
        # depth 0.
        assigned.sort(key=lambda tup: (tup[0].lineage_depth, tup[0].created_at))

        # Assign sibling_index per DNA in created_at order. Rebuild is
        # deterministic: the Nth soul with DNA X (by wall-clock birth)
        # gets sibling_index=N. If the soul's frontmatter already
        # carries a sibling_index (live births write it there), we
        # honor that instead of recomputing — keeps instance_ids stable
        # across rebuilds even when births interleave.
        sibling_by_dna: dict[str, int] = {}
        rebuild_sibling: dict[int, int] = {}
        for i, (s, _inst, _legacy) in enumerate(assigned):
            if s.sibling_index and s.sibling_index > 0:
                rebuild_sibling[i] = s.sibling_index
                cur = sibling_by_dna.get(s.dna, 0)
                sibling_by_dna[s.dna] = max(cur, s.sibling_index)
            else:
                nxt = sibling_by_dna.get(s.dna, 0) + 1
                sibling_by_dna[s.dna] = nxt
                rebuild_sibling[i] = nxt

        orphans: list[str] = []

        with transaction(self._conn):
            # Truncate in FK-safe order.
            for table in schema.REBUILD_TRUNCATE_ORDER:
                self._conn.execute(f"DELETE FROM {table};")

            ancestry_edges = 0
            for i, (soul_rec, inst, is_legacy) in enumerate(assigned):
                parent_inst = _resolve_parent_instance(soul_rec, assigned)
                if soul_rec.parent_dna and parent_inst is None:
                    orphans.append(f"{inst} (parent_dna={soul_rec.parent_dna})")
                self._insert_agent_row(
                    soul_rec,
                    instance_id=inst,
                    parent_instance=parent_inst,
                    status="active",
                    legacy_minted=is_legacy,
                    sibling_index=rebuild_sibling[i],
                )
                ancestry_edges += self._insert_ancestry_for(inst, parent_inst)

            # Audit events are mirrored in file order. ``instance_id``
            # is resolved by DNA when unambiguous; if multiple agents
            # share the short DNA, leave instance_id NULL (operator can
            # still look up by ``agent_dna``).
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

        Multiple rows are legitimate: same trait profile can be birthed
        more than once. Returned in creation order.
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
        sibling_index: int = 1,
    ) -> None:
        try:
            self._conn.execute(
                """
                INSERT INTO agents (
                    instance_id, dna, dna_full, role, agent_name,
                    parent_instance, owner_id, model_name, model_version,
                    soul_path, constitution_path, constitution_hash,
                    created_at, status, legacy_minted, sibling_index,
                    public_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
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
                    sibling_index,
                    # ADR-0049 T4 (Burst 243): per-agent ed25519 public
                    # key (base64-encoded). NULL when the soul's
                    # frontmatter lacked the field (legacy pre-v19
                    # rebuilds + tests that bypass the birth pipeline).
                    soul.public_key,
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

        Self-edge at depth 0 plus one edge per ancestor reachable
        through ``parent_instance``. Returns the number of rows
        inserted.
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
# Module-level helpers (formerly _resolve_parent_instance, _row_to_agent,
# _row_to_audit on registry.registry — moved here because they're only
# used by AgentsTable.)
# ---------------------------------------------------------------------------
def _resolve_parent_instance(
    soul: ParsedSoul,
    assigned: list[tuple[ParsedSoul, str, bool]],
) -> str | None:
    """Return parent instance_id if resolvable, else None.

    Order of attempts:
      1. Explicit ``parent_instance`` on the soul (modern births).
      2. ``parent_dna`` → lookup against the scanned souls. If exactly
         one match, use it. If multiple:
         a. If the child carries ``spawned_by``, prefer a candidate
            whose ``agent_name`` equals ``spawned_by``. Resolves the
            case where two agents legitimately share a short DNA and
            birth timestamp (e.g. a role default + a lineage root of
            the same role).
         b. Otherwise fall back to a temporal tie-break: only consider
            candidates whose ``created_at`` is ≤ the child's, pick the
            most recent. If no temporal match, return None (orphan,
            reported).
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
            # Even the name collides — apply temporal tie-break within
            # the name-matched subset.
            matches = by_name

    valid = [(s, inst) for s, inst in matches if s.created_at <= soul.created_at]
    if not valid:
        return None
    valid.sort(key=lambda pair: pair[0].created_at, reverse=True)
    return valid[0][1]


def _row_to_agent(row: sqlite3.Row) -> AgentRow:
    # sibling_index is v2+. Older DBs that somehow reach this path
    # (shouldn't happen after bootstrap's version check, but defensive)
    # get a default 1.
    keys = row.keys() if hasattr(row, "keys") else []
    sibling = int(row["sibling_index"]) if "sibling_index" in keys else 1
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
        sibling_index=sibling,
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
