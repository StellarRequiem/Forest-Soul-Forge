# Runbook — D1 Personal Knowledge Forge (ADR-0086)

**Scope.** Operating the D1 Personal Knowledge Forge domain
end-to-end: birth, skill install, first dispatch, observation,
recovery.

**Audience.** Operator on a running daemon at HEAD ≥ the commit
that lands D1 Phase A (this runbook will grow as Phases B–D ship).

**Phase context.** D1 ships in four phases per ADR-0086:

| Phase | New agent(s) | New builtin tool | Status |
|---|---|---|---|
| **A** | librarian + prospector | none — reuses web_fetch + memory_write/recall + audit_chain_verify + personal_recall + llm_think | CLOSED |
| **B** | synthesizer | topic_genealogy_build.v1 | CLOSED |
| **C** | knowledge_verifier | knowledge_contradiction_scan.v1 | CLOSED |
| **D** | (none — pure substrate phase) | daily_knowledge_delta.v1 | CLOSED |

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

## Phase B — synthesis

### 1. Restart the daemon to load synthesizer role + tool

```bash
./dev-tools/force-restart-daemon.command
```

Verify `/genres` lists `synthesizer` under the `researcher` genre
and `/tools/catalog` lists `topic_genealogy_build.v1`.

### 2. Birth Synthesizer-D1

```bash
./dev-tools/birth-synthesizer.command
```

Idempotent; sets posture GREEN per ADR-0086 Decision 1.

### 3. Build a topic graph

After the librarian has cataloged some entries against a topic
(per Phase A step 5), the synthesizer can build a graph:

```
POST /agents/<Synthesizer-D1-id>/tools/call
{
  "tool_name": "skill_run",
  "tool_version": "1",
  "session_id": "<uuid>",
  "args": {
    "skill_name": "topic_genealogy",
    "skill_version": "1",
    "skill_args": {
      "topic_slug": "diffusion-models",
      "window_days": 365,
      "operator_reason": "first topic graph for D1 verification"
    }
  }
}
```

The synthesizer returns: a structured graph (nodes = catalog
entries; edges = relationships), a one-paragraph narrative, and
a private-memory attestation tagged `topic_graph_built` +
`topic:<slug>` + `attestor:Synthesizer-D1`.

### 4. Summarize what's been learned

```
POST /agents/<Synthesizer-D1-id>/tools/call
{
  "tool_name": "skill_run",
  "tool_version": "1",
  "session_id": "<uuid>",
  "args": {
    "skill_name": "knowledge_summarize",
    "skill_version": "1",
    "skill_args": {
      "topic_slug": "diffusion-models",
      "max_entries": 50
    }
  }
}
```

Returns narrative prose with per-claim provenance preserved.

### 5. Observation

- **Topic graphs:** `GET /memory/<synthesizer_id>?tag=topic_graph_built`
- **Summaries:** `GET /memory/<synthesizer_id>?tag=knowledge_summary`
- **Per-topic graph history:** filter by `topic:<slug>` to see
  how the graph has grown.

---

## Phase C — verification

### 1. Restart the daemon

The new role + tool need a daemon reload to take effect:

```bash
./dev-tools/force-restart-daemon.command
```

Verify `/genres` lists `knowledge_verifier` under `guardian`
and `/tools/catalog` lists `knowledge_contradiction_scan.v1`.

### 2. Birth KnowledgeVerifier-D1

```bash
./dev-tools/birth-knowledge-verifier.command
```

Idempotent; sets posture **YELLOW** per ADR-0086 Decision 1.
This forces every flagged contradiction through the operator
approval queue before propagating downstream.

### 3. Dispatch a contradiction scan

After the librarian has cataloged multiple entries against a
topic, the verifier can scan for contradictions:

```
POST /agents/<KnowledgeVerifier-D1-id>/tools/call
{
  "tool_name": "skill_run",
  "tool_version": "1",
  "session_id": "<uuid>",
  "args": {
    "skill_name": "knowledge_contradiction_flag",
    "skill_version": "1",
    "skill_args": {
      "topic_slug": "diffusion-models",
      "window_days": 365,
      "min_confidence": 0.4,
      "operator_reason": "post-catalog contradiction sweep"
    }
  }
}
```

The verifier returns: the candidate count, per-pair evidence
narrative, the chain integrity status, and a delegate-escalation
id pointing at the operator approval queue entry. The actual
contradiction-stamp write (`memory_flag_contradiction.v1`) is
operator-dispatched **after review** — YELLOW posture gates the
in-skill flag.

### 4. Single-agent scope guard

Per ADR-0086 Decision 3, `knowledge_contradiction_scan.v1`
hard-rejects `scope: cross_agent` with a `ToolValidationError`.
The cross-agent contradiction-scan path is deferred to v0.4
when the kernel-singleton verifier_loop's wiring lands. Operators
who try `scope: "cross_agent"` get a clean rejection (not a
silent fallback), so the deferral is observable.

### 5. Observation

- **Candidate pairs:** the scan tool's output includes
  `contradiction_pairs` with per-pair `confidence` +
  `detection_kind` (`explicit_flag` 1.0 / `lexical_cue` 0.4).
- **Operator approvals:** the YELLOW posture queue at
  `GET /approvals?agent_id=<KnowledgeVerifier-D1-id>` lists
  pending escalations.
- **Stamped contradictions:** after operator-driven dispatch
  of `memory_flag_contradiction.v1`, query
  `GET /memory/<verifier_id>?contradiction_flag=true`
  (the substrate writes to a dedicated `memory_contradictions`
  table — ADR-0036 T2).

---

## Phase D — delta + cascade + umbrella

### 1. Restart the daemon

```bash
./dev-tools/force-restart-daemon.command
```

Verify `/tools/catalog` lists `daily_knowledge_delta.v1`.

### 2. Umbrella birth — all four D1 agents at once

After pulling D1-A through D1-D, the umbrella script births
every D1 agent in order:

```bash
./dev-tools/birth-d1-knowledge-forge.command
```

It calls (in order) `birth-librarian` → `birth-prospector` →
`birth-synthesizer` → `birth-knowledge-verifier`. Each child
script is idempotent, so re-running the umbrella is safe.

### 3. Pull a daily delta

```
POST /agents/<Synthesizer-D1-id>/tools/call
{
  "tool_name": "skill_run",
  "tool_version": "1",
  "session_id": "<uuid>",
  "args": {
    "skill_name": "daily_knowledge_delta",
    "skill_version": "1",
    "skill_args": {
      "window_hours": 24,
      "operator_reason": "morning briefing"
    }
  }
}
```

Returns: a structured delta (catalog writes + prospector pulls
+ contradiction flags bucketed by topic) plus an operator
narrative + a private-memory attestation tagged
`daily_knowledge_delta_built`. Widen `window_hours` to 168
(weekly) or 720 (monthly) for catch-up briefs after PTO.

### 4. Cascade wiring

Live cascade:
- `d8_compliance.compliance_scan` → `d1_knowledge_forge.knowledge_curation`

When the D8 compliance scanner surfaces a framework-rule change
or control delta, the resolved route cascades into the
librarian's catalog so the operator's personal knowledge corpus
stays current. Audit-chain entries join the source +
cascaded routes via `cascade_source_*` fields on the
ResolvedRoute.

Declared-INERT cascades (commented in `config/handoffs.yaml`):
- `d1.knowledge_contradiction_flag` → `d9.curriculum_design`
- `d1.knowledge_summarize` → `d10.deep_research`
- `d1.knowledge_curation` → `d7.content_drafting`
- `d1.daily_knowledge_delta` → `d2.morning_briefing`

Each lands as a real cascade rule when its target domain ships
per ADR-0067 rollout order (D2 → D7 → D9 → D10 still upstream).

### 5. Diagnostic harness coverage

`dev-tools/diagnostic/section-09-handoff-routing.command` is
config-driven; it picks up D1 automatically from `handoffs.yaml`
+ `config/domains/d1_knowledge_forge.yaml`. Expected outcome at
D1 close: 3/4 PASS + 1 INFO (the INFO is the long tail of
future-domain capabilities without handoff mapping, unrelated
to D1).

### 6. Observation

- **Daily deltas:** `GET /memory/<synthesizer_id>?tag=daily_knowledge_delta_built`
- **Per-window deltas:** filter by `window:<N>h` tag.
- **Full live status:** all 4 D1 agents alive + posture
  (GREEN/GREEN/GREEN/YELLOW for librarian/prospector/synthesizer/
  knowledge_verifier).

---

## D1 LIVE — 2026-05-23

All four phases CLOSED. ADR-0086 Accepted.
`config/domains/d1_knowledge_forge.yaml` status: `live`.
