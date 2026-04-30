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

SCHEMA_VERSION: int = 10

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
    # --- memory subsystem v0.1 (ADR-0022 + ADR-0027) ---------------------
    # One row per memory entry. Scope is one of private | lineage |
    # realm | consented (only `private` is reachable in v0.1; the
    # others are designed in but unused until ADR-0027's per-event
    # consent + multi-agent disclosure tranches land).
    #
    # ``layer`` is the memory layer (episodic | semantic | procedural).
    # ``content_digest`` is SHA-256 over canonical content; lets
    # tamper detection notice if the row's content was edited
    # outside the API. ``deleted_at`` is the tombstone marker —
    # soft delete keeps the row visible to audit but excluded from
    # default reads. Hard delete (purge) removes the row entirely
    # and emits memory_purged in the chain.
    """
    CREATE TABLE IF NOT EXISTS memory_entries (
        entry_id        TEXT PRIMARY KEY,
        instance_id     TEXT NOT NULL,
        agent_dna       TEXT NOT NULL,
        layer           TEXT NOT NULL,
        scope           TEXT NOT NULL DEFAULT 'private',
        content         TEXT NOT NULL,
        content_digest  TEXT NOT NULL,
        tags_json       TEXT NOT NULL DEFAULT '[]',
        consented_to_json TEXT NOT NULL DEFAULT '[]',
        created_at      TEXT NOT NULL,
        deleted_at      TEXT,
        -- v7 additions (ADR-0022 v0.2 — cross-agent disclosure):
        -- when this row is a *disclosed copy* on a recipient's store,
        -- these three columns capture the minimum-disclosure metadata
        -- per ADR-0027 §4. Null on the originating agent's row.
        disclosed_from_entry TEXT,
        disclosed_summary    TEXT,
        disclosed_at         TEXT,
        FOREIGN KEY (instance_id) REFERENCES agents(instance_id),
        FOREIGN KEY (disclosed_from_entry) REFERENCES memory_entries(entry_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_memory_instance ON memory_entries(instance_id);",
    "CREATE INDEX IF NOT EXISTS idx_memory_layer ON memory_entries(layer);",
    "CREATE INDEX IF NOT EXISTS idx_memory_created ON memory_entries(created_at);",
    # Disclosed-copy back-reference index — answers "who has copies of
    # entry X?" in O(rows-with-pointer) instead of full-table scan.
    # Used by the future revocation propagation path (ADR-0022 v0.3).
    "CREATE INDEX IF NOT EXISTS idx_memory_disclosed_from ON memory_entries(disclosed_from_entry);",
    # --- memory consents (ADR-0022 v0.2) ---------------------------------
    # Per-(entry, recipient) consent grants. Per-event consent only in
    # v0.2; per-relationship + tiered consent (ADR-0027 §2) are deferred
    # to v0.3 driven by Horizon 3 social-anchoring needs.
    #
    # Composite primary key (entry_id, recipient_instance) makes the
    # "is X allowed to see entry Y?" check a single index probe, and
    # naturally rejects duplicate grants without an explicit constraint.
    """
    CREATE TABLE IF NOT EXISTS memory_consents (
        entry_id           TEXT NOT NULL,
        recipient_instance TEXT NOT NULL,
        granted_at         TEXT NOT NULL,
        granted_by         TEXT NOT NULL,
        revoked_at         TEXT,
        PRIMARY KEY (entry_id, recipient_instance),
        FOREIGN KEY (entry_id) REFERENCES memory_entries(entry_id),
        FOREIGN KEY (recipient_instance) REFERENCES agents(instance_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_memory_consents_recipient ON memory_consents(recipient_instance);",
    # --- metadata --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS registry_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    # --- per-agent encrypted secrets (ADR-003X Phase C1) -----------------
    """
    CREATE TABLE IF NOT EXISTS agent_secrets (
        instance_id      TEXT NOT NULL,
        name             TEXT NOT NULL,
        ciphertext       BLOB NOT NULL,
        nonce            BLOB NOT NULL,
        created_at       TEXT NOT NULL,
        last_revealed_at TEXT,
        PRIMARY KEY (instance_id, name),
        FOREIGN KEY (instance_id) REFERENCES agents(instance_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_agent_secrets_instance ON agent_secrets(instance_id);",
    # --- per-entry verification (ADR-003X K1 — Iron Gate equivalent) -----
    # Reuses the consent-grant SEMANTIC (idempotent grant + revoke,
    # external party stamps standing on an entry) but stores it in a
    # dedicated table because memory_consents.recipient_instance has
    # an FK on agents and the verifier identifier (operator handle,
    # public key fingerprint) isn't a registered agent. One row per
    # entry — re-verification updates in place, revocation sets
    # revoked_at + records who revoked.
    """
    CREATE TABLE IF NOT EXISTS memory_verifications (
        entry_id      TEXT PRIMARY KEY,
        verifier_id   TEXT NOT NULL,
        verified_at   TEXT NOT NULL,
        seal_note     TEXT,
        revoked_at    TEXT,
        revoked_by    TEXT,
        FOREIGN KEY (entry_id) REFERENCES memory_entries(entry_id)
    );
    """,
    # --- conversations (ADR-003Y Y1) -------------------------------------
    # First-class operator-driven multi-turn interaction. ``domain`` is
    # operator-defined free-text (recommended seeds: therapy, coding,
    # builders, admin). ``retention_policy`` governs how long raw
    # ``conversation_turns.body`` lives before lazy summarization +
    # body deletion (full_7d default, full_30d, or full_indefinite).
    """
    CREATE TABLE IF NOT EXISTS conversations (
        conversation_id  TEXT PRIMARY KEY,
        domain           TEXT NOT NULL,
        operator_id      TEXT NOT NULL,
        created_at       TEXT NOT NULL,
        last_turn_at     TEXT,
        status           TEXT NOT NULL DEFAULT 'active',
        retention_policy TEXT NOT NULL DEFAULT 'full_7d'
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_conversations_domain    ON conversations(domain);",
    "CREATE INDEX IF NOT EXISTS idx_conversations_operator  ON conversations(operator_id);",
    "CREATE INDEX IF NOT EXISTS idx_conversations_status    ON conversations(status);",
    "CREATE INDEX IF NOT EXISTS idx_conversations_last_turn ON conversations(last_turn_at);",
    # Participants. ``bridged_from`` is the source domain when the agent
    # was added via /conversations/{id}/bridge from another domain;
    # NULL for same-domain joins. Composite PK collapses uniqueness.
    """
    CREATE TABLE IF NOT EXISTS conversation_participants (
        conversation_id  TEXT NOT NULL,
        instance_id      TEXT NOT NULL,
        joined_at        TEXT NOT NULL,
        bridged_from     TEXT,
        PRIMARY KEY (conversation_id, instance_id),
        FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id),
        FOREIGN KEY (instance_id) REFERENCES agents(instance_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_participants_instance ON conversation_participants(instance_id);",
    # Turns. ``body`` is NULL after the retention window expires (Y7
    # background pass writes ``summary`` then deletes body).
    # ``body_hash`` is SHA-256 over the original body and persists for
    # tamper-evidence even after the body is purged. ``addressed_to``
    # is a comma-joined string of instance_ids when the operator
    # addresses specific agents; NULL means "whole room."
    """
    CREATE TABLE IF NOT EXISTS conversation_turns (
        turn_id          TEXT PRIMARY KEY,
        conversation_id  TEXT NOT NULL,
        speaker          TEXT NOT NULL,
        addressed_to     TEXT,
        body             TEXT,
        summary          TEXT,
        body_hash        TEXT NOT NULL,
        token_count      INTEGER,
        timestamp        TEXT NOT NULL,
        model_used       TEXT,
        FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_turns_conversation ON conversation_turns(conversation_id);",
    "CREATE INDEX IF NOT EXISTS idx_turns_timestamp    ON conversation_turns(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_turns_speaker      ON conversation_turns(speaker);",
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
    # memory_consents references memory_entries(entry_id) — clear first.
    "memory_consents",
    "memory_entries",
    # ADR-003X C1: agent_secrets references agents(instance_id) — clear
    # first. Note: rebuild-from-artifacts will NOT recover secrets
    # (they're not in the artifact tree by design — encrypted blobs in
    # the registry are not canonical). Operator re-sets secrets after
    # rebuild via set_secret() calls.
    "agent_secrets",
    # ADR-003X K1: memory_verifications references memory_entries —
    # clear before memory_entries. Like secrets, verifications are
    # not in the artifact tree; operator re-verifies after rebuild
    # via memory_verify.v1 calls.
    "memory_verifications",
    # ADR-003Y Y1: conversation tables. ``conversation_turns`` and
    # ``conversation_participants`` reference ``conversations`` so
    # children clear first. Like secrets / verifications, conversations
    # are runtime state and are not recovered by rebuild-from-artifacts
    # — the operator re-creates rooms and re-issues turns after rebuild.
    "conversation_turns",
    "conversation_participants",
    "conversations",
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
    # v5 → v6: memory subsystem v0.1 (ADR-0022 + ADR-0027). Per-agent
    # private-scope memory only. Other scopes are designed in but
    # unused pre per-event-consent + multi-agent disclosure.
    6: (
        """
        CREATE TABLE IF NOT EXISTS memory_entries (
            entry_id        TEXT PRIMARY KEY,
            instance_id     TEXT NOT NULL,
            agent_dna       TEXT NOT NULL,
            layer           TEXT NOT NULL,
            scope           TEXT NOT NULL DEFAULT 'private',
            content         TEXT NOT NULL,
            content_digest  TEXT NOT NULL,
            tags_json       TEXT NOT NULL DEFAULT '[]',
            consented_to_json TEXT NOT NULL DEFAULT '[]',
            created_at      TEXT NOT NULL,
            deleted_at      TEXT,
            FOREIGN KEY (instance_id) REFERENCES agents(instance_id)
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_memory_instance ON memory_entries(instance_id);",
        "CREATE INDEX IF NOT EXISTS idx_memory_layer ON memory_entries(layer);",
        "CREATE INDEX IF NOT EXISTS idx_memory_created ON memory_entries(created_at);",
    ),
    # v6 → v7: cross-agent memory disclosure (ADR-0022 v0.2). Adds the
    # three disclosed_* columns on memory_entries (NULL on originating
    # rows; populated on disclosed-copy rows on the recipient's side)
    # plus the memory_consents table for per-event consent grants.
    #
    # Pure addition — old v6 rows are unaffected. Existing readers that
    # don't know about disclosed_* see NULL and behave as they did
    # under v0.1 (private-only).
    7: (
        # ALTER TABLE ADD COLUMN — these are nullable (no DEFAULT) so
        # existing rows get NULL, which is the correct semantic ("this
        # is an originating entry, not a disclosed copy").
        "ALTER TABLE memory_entries ADD COLUMN disclosed_from_entry TEXT;",
        "ALTER TABLE memory_entries ADD COLUMN disclosed_summary    TEXT;",
        "ALTER TABLE memory_entries ADD COLUMN disclosed_at         TEXT;",
        # Index for back-reference lookups: "who holds copies of X?"
        "CREATE INDEX IF NOT EXISTS idx_memory_disclosed_from ON memory_entries(disclosed_from_entry);",
        # New memory_consents table. Composite PK collapses the
        # uniqueness constraint into the index; FK to memory_entries
        # ties consent lifetime to the entry's lifetime (purging the
        # entry deletes its consent rows on cascade-aware tooling).
        """
        CREATE TABLE IF NOT EXISTS memory_consents (
            entry_id           TEXT NOT NULL,
            recipient_instance TEXT NOT NULL,
            granted_at         TEXT NOT NULL,
            granted_by         TEXT NOT NULL,
            revoked_at         TEXT,
            PRIMARY KEY (entry_id, recipient_instance),
            FOREIGN KEY (entry_id) REFERENCES memory_entries(entry_id),
            FOREIGN KEY (recipient_instance) REFERENCES agents(instance_id)
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_memory_consents_recipient ON memory_consents(recipient_instance);",
    ),
    # v7 → v8: per-agent encrypted secrets store (ADR-003X Phase C1).
    # Foundation for the open-web tool family — web_fetch, browser_action,
    # mcp_call all read API tokens / cookies from here. AES-256-GCM
    # encryption is per-row; AAD pins (instance_id, name) so a stolen
    # ciphertext can't be re-attached to a different row. Master key
    # comes from FSF_SECRETS_MASTER_KEY env var; subsystem disables
    # cleanly when unset (tools refuse with SecretsUnavailableError).
    # Pure addition — no impact on existing agents that don't use it.
    8: (
        """
        CREATE TABLE IF NOT EXISTS agent_secrets (
            instance_id      TEXT NOT NULL,
            name             TEXT NOT NULL,
            ciphertext       BLOB NOT NULL,
            nonce            BLOB NOT NULL,
            created_at       TEXT NOT NULL,
            last_revealed_at TEXT,
            PRIMARY KEY (instance_id, name),
            FOREIGN KEY (instance_id) REFERENCES agents(instance_id)
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_agent_secrets_instance ON agent_secrets(instance_id);",
    ),
    # v8 → v9: per-entry memory verification (ADR-003X K1 — Iron Gate
    # equivalent). Reuses the consent-grant SEMANTIC (idempotent
    # promote + revoke; external party stamps standing on an entry)
    # but stores it in a dedicated table because
    # memory_consents.recipient_instance has an FK on agents — the
    # verifier identifier (operator handle, public key fingerprint)
    # isn't a registered agent. One row per entry; re-verification
    # updates in place; revocation sets revoked_at + revoked_by.
    9: (
        """
        CREATE TABLE IF NOT EXISTS memory_verifications (
            entry_id      TEXT PRIMARY KEY,
            verifier_id   TEXT NOT NULL,
            verified_at   TEXT NOT NULL,
            seal_note     TEXT,
            revoked_at    TEXT,
            revoked_by    TEXT,
            FOREIGN KEY (entry_id) REFERENCES memory_entries(entry_id)
        );
        """,
    ),
    # v9 → v10: ADR-003Y Y1 conversation runtime substrate. Three new
    # tables: ``conversations`` (operator-defined rooms with
    # retention policy), ``conversation_participants`` (which agents
    # are in which room, with optional bridged_from for cross-domain
    # invitations), and ``conversation_turns`` (one row per turn;
    # ``body`` is purged after retention window per Y7's lazy
    # summarization, but ``body_hash`` persists for tamper-evidence).
    # Pure addition — no impact on existing rows; old DBs gain three
    # tables and the ability to host conversation-mode agents.
    10: (
        """
        CREATE TABLE IF NOT EXISTS conversations (
            conversation_id  TEXT PRIMARY KEY,
            domain           TEXT NOT NULL,
            operator_id      TEXT NOT NULL,
            created_at       TEXT NOT NULL,
            last_turn_at     TEXT,
            status           TEXT NOT NULL DEFAULT 'active',
            retention_policy TEXT NOT NULL DEFAULT 'full_7d'
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_conversations_domain    ON conversations(domain);",
        "CREATE INDEX IF NOT EXISTS idx_conversations_operator  ON conversations(operator_id);",
        "CREATE INDEX IF NOT EXISTS idx_conversations_status    ON conversations(status);",
        "CREATE INDEX IF NOT EXISTS idx_conversations_last_turn ON conversations(last_turn_at);",
        """
        CREATE TABLE IF NOT EXISTS conversation_participants (
            conversation_id  TEXT NOT NULL,
            instance_id      TEXT NOT NULL,
            joined_at        TEXT NOT NULL,
            bridged_from     TEXT,
            PRIMARY KEY (conversation_id, instance_id),
            FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id),
            FOREIGN KEY (instance_id) REFERENCES agents(instance_id)
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_participants_instance ON conversation_participants(instance_id);",
        """
        CREATE TABLE IF NOT EXISTS conversation_turns (
            turn_id          TEXT PRIMARY KEY,
            conversation_id  TEXT NOT NULL,
            speaker          TEXT NOT NULL,
            addressed_to     TEXT,
            body             TEXT,
            summary          TEXT,
            body_hash        TEXT NOT NULL,
            token_count      INTEGER,
            timestamp        TEXT NOT NULL,
            model_used       TEXT,
            FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id)
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_turns_conversation ON conversation_turns(conversation_id);",
        "CREATE INDEX IF NOT EXISTS idx_turns_timestamp    ON conversation_turns(timestamp);",
        "CREATE INDEX IF NOT EXISTS idx_turns_speaker      ON conversation_turns(speaker);",
    ),
}
