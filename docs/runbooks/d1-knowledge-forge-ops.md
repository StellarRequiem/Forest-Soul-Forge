# Runbook — D1 Personal Knowledge Forge (ADR-0086)

**Scope.** Operating the D1 Personal Knowledge Forge domain
end-to-end: birth, skill install, first dispatch, observation,
recovery.

**Audience.** Operator on a running daemon at HEAD ≥ the commit
that lands D1 Phase A (this runbook will grow as Phases B–D ship).

**Phase context.** D1 ships in four phases per ADR-0086:

| Phase | New agent(s) | New builtin tool | Status |
|---|---|---|---|
| **A** | librarian + prospector | none — reuses web_fetch + memory_write/recall + audit_chain_verify + personal_recall + llm_think | in flight |
| **B** | synthesizer | topic_genealogy_build.v1 | pending |
| **C** | knowledge_verifier | knowledge_contradiction_scan.v1 | pending |
| **D** | (none — pure substrate phase) | daily_knowledge_delta.v1 | pending |

Each phase = one commit + one push, so the operator can verify
phase N before phase N+1 fires.

---

## At a glance

D1's value proposition: **active personal knowledge curation,
not a passive note store.** A research → summarize → categorize
→ store loop with provenance per fact, daily delta reports,
topic genealogy, and contradiction flagging.

| Role | Genre | Posture | Skill | What it does |
|---|---|---|---|---|
| `librarian` | guardian | green | `knowledge_curation.v1` | Owns the knowledge catalog + per-fact provenance ledger. Reads memory layers + writes catalog attestations tagged with topic + provenance. Never mutates source data. |
| `prospector` | researcher | green | `research_gathering.v1` | Allowlisted-network sourcing agent. Pulls source material on operator request + hands off to the librarian via the prospector inbox. Never writes to the catalog directly. |

Both Phase A agents are **operator-birthed via the approval queue**
per ADR-0086 — no auto-birth.

**Why two roles, not one?** Sourcing and catalog discipline are
different governance surfaces. The prospector REACHES OUT
(network ceiling, allowlisted fetches); the librarian CURATES +
ATTESTS (read-only, audit-trait emphasis). Different ceilings,
different policies; one role would conflate them + raise the
catalog discipline's blast radius unnecessarily.

**Connector posture.** D1 declares three connector dependencies:
`forest-files`, `forest-notes`, `forest-browser-history`. None
ship in v0.3 — they're operator-installable. Phase A operates
with **graceful degradation** per ADR-0086 Decision 4: when a
connector is absent, the librarian's catalog only sees entries
written via `memory_write.v1` (private + lineage memory layers);
the prospector's source provenance is capped at the fetched
URL + access timestamp. Connectors widen the surface; their
absence doesn't break the loop.

---

## Phase A — intake foundation

### 1. Restart the daemon

The new role definitions land in `trait_tree.yaml` +
`genres.yaml` + `constitution_templates.yaml`; the per-role kits
land in `tool_catalog.yaml`. The daemon loads these at lifespan
boot, so a restart is required before the births can pick them
up.

```bash
./dev-tools/force-restart-daemon.command
```

Verify in `/healthz`'s `startup_diagnostics` that the genre
engine reports `status: ok` and that `librarian` appears in
`/genres` under the `guardian` genre's `roles` list and
`prospector` appears under `researcher`.

### 2. Birth the agents

```bash
./dev-tools/birth-librarian.command
./dev-tools/birth-prospector.command
```

Each script is idempotent — re-running it skips the birth if
the agent already exists. Both set posture GREEN as the default
per ADR-0086 Decision 1 (read-only catalog discipline +
read-from-network are non-acting).

### 3. Confirm the knowledge corpus roots

The birth scripts `mkdir -p` the canonical paths if missing:

```
data/knowledge/
data/knowledge/catalog/             ← librarian writes here (via memory + optional file-level catalog dump)
data/knowledge/prospector_inbox/    ← prospector handoff lane
```

The agents themselves never `mkdir` — the per-tool
`allowed_paths` constraints (librarian's `code_read`) are scoped
to these existing roots.

### 4. First dispatch — research_gathering.v1

The operator drives the first source pull explicitly (no
cascade fires in Phase A; cascade wiring lands in Phase D):

```
POST /agents/<Prospector-D1-id>/tools/call
{
  "tool_name": "skill_run",
  "tool_version": "1",
  "session_id": "<uuid>",
  "args": {
    "skill_name": "research_gathering",
    "skill_version": "1",
    "skill_args": {
      "source_url": "https://arxiv.org/abs/2401.00000",
      "topic_slug": "diffusion-models",
      "operator_reason": "initial intake"
    }
  }
}
```

Expected: the prospector returns a brief + records a private-
memory attestation tagged `knowledge_prospector_inbox` +
`topic:diffusion-models` + `provenance:<source_url>`. The
librarian's catalog loop is operator-driven in Phase A; the
inbox tag is the handoff lane.

### 5. Second dispatch — knowledge_curation.v1

The operator drives the librarian's catalog write against the
prospector's inbox entry (or against an operator-provided
claim direct):

```
POST /agents/<Librarian-D1-id>/tools/call
{
  "tool_name": "skill_run",
  "tool_version": "1",
  "session_id": "<uuid>",
  "args": {
    "skill_name": "knowledge_curation",
    "skill_version": "1",
    "skill_args": {
      "topic_slug": "diffusion-models",
      "claim_text": "DDIM removes the stochasticity from DDPM sampling, enabling deterministic generation.",
      "source_provenance": {
        "source_url": "https://arxiv.org/abs/2010.02502",
        "access_ts": "2026-05-23T00:00:00Z",
        "prospector_id": "<Prospector-D1-id>"
      },
      "operator_reason": "initial catalog seed"
    }
  }
}
```

Expected: the librarian returns a structured catalog block +
records a private-memory attestation tagged
`knowledge_catalog_entry` + `topic:diffusion-models` +
`provenance:<url>` + `attestor:Librarian-D1`.

### 6. Recovery — when the agent halts

The librarian + prospector both halt cleanly when the audit
chain integrity check fails. Look for the `audit_chain_verify`
step's `status` in the skill response:

- `status: ok` → catalog/inbox write proceeded.
- `status: broken` → the agent surfaced a `chain_broken` halt;
  catalog/inbox write was NOT performed. Run
  `./dev-tools/check-drift.sh` to triage chain integrity before
  retrying.

### 7. Observation

- **Catalog entries:** `GET /memory/<librarian_id>?tag=knowledge_catalog_entry`
- **Inbox entries:** `GET /memory/<prospector_id>?tag=knowledge_prospector_inbox`
- **Per-topic view:** filter by `topic:<slug>` tag on either query.
- **Audit chain:** the `memory_write` for each catalog/inbox
  entry produces an entry on the chain; `audit_chain_verify`
  must remain ok for both agents to continue operating.

---

## Phase B–D (pending)

These sections will land as each phase closes. Tracking summary:

- **Phase B** — synthesis. Will add: `synthesizer` role,
  `topic_genealogy_build.v1` tool, `knowledge_summarize.v1` +
  `topic_genealogy.v1` skills. Operator-facing dispatch surface
  for building topic graphs from the catalog.
- **Phase C** — verification. Will add: `knowledge_verifier`
  role (YELLOW posture), `knowledge_contradiction_scan.v1`
  tool, `knowledge_contradiction_flag.v1` skill. Single-agent
  scope per ADR-0086 Decision 3.
- **Phase D** — delta + cascade + umbrella. Will add:
  `daily_knowledge_delta.v1` tool, `daily_knowledge_delta.v1`
  skill, cascade wiring (d8→d1 active; d1→d9/d10/d7/d2 declared
  inert), `birth-d1-knowledge-forge.command` umbrella script,
  diagnostic harness extensions.
