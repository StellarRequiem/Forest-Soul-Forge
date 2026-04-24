# ADR-0006 — SQLite Registry as a Convenience Index over Canonical Artifacts

- **Status:** Accepted
- **Date:** 2026-04-23
- **Supersedes:** —
- **Related:** ADR-0002 (DNA/lineage), ADR-0004 (constitution builder), ADR-0005 (audit chain)

## Context

Phase 2 produced canonical per-agent artifacts on the filesystem — `*.soul.md` (YAML frontmatter + prose), `*.constitution.yaml`, and a single hash-linked `audit/chain.jsonl`. These files are the source of truth and are designed to be readable, diffable, version-controllable, and tamper-evident.

What they are **not** is queryable. The Phase 3 frontend (ADR-0007) needs answers like:

- "List every agent I've ever birthed with role `network_watcher`."
- "Show the full descendant tree of agent `34d7b558476e`."
- "Every audit event in the last 24 hours, joined to the agent that caused it."
- "Which agents are currently in status `active`?"

Re-parsing YAML frontmatter and walking the JSONL every request is viable at 10 agents and catastrophic at 10,000. A naive third-party reviewer would reach for a database.

Grok's handoff notes (parsed 2026-04-23) proposed a full SQLite schema — `agents`, `agent_capabilities`, `tools`, `agent_lineage` (closure table), and `audit_log`. The notes implicitly cast SQLite as canonical. That collides with ADR-0005's tamper-evident chain: a plain SQL table can be mutated with one `UPDATE` statement and leaves no hash-chain evidence, throwing away the property ADR-0005 was built to provide.

We need to pick a layering story *explicitly* rather than let the registry drift into "canonical by accident."

## Decision

### Canonical vs derived

- **Canonical sources of truth** (unchanged):
  - `audit/chain.jsonl` — hash-linked append-only event log.
  - `<stem>.soul.md` — per-agent soul with YAML frontmatter.
  - `<stem>.constitution.yaml` — per-agent constitution.
- **Derived index** (new in Phase 3):
  - `registry.sqlite` (path configurable; default `state/registry.sqlite`). Populated by a one-way sync from the canonical artifacts. Can be **deleted and rebuilt** from the canonical set at any time without data loss.

If the registry and the artifacts disagree, **the artifacts win.** A `rebuild_registry()` function scans the artifacts and rewrites the SQLite tables from scratch. This is the single invariant that holds the whole design together.

### Dual identity: DNA + instance_id

Grok's notes called for `agent_id TEXT PRIMARY KEY` as a UUID. Forest Soul Forge already has deterministic **DNA** (SHA-256 of canonical TraitProfile) from ADR-0002. The right resolution is to keep both, with distinct meanings:

| Field         | Type                      | Meaning                                                              | Stable across… |
|---------------|---------------------------|----------------------------------------------------------------------|---------------------------------------|
| `dna`         | 12-char hex (short DNA)   | *What the agent is* — reproducible from the trait profile alone.     | Identical profiles → identical DNA.   |
| `dna_full`    | 64-char hex (full SHA-256)| Same meaning, full-precision form.                                   | Same as above.                        |
| `instance_id` | UUID v4 (string)          | *Which specific incarnation this is.* Minted once at birth.          | Only this row. Never reused.          |

Class vs object. Two agents born from the same profile share DNA but have different `instance_id`. Lineage edges use `instance_id` (not DNA), because two distinct incarnations of the same profile are distinct family-tree nodes.

### Schema (v1)

```sql
-- Canonical per-agent registry row. One row per birth event.
CREATE TABLE agents (
    instance_id      TEXT PRIMARY KEY,                -- UUID v4
    dna              TEXT NOT NULL,                   -- 12-char short DNA
    dna_full         TEXT NOT NULL,                   -- 64-char full DNA
    role             TEXT NOT NULL,
    agent_name       TEXT NOT NULL,
    parent_instance  TEXT,                            -- NULL for root agents
    owner_id         TEXT,                            -- Nullable. Reserved for multi-tenant; "local" in solo mode.
    model_name       TEXT,                            -- e.g. "claude-opus-4-7", "phi-3.5-mini-3.8b"
    model_version    TEXT,                            -- Free-form provider version string
    soul_path        TEXT NOT NULL,                   -- Relative path from repo root
    constitution_path TEXT NOT NULL,                  -- Relative path from repo root
    constitution_hash TEXT NOT NULL,                  -- For drift detection
    created_at       TEXT NOT NULL,                   -- ISO-8601 UTC from audit chain
    status           TEXT NOT NULL DEFAULT 'active',  -- active | archived | suspended
    FOREIGN KEY (parent_instance) REFERENCES agents(instance_id)
);

CREATE INDEX idx_agents_dna        ON agents(dna);
CREATE INDEX idx_agents_role       ON agents(role);
CREATE INDEX idx_agents_status     ON agents(status);
CREATE INDEX idx_agents_parent     ON agents(parent_instance);

-- Closure table for fast ancestor / descendant queries.
-- Every agent has a self-edge at depth 0, plus one edge per ancestor.
-- Rebuilt atomically on every agent insert from soul-frontmatter lineage.
CREATE TABLE agent_ancestry (
    instance_id  TEXT NOT NULL,
    ancestor_id  TEXT NOT NULL,
    depth        INTEGER NOT NULL,                    -- 0 = self, 1 = parent, 2 = grandparent, ...
    PRIMARY KEY (instance_id, ancestor_id),
    FOREIGN KEY (instance_id) REFERENCES agents(instance_id),
    FOREIGN KEY (ancestor_id) REFERENCES agents(instance_id)
);

CREATE INDEX idx_ancestry_ancestor ON agent_ancestry(ancestor_id);

-- Audit events, mirrored from audit/chain.jsonl. Index only — JSONL remains canonical.
-- Populated by sync. Any mutation to this table without a matching JSONL line is
-- a bug and the next rebuild will overwrite it.
CREATE TABLE audit_events (
    seq          INTEGER PRIMARY KEY,                 -- Matches JSONL seq
    timestamp    TEXT NOT NULL,
    agent_dna    TEXT,                                -- Nullable for system events
    instance_id  TEXT,                                -- Resolved from dna + timing; may be NULL if pre-registry
    event_type   TEXT NOT NULL,
    event_json   TEXT NOT NULL,                       -- Raw event_data as JSON string
    entry_hash   TEXT NOT NULL,                       -- For spot-checking against JSONL
    FOREIGN KEY (instance_id) REFERENCES agents(instance_id)
);

CREATE INDEX idx_audit_ts          ON audit_events(timestamp);
CREATE INDEX idx_audit_type        ON audit_events(event_type);
CREATE INDEX idx_audit_dna         ON audit_events(agent_dna);
CREATE INDEX idx_audit_instance    ON audit_events(instance_id);

-- Capabilities and tools tables — STUB ONLY for v1.
-- Schema reserved; no writer code until Phase 4 runtime lands.
-- Keeping them here means we don't invalidate registries later.
CREATE TABLE agent_capabilities (
    instance_id  TEXT NOT NULL,
    capability   TEXT NOT NULL,
    level        INTEGER,
    acquired_at  TEXT,
    PRIMARY KEY (instance_id, capability),
    FOREIGN KEY (instance_id) REFERENCES agents(instance_id)
);

CREATE TABLE tools (
    tool_id           TEXT PRIMARY KEY,                -- UUID
    instance_id       TEXT NOT NULL,                   -- Who owns/created this tool
    name              TEXT NOT NULL,
    description       TEXT,
    parameters_json   TEXT,
    code_snippet      TEXT,
    created_at        TEXT NOT NULL,
    is_inherited      INTEGER NOT NULL DEFAULT 0,      -- SQLite boolean
    parent_tool_id    TEXT,
    FOREIGN KEY (instance_id) REFERENCES agents(instance_id),
    FOREIGN KEY (parent_tool_id) REFERENCES tools(tool_id)
);

-- Schema metadata
CREATE TABLE registry_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT INTO registry_meta (key, value) VALUES ('schema_version', '1');
INSERT INTO registry_meta (key, value) VALUES ('canonical_contract', 'artifacts-authoritative');
```

### Deviations from Grok's proposed schema

| Grok proposed                        | Decision here                             | Reason                                                                                                                                                 |
|--------------------------------------|-------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------|
| `agents.agent_id` = single UUID      | Split into `dna` + `instance_id`           | DNA is already deterministic and load-bearing; can't lose it. UUID handles per-incarnation identity.                                                    |
| `audit_log` as canonical SQL table   | `audit_events` as an *index* of the JSONL | ADR-0005 shipped tamper-evidence; we keep it. SQL can be rebuilt; the chain cannot be forged without the chain code.                                    |
| `owner_fingerprint NOT NULL`         | `owner_id` nullable, default `NULL`       | Local-first, Apache 2.0, one-operator. Column reserved for future multi-tenant; doesn't need to be populated today.                                    |
| `agent_capabilities` with real data  | Schema-only stub                          | No runtime yet. Landing the schema now avoids a migration when capabilities actually arrive.                                                            |
| `tools` with real data               | Schema-only stub                          | Same as capabilities.                                                                                                                                  |
| `agent_lineage` (ancestor-only)      | `agent_ancestry` including self at depth 0 | Including the self-edge simplifies "all descendants of X" to a single equality query instead of a UNION.                                              |

### Sync path

One-way: `artifacts → SQLite`.

1. `bootstrap()` — if no DB exists, create schema and leave tables empty.
2. `rebuild_from_artifacts(root_dir)` — scan `examples/` (or configured agent directories) for `*.soul.md`, scan `audit/chain.jsonl`, rewrite all tables atomically inside a single transaction. Idempotent.
3. `register_birth(soul_doc, constitution, audit_entry)` — called by the daemon immediately after an atomic "soul+constitution+audit append" transaction. Inserts into `agents`, populates `agent_ancestry`, appends to `audit_events`. Fails loudly on FK violations rather than silently corrupting.
4. `register_audit_event(entry)` — append-only mirror of the audit chain. Called after every `AuditChain.append()`.
5. `rebuild_registry()` (operator command) — drops and rebuilds the DB from canonical artifacts. Survivable escape hatch for any sync bug.

The registry never writes to the filesystem artifacts. Flow is always:

```
write artifact → append audit chain → update registry
```

If the process crashes mid-sequence, the next `rebuild_registry()` brings the DB back into agreement with the artifacts. Audit chain has its own consistency story (ADR-0005). Soul + constitution writes are per-file, so a crash between them leaves a dangling soul without a constitution — the registry detects this (FK fails on `constitution_path`) and the operator either deletes the orphan soul or regenerates the constitution.

### What the registry gains us

- **Sub-millisecond queries** for every frontend view listed in the context.
- **Referential integrity** for lineage — can't insert an agent with a nonexistent `parent_instance`.
- **Forward-compat schema** — capabilities and tools have their home; the registry won't need a v2 migration when Phase 4 lands.
- **Drift detection** — the audit chain's hash and the registry's mirror can be cross-checked. Any mismatch is a tampering signal.

## Consequences

### Positive

- Frontend queries become trivial SQL joins.
- Canonical artifacts keep their human-readable, git-friendly, tamper-evident properties — we layered on top, didn't replace.
- Rebuild-from-scratch is a real option, not an aspirational one.

### Negative

- Two data locations to keep in sync. Sync bugs will exist; `rebuild_registry()` is the mitigation.
- `instance_id` is new vocabulary the docs have to teach alongside `dna`. Failure mode: users assume they're interchangeable and confuse themselves.
- SQLite locks on concurrent writes. The daemon is the sole writer — we enforce this architecturally rather than with locks.

### Neutral

- Registry file grows linearly with agent count. At 1M agents the DB is still small (low hundreds of MB). No archival strategy needed for v1.

## Open questions

1. **Do we ship a CLI `fsf registry rebuild` command in this phase, or wait until it's actually broken and add it reactively?** Leaning: ship it now — cheap, and operators will want it the first time they mess with the files manually.
2. **How do we resolve `instance_id` for audit events minted before the registry existed?** Phase 2 examples were written without `instance_id`. On first rebuild, we either (a) leave `audit_events.instance_id` NULL for those rows, (b) synthesize deterministic instance_ids from (dna, created_at), or (c) delete and regenerate the Phase 2 examples. Leaning: (a) — NULLs are honest.
3. **Should `agents.status` transitions be audit events?** Currently no — `status` is in the registry only. If we want the status history auditable, `status_changed` needs adding to ADR-0005's event type enum.

## References

- ADR-0002: Agent DNA and Lineage
- ADR-0004: Constitution builder
- ADR-0005: Audit chain
- Grok handoff notes, 2026-04-23 (registry schema proposal)
