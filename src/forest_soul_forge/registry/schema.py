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

SCHEMA_VERSION: int = 1

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
        FOREIGN KEY (parent_instance) REFERENCES agents(instance_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_agents_dna    ON agents(dna);",
    "CREATE INDEX IF NOT EXISTS idx_agents_role   ON agents(role);",
    "CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);",
    "CREATE INDEX IF NOT EXISTS idx_agents_parent ON agents(parent_instance);",
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
REBUILD_TRUNCATE_ORDER: tuple[str, ...] = (
    "audit_events",
    "agent_ancestry",
    "tools",
    "agent_capabilities",
    "agents",
)
