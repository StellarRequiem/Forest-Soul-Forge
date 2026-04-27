"""Registry SQL schema — DDL straight from ADR-0006.

The schema is v1. Any breaking change bumps ``SCHEMA_VERSION`` and requires a
migration path. Non-breaking additions (new indexes, new columns with defaults)
can land without a version bump so long as old registry files still open
correctly.

Rebuildability is the escape hatch for migrations: a breaking change can be
shipped as "drop + rebuild from artifacts" rather than writing upgrade SQL,
because the canonical source of truth is on disk.
"""
from __future__ import annotations

SCHEMA_VERSION: int = 5

# PRAGMA settings applied on every connection open. WAL mode lets readers not
# block writers; foreign_keys=ON is off by default in SQLite for historical
# reasons and must be re-enabled per connection.
CONNECTION_PRAGMAS: tuple[str, ...] = (
    "PRAGMA journal_mode=WAL;",
    "PRAGMA foreign_keys=ON;",
    "PRAGMA synchronous=NORMAL;",
)

# DDL is split into individual statements so we can execute them one at a time
# and surface any error with the statement that caused it.
DDL_STATEMENTS: tuple[str, ...] = (
    # --- agents -----------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS agents (
        instance_id      TEXT PRIMARY KEY,
        dna              TEXT NOT NULL,
        dna_full         TEXT NOT NULL,
        role             TEXT NOT NULL,
        agent_name       TEXT NOT NULL,
        parent_instance  TEXT,
        owner_id         TEXT,
        model_name       TEXT,
        model_version    TEXT,
        soul_path        TEXT NOT NULL,
        constitution_path TEXT NOT NULL,
        constitution_hash TEXT NOT NULL,
        created_at       TEXT NOT NULL,
        status           TEXT NOT NULL DEFAULT 'active',
        legacy_minted    INTEGER NOT NULL DEFAULT 0,
        sibling_index    INTEGER NOT NULL DEFAULT 1,
        FOREIGN KEY (parent_instance) REFERENCES agents(instance_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_agents_dna    ON agents(dna);",
    "CREATE INDEX IF NOT EXISTS idx_agents_role   ON agents(role);",
    "CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);",
    "CREATE INDEX IF NOT EXISTS idx_agents_parent ON agents(parent_instance);",
    # Sibling-index lookup is hot on the birth write path — every birth does
    # MAX(sibling_index) WHERE dna=? inside the write lock to pick the next
    # free slot. Composite index keeps that O(log n) instead of O(n).
    "CREATE INDEX IF NOT EXISTS idx_agents_dna_sibling ON agents(dna, sibling_index);",
    # --- ancestry closure table ------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS agent_ancestry (
        instance_id  TEXT NOT NULL,
        ancestor_id  TEXT NOT NULL,
        depth        INTEGER NOT NULL,
        PRIMARY KEY (instance_id, ancestor_id),
        FOREIGN KEY (instance_id) REFERENCES agents(instance_id),
        FOREIGN KEY (ancestor_id) REFERENCES agents(instance_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_ancestry_ancestor ON agent_ancestry(ancestor_id);",
    # --- audit mirror ----------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS audit_events (
        seq          INTEGER PRIMARY KEY,
        timestamp    TEXT NOT NULL,
        agent_dna    TEXT,
        instance_id  TEXT,
        event_type   TEXT NOT NULL,
        event_json   TEXT NOT NULL,
        entry_hash   TEXT NOT NULL,
        FOREIGN KEY (instance_id) REFERENCES agents(instance_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_audit_ts       ON audit_events(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_audit_type     ON audit_events(event_type);",
    "CREATE INDEX IF NOT EXISTS idx_audit_dna      ON audit_events(agent_dna);",
    "CREATE INDEX IF NOT EXISTS idx_audit_instance ON audit_events(instance_id);",
    # --- capabilities + tools (stubs; no writers in v1) -------------------
    """
    CREATE TABLE IF NOT EXISTS agent_capabilities (
        instance_id  TEXT NOT NULL,
        capability   TEXT NOT NULL,
        level        INTEGER,
        acquired_at  TEXT,
        PRIMARY KEY (instance_id, capability),
        FOREIGN KEY (instance_id) REFERENCES agents(instance_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS tools (
        tool_id           TEXT PRIMARY KEY,
        instance_id       TEXT NOT NULL,
        name              TEXT NOT NULL,
        description       TEXT,
        parameters_json   TEXT,
        code_snippet      TEXT,
        created_at        TEXT NOT NULL,
        is_inherited      INTEGER NOT NULL DEFAULT 0,
        parent_tool_id    TEXT,
        FOREIGN KEY (instance_id) REFERENCES agents(instance_id),
        FOREIGN KEY (parent_tool_id) REFERENCES tools(tool_id)
    );
    """,
    # --- idempotency cache -----------------------------------------------
    # Per ADR-0007: every write endpoint honors ``X-Idempotency-Key``.
    # The daemon hashes the request body alongside the key so a replay
    # with a mutated body is rejected (409) instead of silently served
    # from cache. Non-breaking addition — old DBs open fine; the
    # CREATE IF NOT EXISTS is self-healing.
    """
    CREATE TABLE IF NOT EXISTS idempotency_keys (
        key           TEXT PRIMARY KEY,
        endpoint      TEXT NOT NULL,
        request_hash  TEXT NOT NULL,
        status_code   INTEGER NOT NULL,
        response_json TEXT NOT NULL,
        created_at    TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_idempotency_created ON idempotency_keys(created_at);",
    # --- tool-call counters (ADR-0019 T2) --------------------------------
    # One row per (instance_id, session_id). The dispatcher increments
    # ``calls`` after a successful enforce-and-execute pass; the row is
    # created on first call and persists for the life of the registry.
    # ``last_call_at`` is informational (helps the operator spot stalled
    # sessions). The composite primary key keeps the lookup index-only.
    """
    CREATE TABLE IF NOT EXISTS tool_call_counters (
        instance_id   TEXT NOT NULL,
        session_id    TEXT NOT NULL,
        calls         INTEGER NOT NULL DEFAULT 0,
        last_call_at  TEXT,
        PRIMARY KEY (instance_id, session_id),
        FOREIGN KEY (instance_id) REFERENCES agents(instance_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_tool_call_counters_instance ON tool_call_counters(instance_id);",
    # --- tool-call accounting (ADR-0019 T4) ------------------------------
    # One row per terminating dispatch event (succeeded or failed). The
    # audit chain has the integrity proof; this table has the queryable
    # view. Both must agree — the dispatcher writes them under the same
    # write-lock-held transaction so a process crash between them is
    # caught by audit-chain verification on next boot. ``audit_seq``
    # joins back to the chain for full detail; the per-call cost +
    # token columns are denormalized for fast character-sheet roll-ups.
    """
    CREATE TABLE IF NOT EXISTS tool_calls (
        audit_seq        INTEGER PRIMARY KEY,
        instance_id      TEXT NOT NULL,
        session_id       TEXT NOT NULL,
        tool_key         TEXT NOT NULL,
        status           TEXT NOT NULL,
        tokens_used      INTEGER,
        cost_usd         REAL,
        side_effect_summary TEXT,
        finished_at      TEXT NOT NULL,
        FOREIGN KEY (instance_id) REFERENCES agents(instance_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_tool_calls_instance ON tool_calls(instance_id);",
    "CREATE INDEX IF NOT EXISTS idx_tool_calls_tool ON tool_calls(tool_key);",
    "CREATE INDEX IF NOT EXISTS idx_tool_calls_finished ON tool_calls(finished_at);",
    # --- approval queue (ADR-0019 T3) ------------------------------------
    # Tracks tool calls that hit ``requires_human_approval=True`` and are
    # waiting for an operator decision. ``ticket_id`` is the same string
    # the dispatcher minted in T2 (``pending-{instance}-{session}-{seq}``)
    # so an in-flight ticket from before this table existed can still
    # be looked up by clients that already saw it. ``args_json`` holds
    # the original args so the resume path doesn't have to re-fetch
    # them from the chain. ``decided_audit_seq`` points at the
    # tool_call_approved or tool_call_rejected entry the operator's
    # decision produced — provides the cross-link the chain doesn't
    # carry directly.
    """
    CREATE TABLE IF NOT EXISTS tool_call_pending_approvals (
        ticket_id          TEXT PRIMARY KEY,
        instance_id        TEXT NOT NULL,
        session_id         TEXT NOT NULL,
        tool_key           TEXT NOT NULL,
        args_json          TEXT NOT NULL,
        side_effects       TEXT NOT NULL,
        status             TEXT NOT NULL DEFAULT 'pending',
        pending_audit_seq  INTEGER NOT NULL,
        decided_audit_seq  INTEGER,
        decided_by         TEXT,
        decision_reason    TEXT,
        created_at         TEXT NOT NULL,
        decided_at         TEXT,
        FOREIGN KEY (instance_id) REFERENCES agents(instance_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_pending_approvals_instance ON tool_call_pending_approvals(instance_id);",
    "CREATE INDEX IF NOT EXISTS idx_pending_approvals_status ON tool_call_pending_approvals(status);",
    # --- metadata --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS registry_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
)

# Metadata rows written on bootstrap. ``canonical_contract`` is a tripwire —
# any tool or migration that reads this DB should refuse to treat it as
# canonical.
INITIAL_METADATA: tuple[tuple[str, str], ...] = (
    ("schema_version", str(SCHEMA_VERSION)),
    ("canonical_contract", "artifacts-authoritative"),
)

# Tables the rebuild path clears before repopulating. Order matters because of
# foreign keys — children before parents.
#
# ``tool_call_counters`` is included so a rebuild-from-artifacts wipes the
# per-session call budgets — they're runtime state, not artifact state, and
# leaving stale rows around would let an attacker who tampered with the
# artifact tree carry forward partial budgets from a prior life. Rebuild
# resets every counter to zero on the first call after rebuild.
REBUILD_TRUNCATE_ORDER: tuple[str, ...] = (
    "audit_events",
    "agent_ancestry",
    "tool_call_pending_approvals",
    "tool_calls",
    "tool_call_counters",
    "tools",
    "agent_capabilities",
    "agents",
)

# ---------------------------------------------------------------------------
# Forward migrations
#
# Invariant: running ``MIGRATIONS[N]`` on a DB at version N-1 produces a DB
# indistinguishable from a fresh install at version N. Everything here is
# **additive only** — new tables, new columns with defaults, new indexes. A
# breaking change (drop a column, tighten a constraint, rename) is NOT a
# migration; the escape hatch for that is ``rebuild_from_artifacts`` because
# the canonical source of truth is the artifact tree on disk.
#
# Each ``MIGRATIONS[N]`` tuple is executed inside one transaction together
# with the ``schema_version`` metadata bump. Either the whole step lands or
# none of it does.
#
# When bumping ``SCHEMA_VERSION``, add a ``MIGRATIONS[new_version]`` entry
# covering the diff from the previous version. Missing entries are treated
# as a hard error at bootstrap time, not a silent skip.
# ---------------------------------------------------------------------------
MIGRATIONS: dict[int, tuple[str, ...]] = {
    # v1 → v2: add sibling_index to agents (+ composite index) and the
    # idempotency_keys cache table. Both landed as part of the write path
    # build-out (ADR-0007). Old DBs lack the column and the table; this
    # migration adds them in place without touching row data.
    2: (
        # SQLite ALTER TABLE ADD COLUMN supports NOT NULL only when a
        # DEFAULT is supplied — the default populates existing rows at
        # add time.
        "ALTER TABLE agents ADD COLUMN sibling_index INTEGER NOT NULL DEFAULT 1;",
        "CREATE INDEX IF NOT EXISTS idx_agents_dna_sibling ON agents(dna, sibling_index);",
        """
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            key           TEXT PRIMARY KEY,
            endpoint      TEXT NOT NULL,
            request_hash  TEXT NOT NULL,
            status_code   INTEGER NOT NULL,
            response_json TEXT NOT NULL,
            created_at    TEXT NOT NULL
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_idempotency_created ON idempotency_keys(created_at);",
    ),
    # v2 → v3: add tool_call_counters (ADR-0019 T2). One row per
    # (instance_id, session_id) for max_calls_per_session enforcement.
    # Pure addition — old DBs gain the table without touching any
    # existing data. CREATE IF NOT EXISTS is self-healing if a partial
    # migration ran (the transaction wrapper makes that impossible in
    # practice, but defensive).
    3: (
        """
        CREATE TABLE IF NOT EXISTS tool_call_counters (
            instance_id   TEXT NOT NULL,
            session_id    TEXT NOT NULL,
            calls         INTEGER NOT NULL DEFAULT 0,
            last_call_at  TEXT,
            PRIMARY KEY (instance_id, session_id),
            FOREIGN KEY (instance_id) REFERENCES agents(instance_id)
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_tool_call_counters_instance ON tool_call_counters(instance_id);",
    ),
    # v3 → v4: add tool_calls (ADR-0019 T4). One row per terminating
    # dispatch event; audit chain has the integrity proof, this table
    # has the queryable view. Pure addition.
    4: (
        """
        CREATE TABLE IF NOT EXISTS tool_calls (
            audit_seq        INTEGER PRIMARY KEY,
            instance_id      TEXT NOT NULL,
            session_id       TEXT NOT NULL,
            tool_key         TEXT NOT NULL,
            status           TEXT NOT NULL,
            tokens_used      INTEGER,
            cost_usd         REAL,
            side_effect_summary TEXT,
            finished_at      TEXT NOT NULL,
            FOREIGN KEY (instance_id) REFERENCES agents(instance_id)
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_tool_calls_instance ON tool_calls(instance_id);",
        "CREATE INDEX IF NOT EXISTS idx_tool_calls_tool ON tool_calls(tool_key);",
        "CREATE INDEX IF NOT EXISTS idx_tool_calls_finished ON tool_calls(finished_at);",
    ),
    # v4 → v5: add tool_call_pending_approvals (ADR-0019 T3). Tracks
    # gated calls awaiting operator decision. Pure addition.
    5: (
        """
        CREATE TABLE IF NOT EXISTS tool_call_pending_approvals (
            ticket_id          TEXT PRIMARY KEY,
            instance_id        TEXT NOT NULL,
            session_id         TEXT NOT NULL,
            tool_key           TEXT NOT NULL,
            args_json          TEXT NOT NULL,
            side_effects       TEXT NOT NULL,
            status             TEXT NOT NULL DEFAULT 'pending',
            pending_audit_seq  INTEGER NOT NULL,
            decided_audit_seq  INTEGER,
            decided_by         TEXT,
            decision_reason    TEXT,
            created_at         TEXT NOT NULL,
            decided_at         TEXT,
            FOREIGN KEY (instance_id) REFERENCES agents(instance_id)
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_pending_approvals_instance ON tool_call_pending_approvals(instance_id);",
        "CREATE INDEX IF NOT EXISTS idx_pending_approvals_status ON tool_call_pending_approvals(status);",
    ),
}
