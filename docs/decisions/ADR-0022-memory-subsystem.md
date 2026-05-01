# ADR-0022 — Memory subsystem

- **Status:** Accepted (promoted 2026-04-30 — Phase A audit §A-2; see docs/audits/2026-04-30-comprehensive-repo-audit.md). Memory subsystem — v0.1 + v0.2 (lineage + consented + disclosed) shipped.
- **Date:** 2026-04-25
- **Supersedes:** —
- **Related:** ADR-0005 (audit chain), ADR-0006 (registry as index over artifacts), ADR-0008 (local-first model provider — the privacy floor this ADR enforces), ADR-0017 (LLM-enriched soul.md narrative — the Voice depends on consolidated memory in the long run), ADR-0018 (tool catalog — a `memory_recall` tool wraps this subsystem), ADR-0020 (character sheet — `memory` section), ADR-0021 (genres carry `memory_pattern`).

## Context

An agent that resets its mind every session is a useful tool but not an agent. The Forge's mission — agents the operator can spawn for a job, including jobs that span days or weeks — requires persistent state per agent. A therapist who can't remember last week's session can't do therapy. A long-running investigator who can't remember the baselines it's seen has to rebuild context from scratch every shift. A companion who can't remember the user's name is a chatbot.

Today the Forge persists:
- The agent itself (soul.md, constitution.yaml — what the agent IS)
- The audit chain (what happened, append-only, tamper-evident)
- The registry (derived index over both)

None of those are memory in the agent sense. The audit chain has *events the system observed*; an agent's memory is *what the agent itself retained*. They overlap (a memory write might also produce an audit event) but they're not the same thing — audit is global and tamper-evident; memory is per-agent and editable (via consolidation, summarization, forgetting).

The privacy stakes are high. ADR-0008 commits to local-first inference. Memory inherits and amplifies that commitment: a Companion-class agent (therapy, accessibility) accumulates exactly the kind of state that must not silently leave the machine. **Memory subsystem decisions are privacy decisions.** Get this wrong and the local-first guarantee from ADR-0008 becomes hollow.

This ADR captures the design space without implementing it. The implementation is real engineering — multiple files per agent, retention policies, consolidation jobs, possibly vector indices. The implementation tranche list at the end breaks it into shippable pieces.

## Decision

### Three-layer memory model

Memory is structured into three layers with different retention windows, different storage backends, and different access patterns. The model is loosely inspired by cognitive science (working / episodic / semantic memory) but pragmatic: each layer earns its keep by serving a specific class of operator question.

```
working:        the current session's state, like an LLM's context window.
                 Bounded by token count. Cleared on session end OR aged out
                 by a sliding window. Lives in RAM during the session,
                 spilled to a checkpoint file at session boundaries.

episodic:        specific events the agent retained. Each entry is timestamped,
                 typed, and indexed. Configurable retention window (default
                 30 days, per-genre). Stored as JSONL on disk; indexed in
                 a per-agent SQLite for fast retrieval.

consolidated:    distilled knowledge the agent will likely need across many
                 future sessions. Updated by periodic consolidation jobs:
                 the agent (or a Guardian-class auxiliary) reflects on
                 expiring episodic entries, produces a structured summary,
                 writes it to consolidated. Stored as Markdown + JSONL pair
                 (markdown for readability, JSONL for structured access).
```

The layers are distinct files on disk, distinct schemas, and distinct access methods. Promotion (working → episodic, episodic → consolidated) is explicit and audit-logged; demotion (consolidated → forgotten, episodic → expired) requires explicit operator action OR retention-window expiration.

### On-disk layout

```
data/agent_memory/{instance_id}/
    working_checkpoint.jsonl       # last-session snapshot (RAM-backed at runtime)
    episodic.jsonl                  # append-only event log
    consolidated.md                 # human-readable distilled summary
    consolidated.jsonl              # structured form of the same content
    memory_index.sqlite             # rebuildable index — recency, type tags, free-text
```

Per-instance directory matches the soul/constitution pattern — one logical agent, one home. The bind-mount on `data/` (per the docker compose stack) keeps memory visible to the operator.

`memory_index.sqlite` is **derived** per ADR-0006: rebuildable from the JSONL files. Loss is recoverable; the JSONL files are canonical.

### Privacy contract — by genre

| Genre        | Frontier provider for memory ops? | Cross-agent read? | Cross-agent write? | Default retention |
| :----------- | :-------------------------------- | :---------------- | :----------------- | :---------------- |
| Observer     | yes                               | siblings only      | no                 | 30 days episodic  |
| Investigator | yes                               | lineage-up         | no                 | 90 days episodic  |
| Communicator | yes (for context)                 | recipients only    | no                 | 7 days episodic   |
| Actuator     | yes                               | no (isolation)     | no                 | 90 days episodic  |
| Guardian     | yes                               | reads any agent in same lineage (its job) | no | 365 days episodic |
| Researcher   | yes                               | shared knowledge graph (post-MVP) | no | 365 days episodic |
| **Companion** | **NO — local-only-provider floor** | **NO**           | **NO**             | indefinite consolidated, 30 days episodic |

Companion is the strict tier:
- Memory operations NEVER use a frontier provider, regardless of `FSF_DEFAULT_PROVIDER`. The constraint policy (ADR-0018 T2.5) gains a Companion-genre always-rule that refuses to route memory ops to anything but `local`.
- No cross-agent memory access at all. Each Companion agent's memory is a closed island. An operator who needs cross-agent consolidation runs it manually with explicit consent.

Other genres get progressively more permissive. The defaults are conservative; per-agent overrides are possible via constitution_override but each override is an audit chain event.

### Retention and forgetting

Three forgetting paths, all explicit:

1. **Time-based expiration.** Episodic entries past their retention window are batch-promoted to consolidated (as part of the consolidation job) OR dropped if no signal warrants consolidation. The consolidation job runs on a schedule (per-genre cadence — daily for short-retention, weekly for long-retention) AND on demand via a `POST /agents/{id}/memory/consolidate` endpoint.

2. **Operator-initiated forgetting.** `POST /agents/{id}/memory/forget` with a search query or specific entry hash. Adds a "forgotten" record to the audit chain (so the act of forgetting is itself remembered), removes the entry from episodic, optionally rewrites consolidated to remove derived facts. Compliance-friendly (GDPR-style erasure) without compromising the audit chain — the chain has "X was forgotten at T," not what X was.

3. **User-initiated forgetting** (Companion-class, post-MVP). The user-facing surface (interactive session) gets a "forget what we just discussed" affordance. Hits the same backend path as operator-initiated, with `actor: user` recorded.

### Read / Write API

Read endpoints (auth gated by `require_api_token` when configured):

```
GET /agents/{id}/memory/working        # current session state
GET /agents/{id}/memory/episodic?since=...&until=...&type=...&limit=...
GET /agents/{id}/memory/consolidated   # full markdown
GET /agents/{id}/memory/budget         # configured budget + current usage
```

Write endpoints (require_api_token AND require_writes_enabled):

```
POST /agents/{id}/memory/episodic      # body: {type, content, tags, ts?}
POST /agents/{id}/memory/promote       # body: {entry_hashes: [...]}  → episodic→consolidated
POST /agents/{id}/memory/consolidate   # trigger consolidation pass on demand
POST /agents/{id}/memory/forget        # body: {query | entry_hash, actor}
```

Working memory is special — it's session-scoped, so it has its own affordances:

```
POST /agents/{id}/memory/working/append
GET  /agents/{id}/memory/working
DELETE /agents/{id}/memory/working      # session boundary
```

### Audit chain integration

Every memory write produces a corresponding audit chain event. Event types:

```
memory_appended      # new entry written to episodic
memory_promoted      # episodic entries promoted to consolidated
memory_forgotten     # explicit forget — actor + scope + count
memory_consolidated  # consolidation job ran — stats only, no content
working_session_started / working_session_ended
```

The audit event payload carries the entry's hash + type + tag set, **NEVER the content**. Privacy floor: the audit chain is a global, tamper-evident, indefinitely-retained record; putting per-agent private content there would break the privacy contract. Auditors get verifiable presence ("the agent retained 17 episodic entries between T1 and T2, 4 of type X") without intrusion.

### Constitution.yaml and the character sheet

`constitution.yaml` gains a `memory_budget:` block per-agent — derived at birth from genre defaults, overridable via constitution_override. Shape:

```yaml
memory_budget:
  working_capacity_tokens: 4096       # depends on model context size
  episodic_capacity_entries: 10000
  episodic_retention_days: 30
  consolidated_capacity_entries: 1000
  cross_agent_read: lineage_up        # one of: none, siblings, lineage_up, lineage_any
  frontier_allowed: true              # false for Companion genre
```

The memory_budget IS in the constitution_hash. Two agents with the same trait profile but different memory budgets have different rulebook hashes — correct, since their effective state shape differs.

The character sheet (ADR-0020) `memory` section pulls from `GET /agents/{id}/memory/budget`, which reads constitution.yaml's `memory_budget` block + the runtime usage from `memory_index.sqlite`.

### Tool catalog: `memory_recall.v1`

ADR-0018's tool_catalog gets a new tool for agents that need to query their own memory:

```yaml
memory_recall.v1:
  name: memory_recall
  version: "1"
  description: |
    Retrieve entries from this agent's episodic or consolidated memory
    matching a query, time window, or tag set. Read-only with respect
    to memory state — recall does not append a new entry.
  side_effects: read_only       # reads agent's OWN memory; no external surface
  input_schema:
    type: object
    required: [scope]
    properties:
      scope: { type: string, enum: [episodic, consolidated, both], default: both }
      query: { type: string }
      since: { type: string, format: date-time }
      until: { type: string, format: date-time }
      tags: { type: array, items: { type: string } }
      limit: { type: integer, minimum: 1, maximum: 100, default: 20 }
  archetype_tags: [investigator, communicator, researcher, companion, guardian]
```

Note: `memory_recall.v1.side_effects = read_only` because reading an agent's own memory is not a network or filesystem effect from the agent's perspective. The constraint policy (ADR-0018 T2.5) doesn't gate it. **Cross-agent memory recall** would be a different tool with `side_effects: filesystem` and human-approval-required; that's deferred until the cross-agent reads are wired.

Symmetric write tools (`memory_append.v1`, `memory_promote.v1`) are deliberately NOT in the v1 catalog. Memory writes are operator-driven for now (via the daemon endpoints); auto-write tools land when ADR-0019 (runtime) defines how an agent decides to retain something.

## Consequences

**Upside:**

- **Agents become continuous.** A therapist remembers; a long-running investigator carries baseline knowledge; a companion knows the user. The product graduates from "stateless tool with a face" to "agent with continuity."
- **Privacy-by-design.** Companion's local-only-provider + no-cross-agent-read floor is structural, not procedural. The audit chain captures memory acts without capturing memory contents — verifiability without intrusion.
- **Forgetting is first-class.** GDPR-style erasure is supported by the architecture, not bolted on. Operators and (for Companion) users can request forgetting; the audit chain records the act without storing what was forgotten.
- **Compose-able with the rest of the system.** memory_budget joins constitution_hash; memory section joins character sheet; memory_recall tool joins the catalog. Memory is a citizen of the existing structure, not a parallel universe.
- **Local-first floor strengthens.** Companion-genre + local-only-provider for memory ops means the most sensitive agents (therapy, accessibility) cannot leak state to a frontier API even if the operator misconfigures the global default. ADR-0008's promise survives the addition.

**Downside:**

- **Disk footprint per agent grows.** Every active agent has a memory directory; over time, episodic.jsonl + consolidated artifacts accumulate. A long-running Companion could hit hundreds of MB. Mitigation: retention windows + consolidation; per-genre defaults that match the use case. We accept the cost.
- **Consolidation is a real engineering surface.** It's a job that runs, has failure modes (model unavailable mid-consolidation), and produces side effects (rewriting consolidated.md). The implementation needs careful concurrency handling and idempotency. v1 should run consolidation synchronously on demand and add a scheduled background path later.
- **Cross-agent reads are a future security surface.** "Lineage-up" reads (a child reads its parent's consolidated memory) are useful but introduce a path for a parent's data to flow into a child's session. The default permissive-up needs an audit event per cross-agent read, and the operator needs a way to revoke. v1 ships the contract; v2 adds the revoke.
- **Memory-induced drift.** An agent whose consolidated memory grows over months may drift from its original constitution — its retained beliefs may contradict its policies. This is a real risk and one v1 doesn't solve. Mitigation: a Guardian-class agent that periodically reviews consolidated memory against the constitution, flags conflicts, and (via approval) prunes them. Out of scope for this ADR; useful follow-on.

**Out of scope for this ADR:**

- **Vector embeddings for semantic search.** memory_index.sqlite is recency + tag + free-text in v1. Semantic search via local embedding model is a strong follow-on; defer until episodic.jsonl regularly exceeds 10k entries on a typical agent.
- **Cross-genre shared knowledge graphs** (Researcher-class collaboration). Researchers benefiting from each other's reading is an obvious pattern but introduces a multi-writer coordination problem. Defer.
- **Memory diffing across versions of the same agent.** When an agent is re-birthed (new constitution_hash), does its predecessor's memory carry over? Default: no — new instance_id, new memory directory. An explicit `--inherit-memory-from {prev_instance}` is plausible but introduces a privacy gotcha (previous memory carries forward). Defer with explicit path.
- **Memory backup / restore.** Operators can `cp -r data/agent_memory/...` today; a structured backup tool is downstream tooling.
- **Per-tool memory tagging.** "Tools the memory was retrieved by" is interesting but not v1.

## Open questions

1. **Working memory's spill-to-disk frequency.** Every entry, every N entries, every M seconds, only at session-end? **Lean session-end** for v1 — RAM is fine within a session, spill on session boundary. If session-end never fires (process crash), recover from audit chain entries (memory_appended events have entry hashes; entries themselves can be reconstructed from working RAM only if the process is alive).

2. **Consolidation prompt — who writes it?** Two options: (a) the agent itself prompts its model to summarize (uses its own provider, voice consistent); (b) a Guardian-class auxiliary handles consolidation (independent perspective, can flag drift). **Lean (a) for MVP, (b) as a future Guardian-class enhancement.** Doing (a) first means MVP doesn't depend on Guardian infrastructure being mature.

3. **Should memory_recall use the audit chain for queries against very old episodic entries?** When an entry is past retention and dropped from episodic.jsonl, but its hash is still in the audit chain, can recall return "this hash existed but content is gone"? **Yes** — be transparent about forgotten state rather than pretending it never existed. The recall response includes a "tombstoned" flag for hashes the audit chain knows about but no longer has content for.

4. **Token vs entry budget for working memory.** Token-based matches LLM context windows; entry-based is simpler to operate. **Lean tokens** because the model's context is the actual constraint; provide entry-based as a derived view in the budget endpoint.

5. **Schema versioning.** Memory file format will evolve. Each layer's JSONL gets a `schema_version` field on every entry; readers handle multiple versions; writers emit current version. Migration on read, not on write — same pattern as the audit chain ingest.

## Implementation tranches

- **T1** — disk layout + per-instance memory directory. Episodic JSONL append + read. Privacy-default config in constitution.yaml memory_budget block. No promotion or consolidation yet.
- **T2** — `GET /agents/{id}/memory/episodic` endpoint with filters (since/until/type/tag/limit). Audit chain integration: memory_appended events. Tests.
- **T3** — `POST /agents/{id}/memory/episodic` write endpoint, auth-gated. Audit chain visibility. Tests.
- **T4** — Working memory model: in-RAM session state + checkpoint on session-end. `memory/working` endpoints.
- **T5** — Consolidation pipeline: `POST /agents/{id}/memory/consolidate` triggers it; agent-prompts-its-own-model approach (open question 2 (a)). Writes consolidated.md + .jsonl. Audit chain memory_promoted + memory_consolidated events.
- **T6** — Forgetting: `POST /agents/{id}/memory/forget` endpoint. Episodic removal + consolidated rewrite + audit chain memory_forgotten event.
- **T7** — `memory_recall.v1` tool added to catalog. Tool-policy already covers it (read_only).
- **T8** — Cross-agent reads (lineage-up). Permission check via constitution_override + per-read audit event. Companion-genre always-deny rule.
- **T9** — Character sheet `memory` section populates from `GET /agents/{id}/memory/budget`. ADR-0020 milestone unlocks.
- **T10** — Background consolidation scheduler (per-genre cadence). On-demand T5 stays as the synchronous path.
- **T11** — Vector embedding index over episodic + consolidated (semantic recall). Local-only embedding model. Out of v1 unless episodic regularly exceeds 10k entries.

T1+T2+T3+T4 is the "agents can remember" milestone. T5+T6 are the consolidation + forgetting half. T7+T8 wire the rest of the system. T9 unblocks character sheet completeness. T10+T11 are polish and scale.

## v0.2 — cross-agent disclosure (added 2026-04-27)

The original ADR proposed a JSONL-based memory store. v0.1 implemented a simpler SQLite-table version (registry schema v6 — `memory_entries`) per practical constraints encountered in implementation. The data model below extends that v0.1 schema; the JSONL layout above remains the long-term direction for offline export + portability (ADR-0028).

v0.1 ships **private scope only**. v0.2 adds the cross-agent paths the runtime needs once Horizon 2 multi-agent coordination starts shipping.

### What v0.2 unlocks

- **Lineage scope**: a parent agent's memory entries (scope=`lineage`) become readable by its descendants, and vice-versa. Verified via the existing `agent_ancestry` closure table (ADR-0006). No consent flow needed because lineage is a structural relationship the operator already established at spawn time.
- **Per-event consent**: an agent disclosing a specific memory entry to a specific recipient (per-event consent per ADR-0027 §2). Stored as rows in a new `memory_consents` table.
- **Disclosure tool** (`memory_disclose.v1`): the explicit boundary-crossing path. Takes an entry id + recipient instance_id, copies the boundary-minimum data per ADR-0027 §4 (summary + entry_id + scope) onto the recipient's memory.
- **Cross-agent read audit**: every cross-agent read (lineage or consented) emits `memory_read` to the chain (ADR-0027 §6). Same-agent self-reads stay silent.

### Storage additions

```sql
-- ADR-0022 v0.2 — per-event consent grants. One row per
-- (entry_id, recipient_instance_id) authorization. Withdrawing
-- consent deletes the row + emits memory_consent_revoked.
CREATE TABLE memory_consents (
    entry_id           TEXT NOT NULL,
    recipient_instance TEXT NOT NULL,
    granted_at         TEXT NOT NULL,
    granted_by         TEXT NOT NULL,  -- operator id
    PRIMARY KEY (entry_id, recipient_instance),
    FOREIGN KEY (entry_id) REFERENCES memory_entries(entry_id)
);
CREATE INDEX idx_memory_consents_entry ON memory_consents(entry_id);
CREATE INDEX idx_memory_consents_recipient ON memory_consents(recipient_instance);

-- v0.2 also adds a derived "disclosure pointer" — a row on the
-- recipient's memory_entries that references (without copying) an
-- entry on the source agent. Same memory_entries table; new
-- columns gated by the schema bump.
ALTER TABLE memory_entries ADD COLUMN disclosed_from_entry TEXT;
ALTER TABLE memory_entries ADD COLUMN disclosed_summary    TEXT;
ALTER TABLE memory_entries ADD COLUMN disclosed_at         TEXT;
```

The recipient's row carries:
- `disclosed_from_entry` — the source `entry_id` (for revocation propagation)
- `disclosed_summary` — the operator-supplied or LLM-generated summary
- `disclosed_at` — timestamp of the disclosure event
- `content` — the summary, NOT the source entry's full content (ADR-0027 §4)

This means `Memory.recall` continues to work unchanged on the recipient side — it sees the disclosed entry like any other private memory, with the bonus metadata available for "where did this come from?" UI.

### `memory_disclose.v1` shape

```yaml
name: memory_disclose
version: '1'
description: |
  Disclose one of the calling agent's memory entries to another agent.
  ADR-0027 §4 — only the boundary-minimum (summary + entry_id + scope)
  crosses; full content stays at origin. Per-event consent is recorded
  in memory_consents for revocation propagation.
side_effects: read_only  # writes to local SQLite, like memory_write.v1
input_schema:
  type: object
  required: [entry_id, recipient_instance, summary]
  properties:
    entry_id:           {type: string}
    recipient_instance: {type: string}
    summary:            {type: string, maxLength: 1024}
output_schema:
  type: object
  properties:
    consent_id:           {type: string}
    recipient_entry_id:   {type: string}
    disclosed_at:         {type: string}
```

Side-effects classification stays `read_only` (same logic as memory_write.v1 — local SQLite mutation, no network/host filesystem). Genre ceiling check applies to the SOURCE agent's scope: a Companion calling `memory_disclose` would refuse because its memory is `private`-only.

### Cross-agent read flow

`memory_recall.v1` v0.2 grows a `mode` arg:

| `mode` (v0.2) | Behavior                                                                                |
|---------------|-----------------------------------------------------------------------------------------|
| `private` (default) | v0.1 behavior unchanged — caller's own entries only.                              |
| `lineage`     | Caller's own + ancestors' + descendants' entries with `scope >= lineage`. Each cross-agent entry returned emits `memory_read` to the chain. |
| `consented`   | Caller's own + entries on which the caller is in `memory_consents.recipient_instance`. Emits `memory_read` per result. |

The lineage view is computed via the closure table — same join `audit_for_agent` already uses for ancestry queries. v0.2 adds a `recall_visible_to(viewer_instance_id, ...)` method on `Memory` that runs the cross-agent visibility check at the SQL level (single query joining `memory_entries` + `agent_ancestry` + `memory_consents`).

### Audit chain entries

ADR-0027 §6 already declared the full set:

```
memory_read              { reader_instance, target_instance, entry_id,
                           scope, mode }
memory_disclosed         { source_instance, recipient_instance,
                           source_entry_id, recipient_entry_id, summary }
memory_consent_granted   { entry_id, recipient_instance, granted_by }
memory_consent_revoked   { entry_id, recipient_instance, revoked_by,
                           affected_entry_ids: [...] }
```

v0.2 implementation populates these during the read/write/disclose paths. memory_read emits ONLY for cross-agent reads — same-agent self-reads stay silent (per ADR-0027 §6) to avoid log spam.

### Implementation tranches (v0.2)

- **T12** — Schema bump v6→v7: ALTER memory_entries + CREATE memory_consents.
- **T13** — `Memory.recall_visible_to(viewer, ...)` — lineage + consented read paths. Emits memory_read for cross-agent results.
- **T14** — `memory_disclose.v1` built-in tool. Writes recipient row + memory_consents row + memory_disclosed audit event.
- **T15** — `memory_recall.v1` `mode` arg. Default stays `private`. New `lineage` and `consented` modes.
- **T16** — Consent lifecycle endpoints: `POST /agents/{id}/memory/{entry_id}/consent` (grant) and `DELETE` (revoke). Operator-side, not agent-side — consent is operator-authorized at this layer; per-relationship + tiered consent (per-conversation, not per-entry) lands in v0.3.
- **T17** — Frontend: memory tab on the Agents detail panel showing entries grouped by layer + scope. Disclosed entries get a "from <other agent>" indicator. Revoke button on consent rows.

### What v0.2 does NOT do

- **Per-relationship consent.** v0.2 is per-event only. "I trust this friend with all my emotional state" is v0.3.
- **Tiered consent.** Same — v0.3.
- **Auto-revocation propagation across realms.** When federation lands (Horizon 3), revoking consent should be a federation-protocol message that propagates to peer realms. v0.2 is single-realm, so there's no propagation to do.
- **Encrypted at rest.** Out of v0.2 scope; ADR-0025 (threat model v2) covers this in the federation context.

The v0.2 design preserves the "delete from disclosed agents is impossible" property from ADR-0027 §3 — revoke removes the consent grant + flags `disclosed_from_entry` on the recipient's row as "consent withdrawn" but doesn't delete the recipient's entry. The recipient agent has already integrated that information; we don't pretend we can take it back.

### Cross-references for v0.2

- ADR-0027 §1 (read scopes), §2 (consent model), §3 (deletion semantics), §4 (data minimization), §5 (genre ceilings), §6 (audit obligations) — every section drives a piece of v0.2.
- ADR-0006 — closure table is the existing ancestry truth; v0.2 reuses it.
- ADR-0019 T6 — genre runtime enforcement extends to memory_disclose; the source agent's genre ceiling is the disclosure ceiling.
- ADR-0028 — data portability needs to cover cross-agent disclosures; an export should include both originating entries and any pointers to entries on other agents the user disclosed to them.
