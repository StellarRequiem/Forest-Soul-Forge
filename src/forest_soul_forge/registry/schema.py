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

SCHEMA_VERSION: int = 20

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
        -- ADR-0045 (Burst 114): per-agent posture (traffic light).
        -- Mutable runtime state, NOT part of constitution_hash.
        -- green=honor existing policy / yellow=force pending on
        -- non-read_only / red=refuse non-read_only outright.
        posture          TEXT NOT NULL DEFAULT 'yellow'
                         CHECK (posture IN ('green', 'yellow', 'red')),
        -- ADR-0049 T4 (Burst 243): ed25519 public key for per-event
        -- signatures. Base64-encoded raw 32-byte public-key bytes
        -- (matches the in-soul-frontmatter representation).
        -- Nullable: legacy pre-v19 agents stay NULL; verifier treats
        -- their entries as 'legacy unsigned' per ADR-0049 D5.
        public_key       TEXT,
        FOREIGN KEY (parent_instance) REFERENCES agents(instance_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_agents_dna    ON agents(dna);",
    "CREATE INDEX IF NOT EXISTS idx_agents_role   ON agents(role);",
    "CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);",
    "CREATE INDEX IF NOT EXISTS idx_agents_posture ON agents(posture);",
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
        -- v11 additions (ADR-0027-amendment §7 — epistemic metadata):
        -- ``claim_type`` distinguishes observations from inferences from
        -- preferences etc. so agents can't silently treat their own
        -- inferences as operator-stated facts. ``confidence`` is a
        -- three-state coarse signal (low/medium/high). ``last_challenged_at``
        -- captures staleness pressure — an inference unchallenged for
        -- 30+ days surfaces as stale at recall time.
        claim_type      TEXT NOT NULL DEFAULT 'observation'
                          CHECK (claim_type IN (
                              'observation', 'user_statement',
                              'agent_inference', 'preference',
                              'promise', 'external_fact'
                          )),
        confidence      TEXT NOT NULL DEFAULT 'medium'
                          CHECK (confidence IN ('low', 'medium', 'high')),
        last_challenged_at TEXT,
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
    # v11 — claim_type index supports "show me all inferences" UI surfaces
    # without a full-table scan. Cardinality is small (six values) so the
    # index is cheap; the gain is on the targeted-inferences case.
    "CREATE INDEX IF NOT EXISTS idx_memory_claim_type ON memory_entries(claim_type);",
    # v11 — last_challenged_at supports the staleness sweep at recall time
    # (entries with old last_challenged_at + claim_type=agent_inference
    # surface as stale). Partial index over non-NULL values keeps it small.
    "CREATE INDEX IF NOT EXISTS idx_memory_last_challenged ON memory_entries(last_challenged_at) "
        "WHERE last_challenged_at IS NOT NULL;",
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
    # --- memory_procedural_shortcuts (v16 — ADR-0054 T1) -----------------
    # Per-instance procedural-shortcut storage. The dispatcher's
    # ProceduralShortcutStep (ADR-0054 T3) consults this table BEFORE
    # firing llm_think — on a high-confidence situation→action match,
    # the recorded action is returned directly + a tool_call_shortcut
    # event lands in the audit chain (T4). Operator overrides via env
    # vars (ADR-0054 D6); master switch defaults off in v0.1.
    #
    # Sibling table not column-extension on memory_entries because
    # access pattern (cosine similarity over BLOB embeddings) differs
    # from episodic/semantic (text search), and ADR-0040 trust-surface
    # decomposition prefers separate tables for separately-grantable
    # capabilities.
    #
    # Per ADR-0001 D2 identity invariance: this table is per-instance
    # state, not identity. constitution_hash + DNA stay immutable;
    # only what the agent KNOWS evolves, not what it IS.
    """
    CREATE TABLE IF NOT EXISTS memory_procedural_shortcuts (
        shortcut_id          TEXT PRIMARY KEY,
        instance_id          TEXT NOT NULL,
        created_at           TEXT NOT NULL,
        last_matched_at      TEXT,
        last_matched_seq     INTEGER,

        situation_text       TEXT NOT NULL,
        situation_embedding  BLOB NOT NULL,

        action_kind          TEXT NOT NULL
                             CHECK (action_kind IN ('response', 'tool_call', 'no_op')),
        action_payload       TEXT NOT NULL,

        success_count        INTEGER NOT NULL DEFAULT 0,
        failure_count        INTEGER NOT NULL DEFAULT 0,

        learned_from_seq     INTEGER NOT NULL,
        learned_from_kind    TEXT NOT NULL
                             CHECK (learned_from_kind IN ('auto', 'operator_tagged')),

        FOREIGN KEY (instance_id)
            REFERENCES agents(instance_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_psh_instance "
        "ON memory_procedural_shortcuts(instance_id);",
    # --- memory contradictions (v11 — ADR-0027-amendment §7.3) -----------
    # Many-to-many: one entry can be contradicted by N later entries, and
    # one new entry can contradict N older ones. Composite shape doesn't
    # fit on memory_entries as a column; separate table is the correct
    # normalization.
    #
    # Contradictions are operator- or agent-supplied at v0.2; auto-
    # detection is deferred to ADR-0036 (Verifier Loop, queued for v0.3).
    # ``detected_by`` records the agent/operator id; ``resolved_at``
    # is NULL while the contradiction is open. Resolution is operator-
    # narrated (``resolution_summary``) — this is the workflow surface
    # for "the operator decided which version is true."
    """
    CREATE TABLE IF NOT EXISTS memory_contradictions (
        contradiction_id   TEXT PRIMARY KEY,
        earlier_entry_id   TEXT NOT NULL,
        later_entry_id     TEXT NOT NULL,
        contradiction_kind TEXT NOT NULL CHECK (contradiction_kind IN (
            'direct', 'updated', 'qualified', 'retracted'
        )),
        detected_at        TEXT NOT NULL,
        detected_by        TEXT NOT NULL,
        resolved_at        TEXT,
        resolution_summary TEXT,
        -- v12 (ADR-0036 §4.3 + T6) — ratification dial. Verifier-flagged
        -- rows land at flagged_unreviewed; operators move them through
        -- the lifecycle. Recall surfaces (ADR-0027-am T3 + T7) default
        -- to filtering out flagged_rejected so a known-false flag stops
        -- surfacing on every recall. auto_resolved is reserved for v0.4
        -- system-driven resolution paths.
        flagged_state      TEXT NOT NULL DEFAULT 'flagged_unreviewed'
            CHECK (flagged_state IN (
                'flagged_unreviewed', 'flagged_confirmed',
                'flagged_rejected', 'auto_resolved'
            )),
        FOREIGN KEY (earlier_entry_id) REFERENCES memory_entries(entry_id),
        FOREIGN KEY (later_entry_id)   REFERENCES memory_entries(entry_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_contradictions_earlier ON memory_contradictions(earlier_entry_id);",
    "CREATE INDEX IF NOT EXISTS idx_contradictions_later   ON memory_contradictions(later_entry_id);",
    # Partial index over unresolved contradictions — the common query
    # ("any open contradictions affecting entry X?") is hot; resolved
    # rows are kept for audit but rarely queried.
    "CREATE INDEX IF NOT EXISTS idx_contradictions_unresolved ON memory_contradictions(resolved_at) "
        "WHERE resolved_at IS NULL;",
    # v12 — partial index over the ratification-pending rows. The
    # operator review surface (ADR-0037 dashboard) walks these often;
    # the index keeps the query cheap as the table grows.
    "CREATE INDEX IF NOT EXISTS idx_contradictions_state ON memory_contradictions(flagged_state) "
        "WHERE flagged_state = 'flagged_unreviewed';",
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
    # ADR-0041 T5 (Burst 90): scheduled_task_state. Mirrors the
    # in-memory ``ScheduledTask.state`` so the scheduler survives
    # daemon restarts. Without persistence:
    #   * consecutive_failures resets on restart → tripped breakers
    #     unblock and the broken task retries immediately
    #   * last_run_at resets → tasks fire IMMEDIATELY on restart even
    #     if they ran 30s before the crash, double-billing budgets
    #   * total_runs / total_successes / total_failures lose their
    #     career history, hiding long-running flakiness
    # The audit chain is the source of truth (every state change
    # already emits scheduled_task_dispatched/completed/failed).
    # This table is the indexed view — fast read on Scheduler.start
    # without replaying the chain. Updated atomically inside
    # _dispatch under the daemon's write_lock.
    """
    CREATE TABLE IF NOT EXISTS scheduled_task_state (
        task_id                  TEXT PRIMARY KEY,
        last_run_at              TEXT,
        next_run_at              TEXT,
        consecutive_failures     INTEGER NOT NULL DEFAULT 0,
        circuit_breaker_open     INTEGER NOT NULL DEFAULT 0,
        total_runs               INTEGER NOT NULL DEFAULT 0,
        total_successes          INTEGER NOT NULL DEFAULT 0,
        total_failures           INTEGER NOT NULL DEFAULT 0,
        last_failure_reason      TEXT,
        last_run_outcome         TEXT,
        updated_at               TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_scheduled_task_state_breaker "
        "ON scheduled_task_state(circuit_breaker_open) "
        "WHERE circuit_breaker_open = 1;",
    # ADR-0043 follow-up #2 (Burst 113): agent_plugin_grants.
    # Post-birth grants of MCP plugin access without rebirthing the
    # agent (constitution_hash is immutable per agent — see CLAUDE.md
    # architectural invariants — so we add an explicit augmentation
    # layer rather than mutate the constitution).
    #
    # ADR-0053 extension (Burst 235): per-tool granularity via the
    # optional ``tool_name`` column. NULL ``tool_name`` = plugin-level
    # grant (the ADR-0043 original semantic, all tools the manifest
    # declares). Non-NULL ``tool_name`` = per-tool grant covering ONLY
    # that one tool. The dispatcher's grant-resolution path applies
    # specificity-wins precedence: per-tool grant beats plugin-level
    # when both exist (ADR-0053 Decision 3).
    #
    # Effective allowed_mcp_servers at dispatch time =
    #   constitution.allowed_mcp_servers
    #     ∪ {plugin where any active grant row exists (plugin- or
    #        per-tool-level) for that plugin}
    #   …with per-tool grants further narrowing the effective TOOL
    #   set inside that plugin to the named tool(s).
    #
    # ``trust_tier`` is forward-compatible storage for ADR-0045
    # (Agent Posture / Trust-Light System). For Burst 113 the value
    # was informational; ADR-0045's PostureGateStep + ADR-0060's
    # GrantPolicy now consult it.
    #
    # Composite PRIMARY KEY (instance_id, plugin_name, tool_name) —
    # SQLite treats NULL as distinct for PRIMARY KEY uniqueness, so
    # the partial unique index ``ux_plugin_grants_plugin_level`` is
    # what enforces "at most one plugin-level grant per (agent, plugin)"
    # in the NULL-tool_name case. The PRIMARY KEY does the work for
    # non-NULL rows (at most one per-tool grant per (agent, plugin,
    # tool)). Re-granting an already-active grant is idempotent
    # (ON CONFLICT no-op). Revoking flips revoked_at_seq NULL → seq;
    # granting after a revoke creates a fresh row, preserving the
    # historical record.
    """
    CREATE TABLE IF NOT EXISTS agent_plugin_grants (
        instance_id      TEXT NOT NULL,
        plugin_name      TEXT NOT NULL,
        tool_name        TEXT,
        trust_tier       TEXT NOT NULL DEFAULT 'yellow'
                         CHECK (trust_tier IN ('green', 'yellow', 'red')),
        granted_at_seq   INTEGER NOT NULL,
        granted_by       TEXT,
        granted_at       TEXT NOT NULL,
        revoked_at_seq   INTEGER,
        revoked_at       TEXT,
        revoked_by       TEXT,
        reason           TEXT,
        PRIMARY KEY (instance_id, plugin_name, tool_name),
        FOREIGN KEY (instance_id)
            REFERENCES agents(instance_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_plugin_grants_active "
        "ON agent_plugin_grants(instance_id) "
        "WHERE revoked_at_seq IS NULL;",
    # ADR-0053 D1 (Burst 235): plugin-level uniqueness in the
    # NULL-tool_name partition. PRIMARY KEY can't enforce this on
    # its own because SQLite allows multiple NULLs in a PK column.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_plugin_grants_plugin_level "
        "ON agent_plugin_grants(instance_id, plugin_name) "
        "WHERE tool_name IS NULL;",
    # ADR-0060 T1 (Burst 219): agent_catalog_grants.
    # Sister table to agent_plugin_grants, keyed by
    # (instance_id, tool_name, tool_version). Lets operators grant
    # catalog-tool access to a born agent without rebirthing — the
    # constitution_hash stays immutable while the effective tool
    # surface expands at runtime. T2 (queued) wires the dispatcher
    # to consult this on constitution-check miss.
    """
    CREATE TABLE IF NOT EXISTS agent_catalog_grants (
        instance_id      TEXT NOT NULL,
        tool_name        TEXT NOT NULL,
        tool_version     TEXT NOT NULL,
        trust_tier       TEXT NOT NULL DEFAULT 'yellow'
                         CHECK (trust_tier IN ('green', 'yellow', 'red')),
        granted_at_seq   INTEGER NOT NULL,
        granted_by       TEXT,
        granted_at       TEXT NOT NULL,
        revoked_at_seq   INTEGER,
        revoked_at       TEXT,
        revoked_by       TEXT,
        reason           TEXT,
        PRIMARY KEY (instance_id, tool_name, tool_version),
        FOREIGN KEY (instance_id)
            REFERENCES agents(instance_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_catalog_grants_active "
        "ON agent_catalog_grants(instance_id) "
        "WHERE revoked_at_seq IS NULL;",
    # ADR-0063 T6 (Burst 255): reality_anchor_corrections.
    # One row per unique hallucination ever caught by either the
    # T3 dispatcher gate or the T5 conversation hook. Keyed on
    # the sha256 of the normalized claim. ``repetition_count``
    # bumps on every repeat hit so an operator can answer
    # "which agents keep making the same wrong claim?" without
    # walking the audit chain manually.
    #
    # ``last_decision`` is the dispatcher's verdict on the most
    # recent occurrence: "refused" (CRITICAL) or "warned"
    # (HIGH/MEDIUM/LOW). Surface ∈ {dispatcher, conversation}
    # tells the operator which integration point caught it.
    """
    CREATE TABLE IF NOT EXISTS reality_anchor_corrections (
        claim_hash          TEXT PRIMARY KEY,
        canonical_claim     TEXT NOT NULL,
        contradicts_fact_id TEXT NOT NULL,
        worst_severity      TEXT NOT NULL,
        first_seen_at       TEXT NOT NULL,
        last_seen_at        TEXT NOT NULL,
        repetition_count    INTEGER NOT NULL DEFAULT 1,
        last_agent_dna      TEXT,
        last_instance_id    TEXT,
        last_decision       TEXT NOT NULL,
        last_surface        TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_reality_anchor_corrections_fact "
        "ON reality_anchor_corrections(contradicts_fact_id);",
    "CREATE INDEX IF NOT EXISTS idx_reality_anchor_corrections_agent "
        "ON reality_anchor_corrections(last_agent_dna);",
    "CREATE INDEX IF NOT EXISTS idx_reality_anchor_corrections_count "
        "ON reality_anchor_corrections(repetition_count DESC);",
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
    # memory_consents and memory_contradictions both reference
    # memory_entries(entry_id) — clear first. memory_contradictions
    # references entries via TWO FKs (earlier_ + later_entry_id), so it
    # MUST be cleared before memory_entries to avoid orphaned references
    # during the truncate sweep. v11 (ADR-0027-amendment §7.3) addition.
    "memory_consents",
    "memory_contradictions",
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
    # ADR-0063 T6 (B255): correction memory. Standalone — no FKs
    # to other tables, so order vs. siblings doesn't matter. Cleared
    # on rebuild because corrections are runtime governance state,
    # not artifact state. Operator's ground_truth.yaml is the
    # canonical truth source; the corrections table is a derived
    # view that rebuilds naturally from the chain on resumed traffic.
    "reality_anchor_corrections",
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
    # v10 → v11: ADR-0027-amendment §7 epistemic memory metadata. Adds
    # three columns to memory_entries (claim_type, confidence,
    # last_challenged_at) and one new memory_contradictions table. Pure
    # addition — old v10 rows land at claim_type='observation',
    # confidence='medium', last_challenged_at=NULL via the column
    # DEFAULTs. Operator-driven re-classification post-migration goes
    # through memory_reclassify.v1 (deferred to ADR-0027-am T7).
    #
    # SQLite ALTER TABLE ADD COLUMN with a CHECK constraint is supported
    # since 3.25 (way past our minimum); the CHECK lives on the column
    # definition exactly like the bootstrap DDL above.
    11: (
        "ALTER TABLE memory_entries ADD COLUMN claim_type TEXT NOT NULL "
            "DEFAULT 'observation' CHECK (claim_type IN ("
                "'observation', 'user_statement', 'agent_inference', "
                "'preference', 'promise', 'external_fact'"
            "));",
        "ALTER TABLE memory_entries ADD COLUMN confidence TEXT NOT NULL "
            "DEFAULT 'medium' CHECK (confidence IN ('low', 'medium', 'high'));",
        "ALTER TABLE memory_entries ADD COLUMN last_challenged_at TEXT;",
        "CREATE INDEX IF NOT EXISTS idx_memory_claim_type ON memory_entries(claim_type);",
        "CREATE INDEX IF NOT EXISTS idx_memory_last_challenged ON memory_entries(last_challenged_at) "
            "WHERE last_challenged_at IS NOT NULL;",
        # New table for many-to-many contradictions. CHECK enum mirrors
        # the bootstrap DDL above. FKs on both entry_id columns; lifetime
        # tied to memory_entries (purging an entry orphans its
        # contradictions, which the operator-driven purge path knows
        # to clean up).
        """
        CREATE TABLE IF NOT EXISTS memory_contradictions (
            contradiction_id   TEXT PRIMARY KEY,
            earlier_entry_id   TEXT NOT NULL,
            later_entry_id     TEXT NOT NULL,
            contradiction_kind TEXT NOT NULL CHECK (contradiction_kind IN (
                'direct', 'updated', 'qualified', 'retracted'
            )),
            detected_at        TEXT NOT NULL,
            detected_by        TEXT NOT NULL,
            resolved_at        TEXT,
            resolution_summary TEXT,
            FOREIGN KEY (earlier_entry_id) REFERENCES memory_entries(entry_id),
            FOREIGN KEY (later_entry_id)   REFERENCES memory_entries(entry_id)
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_contradictions_earlier ON memory_contradictions(earlier_entry_id);",
        "CREATE INDEX IF NOT EXISTS idx_contradictions_later   ON memory_contradictions(later_entry_id);",
        "CREATE INDEX IF NOT EXISTS idx_contradictions_unresolved ON memory_contradictions(resolved_at) "
            "WHERE resolved_at IS NULL;",
    ),
    # v11 → v12: ADR-0036 T6 — ratification dial. Adds the
    # ``flagged_state`` column to memory_contradictions with the four-
    # value enum from ADR-0036 §4.3. Pure addition; existing rows
    # land at 'flagged_unreviewed' via the column DEFAULT (which is
    # the right semantic — pre-T6 contradictions weren't reviewed yet).
    # Operators ratify via the future memory_set_contradiction_state
    # admin path; the recall surface (T7) filters flagged_rejected by
    # default.
    12: (
        "ALTER TABLE memory_contradictions ADD COLUMN flagged_state TEXT "
            "NOT NULL DEFAULT 'flagged_unreviewed' "
            "CHECK (flagged_state IN ("
                "'flagged_unreviewed', 'flagged_confirmed', "
                "'flagged_rejected', 'auto_resolved'"
            "));",
        "CREATE INDEX IF NOT EXISTS idx_contradictions_state "
            "ON memory_contradictions(flagged_state) "
            "WHERE flagged_state = 'flagged_unreviewed';",
    ),
    # v12 → v13 (ADR-0041 T5, Burst 90): scheduler persistence.
    # Pure addition — new table only, no existing rows touched. On
    # daemon restart the scheduler reads this table to rebuild
    # in-memory ScheduledTask.state, so consecutive_failures /
    # last_run_at / circuit_breaker_open all survive across crashes
    # and intentional restarts. See the DDL_STATEMENTS block above
    # for the schema and the docstring explaining why each column
    # exists.
    13: (
        """
        CREATE TABLE IF NOT EXISTS scheduled_task_state (
            task_id                  TEXT PRIMARY KEY,
            last_run_at              TEXT,
            next_run_at              TEXT,
            consecutive_failures     INTEGER NOT NULL DEFAULT 0,
            circuit_breaker_open     INTEGER NOT NULL DEFAULT 0,
            total_runs               INTEGER NOT NULL DEFAULT 0,
            total_successes          INTEGER NOT NULL DEFAULT 0,
            total_failures           INTEGER NOT NULL DEFAULT 0,
            last_failure_reason      TEXT,
            last_run_outcome         TEXT,
            updated_at               TEXT NOT NULL
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_scheduled_task_state_breaker "
            "ON scheduled_task_state(circuit_breaker_open) "
            "WHERE circuit_breaker_open = 1;",
    ),
    # v13 → v14 (ADR-0043 follow-up #2, Burst 113): plugin grants.
    # Pure addition — new table only, no existing rows touched. The
    # immutable-constitution invariant is preserved: this table
    # AUGMENTS the constitution's allowed_mcp_servers list rather
    # than mutating it. Effective set at dispatch time is the union
    # of (constitution-declared) ∪ (active grants here).
    #
    # ``trust_tier`` is forward-compatible storage for ADR-0045
    # (Agent Posture / Trust-Light System). Burst 113 records the
    # value; ADR-0045's PostureGateStep will start consulting it.
    14: (
        """
        CREATE TABLE IF NOT EXISTS agent_plugin_grants (
            instance_id      TEXT NOT NULL,
            plugin_name      TEXT NOT NULL,
            trust_tier       TEXT NOT NULL DEFAULT 'yellow'
                             CHECK (trust_tier IN ('green', 'yellow', 'red')),
            granted_at_seq   INTEGER NOT NULL,
            granted_by       TEXT,
            granted_at       TEXT NOT NULL,
            revoked_at_seq   INTEGER,
            revoked_at       TEXT,
            revoked_by       TEXT,
            reason           TEXT,
            PRIMARY KEY (instance_id, plugin_name),
            FOREIGN KEY (instance_id)
                REFERENCES agents(instance_id) ON DELETE CASCADE
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_plugin_grants_active "
            "ON agent_plugin_grants(instance_id) "
            "WHERE revoked_at_seq IS NULL;",
    ),
    # v14 → v15 (ADR-0045 T1, Burst 114): per-agent posture.
    # ADD COLUMN with DEFAULT 'yellow' is safe — existing rows get
    # the default which matches their de-facto behavior pre-Burst-114
    # (gating is per-tool config; yellow doesn't override that).
    # CHECK constraint matches the DDL_STATEMENTS shape so reopen
    # path matches fresh-install path.
    15: (
        "ALTER TABLE agents ADD COLUMN posture TEXT NOT NULL "
            "DEFAULT 'yellow' "
            "CHECK (posture IN ('green', 'yellow', 'red'));",
        "CREATE INDEX IF NOT EXISTS idx_agents_posture "
            "ON agents(posture);",
    ),
    # v15 → v16 (ADR-0054 T1, Burst 178): memory_procedural_shortcuts.
    # Pure addition — new table only, no existing rows touched. The
    # immutable-constitution invariant is preserved: this table stores
    # per-instance state (what the agent knows / has seen), not
    # identity (DNA + constitution_hash). Per-tranche feature flag
    # (FSF_PROCEDURAL_SHORTCUT_ENABLED) defaults off; the table can
    # exist empty without affecting any existing behavior.
    #
    # Effective dispatch path:
    #   constitution_hash + DNA stay stable
    #   procedural shortcuts (this table) augment what the assistant
    #     can resolve quickly without firing llm_think
    #   audit chain captures every shortcut hit (tool_call_shortcut
    #     event type, ADR-0054 T4)
    16: (
        """
        CREATE TABLE IF NOT EXISTS memory_procedural_shortcuts (
            shortcut_id          TEXT PRIMARY KEY,
            instance_id          TEXT NOT NULL,
            created_at           TEXT NOT NULL,
            last_matched_at      TEXT,
            last_matched_seq     INTEGER,
            situation_text       TEXT NOT NULL,
            situation_embedding  BLOB NOT NULL,
            action_kind          TEXT NOT NULL
                                 CHECK (action_kind IN ('response', 'tool_call', 'no_op')),
            action_payload       TEXT NOT NULL,
            success_count        INTEGER NOT NULL DEFAULT 0,
            failure_count        INTEGER NOT NULL DEFAULT 0,
            learned_from_seq     INTEGER NOT NULL,
            learned_from_kind    TEXT NOT NULL
                                 CHECK (learned_from_kind IN ('auto', 'operator_tagged')),
            FOREIGN KEY (instance_id)
                REFERENCES agents(instance_id) ON DELETE CASCADE
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_psh_instance "
            "ON memory_procedural_shortcuts(instance_id);",
    ),
    # v16 → v17 (ADR-0060 T1, Burst 219): agent_catalog_grants.
    # Runtime grants of catalog-tool access without rebirthing the agent.
    # Mirror of agent_plugin_grants (Burst 113) generalized to catalog
    # tools keyed by (instance_id, tool_name, tool_version). The
    # constitution_hash is immutable per agent (CLAUDE.md architectural
    # invariant) — this table is consulted alongside the constitution,
    # not in place of it. Effective at-dispatch decision:
    #   constitution lists tool        → use constitution's resolved constraints
    #   not listed, grant active       → use catalog defaults (T2 wiring)
    #   not listed, no grant           → refuse tool_not_in_constitution
    #
    # trust_tier defaults to yellow per ADR-0060 D4 — operators must
    # explicitly pass green for fully-autonomous grants. The CHECK
    # constraint matches agent_plugin_grants so the GrantPolicy helper
    # (queued T4) treats both grant types uniformly.
    17: (
        """
        CREATE TABLE IF NOT EXISTS agent_catalog_grants (
            instance_id      TEXT NOT NULL,
            tool_name        TEXT NOT NULL,
            tool_version     TEXT NOT NULL,
            trust_tier       TEXT NOT NULL DEFAULT 'yellow'
                             CHECK (trust_tier IN ('green', 'yellow', 'red')),
            granted_at_seq   INTEGER NOT NULL,
            granted_by       TEXT,
            granted_at       TEXT NOT NULL,
            revoked_at_seq   INTEGER,
            revoked_at       TEXT,
            revoked_by       TEXT,
            reason           TEXT,
            PRIMARY KEY (instance_id, tool_name, tool_version),
            FOREIGN KEY (instance_id)
                REFERENCES agents(instance_id) ON DELETE CASCADE
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_catalog_grants_active "
            "ON agent_catalog_grants(instance_id) "
            "WHERE revoked_at_seq IS NULL;",
    ),
    # v17 → v18 (ADR-0053 T1, Burst 235): per-tool plugin grants.
    #
    # Extends agent_plugin_grants with an optional tool_name column.
    # NULL tool_name = plugin-level grant (ADR-0043 original semantic,
    # byte-for-byte compatible). Non-NULL tool_name = per-tool grant
    # covering ONLY that one tool. ADR-0053 D3 dispatcher resolution
    # is specificity-wins: per-tool beats plugin-level when both
    # exist.
    #
    # SQLite can't ALTER TABLE to change a PRIMARY KEY in place. We
    # do a standard table-rebuild: build the new shape, copy rows
    # (existing rows get tool_name = NULL → plugin-level by the new
    # semantic, preserving every operator's effective grants), drop
    # the old table, rename the new one in. Indexes are recreated
    # explicitly because SQLite drops them with the old table.
    #
    # No incoming FKs reference agent_plugin_grants (only outgoing
    # FK to agents.instance_id), so the rebuild is contained.
    # CASCADE on agents stays correct after rename.
    #
    # Defense in depth: a pre-v18 daemon reading a v18-shape table
    # would see the unfamiliar tool_name column and would either
    # ignore it (column-position-stable SELECT) or fail loudly
    # (SELECT *). Forest's registry uses named-column SELECTs, so a
    # downgrade reads cleanly with tool_name silently dropped.
    18: (
        # 1. Build the new table with the v18 shape.
        """
        CREATE TABLE agent_plugin_grants_v18 (
            instance_id      TEXT NOT NULL,
            plugin_name      TEXT NOT NULL,
            tool_name        TEXT,
            trust_tier       TEXT NOT NULL DEFAULT 'yellow'
                             CHECK (trust_tier IN ('green', 'yellow', 'red')),
            granted_at_seq   INTEGER NOT NULL,
            granted_by       TEXT,
            granted_at       TEXT NOT NULL,
            revoked_at_seq   INTEGER,
            revoked_at       TEXT,
            revoked_by       TEXT,
            reason           TEXT,
            PRIMARY KEY (instance_id, plugin_name, tool_name),
            FOREIGN KEY (instance_id)
                REFERENCES agents(instance_id) ON DELETE CASCADE
        );
        """,
        # 2. Copy every existing row as a plugin-level grant
        #    (tool_name = NULL → matches the ADR-0043 semantic).
        """
        INSERT INTO agent_plugin_grants_v18
            (instance_id, plugin_name, tool_name, trust_tier,
             granted_at_seq, granted_by, granted_at,
             revoked_at_seq, revoked_at, revoked_by, reason)
        SELECT
            instance_id, plugin_name, NULL, trust_tier,
            granted_at_seq, granted_by, granted_at,
            revoked_at_seq, revoked_at, revoked_by, reason
        FROM agent_plugin_grants;
        """,
        # 3. Drop the old table.
        "DROP TABLE agent_plugin_grants;",
        # 4. Rename the new table in.
        "ALTER TABLE agent_plugin_grants_v18 RENAME TO agent_plugin_grants;",
        # 5. Recreate the active-row index from v14 (DROP TABLE
        #    discards it along with the old table).
        "CREATE INDEX IF NOT EXISTS idx_plugin_grants_active "
            "ON agent_plugin_grants(instance_id) "
            "WHERE revoked_at_seq IS NULL;",
        # 6. Create the new partial-unique index that enforces
        #    "at most one plugin-level grant per (agent, plugin)"
        #    in the NULL-tool_name partition. The PRIMARY KEY can't
        #    do this on its own — SQLite allows multiple NULL
        #    combinations in a composite PK.
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_plugin_grants_plugin_level "
            "ON agent_plugin_grants(instance_id, plugin_name) "
            "WHERE tool_name IS NULL;",
    ),
    # v18 → v19 (ADR-0049 T4, Burst 243): per-agent ed25519 public
    # key column on agents.
    #
    # ADR-0049 closes the audit-chain tamper-evident-vs-tamper-proof
    # gap with per-event ed25519 signatures. The public key lives
    # here for runtime lookup (chain replay needs O(1) access by
    # agent_dna → instance_id → public_key); private keys live in
    # the ADR-0052 secrets store via AgentKeyStore. T4 ships the
    # storage substrate. T5 wires sign-on-emit; T6 wires verify-on-
    # replay.
    #
    # Nullable for back-compat: pre-v19 agents stay NULL, and the
    # verifier (ADR-0049 D5) treats their chain entries as "legacy
    # unsigned" — passes hash-chain check, skips signature check.
    # New agents born on v19+ get a public_key populated at birth.
    #
    # No index — the agents table is small (tens to low thousands
    # of rows even for power operators) and lookups go through
    # instance_id (primary key) or dna (already indexed).
    19: (
        "ALTER TABLE agents ADD COLUMN public_key TEXT;",
    ),
    # v19 → v20: ADR-0063 T6 Reality Anchor correction memory.
    # Per ADR-0063 D7. One row per unique hallucinated claim ever
    # caught by either the dispatcher gate (T3) or the
    # conversation hook (T5). repetition_count bumps on every
    # repeat hit; emits reality_anchor_repeat_offender once
    # count crosses 2.
    20: (
        """
        CREATE TABLE IF NOT EXISTS reality_anchor_corrections (
            claim_hash          TEXT PRIMARY KEY,
            canonical_claim     TEXT NOT NULL,
            contradicts_fact_id TEXT NOT NULL,
            worst_severity      TEXT NOT NULL,
            first_seen_at       TEXT NOT NULL,
            last_seen_at        TEXT NOT NULL,
            repetition_count    INTEGER NOT NULL DEFAULT 1,
            last_agent_dna      TEXT,
            last_instance_id    TEXT,
            last_decision       TEXT NOT NULL,
            last_surface        TEXT NOT NULL
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_reality_anchor_corrections_fact "
            "ON reality_anchor_corrections(contradicts_fact_id);",
        "CREATE INDEX IF NOT EXISTS idx_reality_anchor_corrections_agent "
            "ON reality_anchor_corrections(last_agent_dna);",
        "CREATE INDEX IF NOT EXISTS idx_reality_anchor_corrections_count "
            "ON reality_anchor_corrections(repetition_count DESC);",
    ),
}
