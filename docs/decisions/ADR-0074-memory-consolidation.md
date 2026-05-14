# ADR-0074 — Memory Consolidation

**Status:** Accepted (2026-05-14). Phase α scale substrate. Closes
the last untouched scale ADR. ADR-0022's three-layer model named
`working → episodic → consolidated` as a lifecycle but never
shipped the consolidation step. T1 here lands the substrate; the
rollup runner lands in T2-T5.

## Context

ADR-0022 (memory subsystem) declared three layers — working,
episodic, consolidated — with the design intent that episodic
entries eventually fold into longer-lived consolidated summaries.
The schema added the `layer` column but nothing ever drove
entries forward through the lifecycle, so today every memory the
agents write stays at its initial layer indefinitely.

At ~15 active agents the unbounded growth doesn't hurt — the
table is small, the queries are cheap, the recall path scans
fine. The ten-domain platform changes that math:

- D2 Daily Life OS — every operator interaction writes
  observations + inferences + preferences. Conservatively 50-200
  entries per active day per operator.
- D1 Knowledge Forge — daily deltas per tracked topic. Tens of
  topics → tens of entries per day.
- D9 Learning Coach — spaced-repetition reviews each generate
  feedback memories.
- D3 SOC — telemetry observation memories from observer agents.
- Cross-domain — every domain agent writes its own session
  memories per dispatch.

Project the daily floor: 300-1000 new entries per active day,
across an operating window that runs years. At one year the
table is in the 100K-300K range; at three years it's
800K-1M+. SQLite handles that size, but two real costs surface:

1. **Recall quality degrades.** When an agent calls
   `memory_recall.v1`, it gets a chronological tail of recent
   entries. With 100K+ entries the recent tail is dominated by
   noise — operator preferences mentioned once are buried under
   thousands of routine observations. Hybrid BM25+cosine retrieval
   (ADR-0076 T3) helps but still pulls from the full table, and
   semantic dups all rank similarly.
2. **Backup + encryption surface grows linearly.** Every entry
   carries an AES-256-GCM envelope (ADR-0050) at ~150-300 bytes
   of ciphertext + framing overhead per entry. At a million
   entries that's hundreds of MB of encrypted blobs to scan on
   every full backup, every encryption-key rotation, every
   integrity audit. Unbounded.

The fix is what ADR-0022 named but didn't build: periodic
consolidation. Episodic entries older than a window get folded
into a consolidated summary entry; the originals are marked but
preserved (so lineage queries still work, audit chain still has
its digest); the summary becomes the recall surface for that
time-bucket. Per-week or per-day rollups depending on the agent
and the layer.

## Decision

This ADR locks **four** schema-additive decisions in T1. The
consolidation *runner* (selector + summarizer + scheduler hook)
is deliberately queued for T2-T5 so this burst stays focused on
the data model.

### Decision 1 — `consolidation_state` column

Five-state enum on `memory_entries`, default `pending`:

| Value          | Meaning                                                                  |
|----------------|--------------------------------------------------------------------------|
| `pending`      | Default — eligible for the next consolidation pass.                     |
| `consolidated` | Original entry that has been folded INTO a summary. `consolidated_into` points to the summary. Recall path may skip these by default. |
| `summary`      | This row IS a summary entry — produced BY a consolidation pass, absorbing N children. |
| `pinned`       | Operator-pinned: never consolidate. Used for critical facts (the operator's own preferences, ground-truth-seeded entries). |
| `purged`       | Reserved for the GDPR/right-to-be-forgotten path: the row exists for chain-integrity but content is zeroed. (Forward compat — not used in T1.) |

Stored as TEXT with a CHECK constraint. Indexed (partial, on the
`pending` value) so the consolidation selector can scan
candidates in O(log n).

### Decision 2 — `consolidated_into` column (self-FK)

Nullable `TEXT` foreign-key to `memory_entries.entry_id`. When
`consolidation_state='consolidated'`, this points at the summary
row that absorbed the entry. When the state is anything else,
NULL.

Why a self-FK and not a join table: one entry can be consolidated
into at most one summary at a time. If a future tranche re-runs
consolidation (rolling up summaries into longer-window summaries),
the older summary becomes a child of the newer one — same shape.

### Decision 3 — `consolidation_run` column

Nullable `TEXT` carrying a run UUID. Populated by the runner
(T2-T4) on every row it touches in a single pass:

- Source entries get `consolidation_run=<run_id>` AND
  `consolidation_state='consolidated'` AND `consolidated_into=<summary_id>`.
- The summary entry created in that pass gets
  `consolidation_run=<run_id>` AND `consolidation_state='summary'`.

Pairs with the audit chain run-bookend events (Decision 4) so an
operator can trace any consolidated entry back to the run that
produced it without reading the chain entry-by-entry.

### Decision 4 — Three audit event types

Register in `KNOWN_EVENT_TYPES`:

- **`memory_consolidation_run_started`** — fires once when a pass
  begins. Payload: `run_id`, `started_at`, `selector_window`
  (the time-range it's working over), `instance_filter` (agents
  to consolidate, or `null` for all), `candidate_count`.
- **`memory_consolidated`** — fires per entry rolled into a
  summary. Payload: `run_id`, `source_entry_id`, `summary_entry_id`,
  `layer`, `claim_type`. One event per consolidated source.
- **`memory_consolidation_run_completed`** — fires when the pass
  ends. Payload: `run_id`, `completed_at`, `entries_consolidated`,
  `summaries_created`, `errors`, `wall_clock_ms`.

The bookend pair (started + completed) lets the operator detect a
crashed runner (started without completed in the chain →
investigate). The per-entry `memory_consolidated` lets the audit
trail prove that a specific original entry is preserved-but-folded
rather than silently lost.

## Implementation Tranches

| #  | Tranche                                                                          | Effort  |
|----|----------------------------------------------------------------------------------|---------|
| T1 | Schema v23 (3 columns + indexes) + 3 audit events + tests                        | 1 burst |
| T2 | `ConsolidationSelector` — age + layer + claim_type policy → candidate batch      | 1 burst |
| T3 | `ConsolidationSummarizer` — LLM call producing the summary content + lineage     | 1 burst |
| T4 | Scheduled task wiring (uses ADR-0075 budget cap) + runner end-to-end             | 1 burst |
| T5 | `/memory/consolidation/status` endpoint + operator runbook + pin/unpin CLI       | 1 burst |

Total: 5 bursts.

## Consequences

**Positive:**

- Substrate ready for the runner; T2-T5 only ever touch
  `memory_entries` columns added in T1, never the schema again.
- Recall path can filter out `consolidation_state='consolidated'`
  rows by default — the summary becomes the recall surface for
  that time-bucket without losing the underlying detail.
- Operator gets per-entry pin control (Decision 1 `pinned` state)
  so critical facts survive every pass.
- Audit chain provides full traceability: every consolidated
  entry has its run_id; every run has its bookend events.

**Negative:**

- Schema migration v23 adds 3 columns + 2 indexes to a table that
  will eventually be the biggest in the registry. Pure additive
  but write cost per insert grows slightly (column count, not
  index count — the indexes are partial).
- The consolidation_state enum is locked here; future states
  (e.g., `archived` for cold-tier offload) need a new ADR.
- `consolidated_into` is a self-FK. SQLite enforces FKs only when
  `PRAGMA foreign_keys=ON` (we set this — see schema.py
  CONNECTION_PRAGMAS). If a future migration forgets to preserve
  that pragma, cascade behavior changes silently.

**Neutral:**

- Doesn't change `memory_recall.v1` semantics in T1; that
  filter lands in T2 alongside the selector that proves the
  consolidated-entry semantics.
- Doesn't change `memory_written` / `memory_disclosed` / any
  existing event. New entries are still `pending` at write time.
- No new dependency.

## What this ADR does NOT do

- **Does not pick a consolidation policy.** Age windows, batch
  sizes, layer-specific rules — all T2 work. T1 just makes the
  state machine writable.
- **Does not summarize.** The LLM call that produces summary
  content lands in T3.
- **Does not run a pass.** Scheduling lands in T4 (ADR-0075
  budget-aware scheduled task).
- **Does not change at-rest encryption.** Summary entries get the
  same ADR-0050 envelope treatment as any other memory write.
- **Does not delete.** Consolidated source entries are preserved;
  the runner only flips state + sets `consolidated_into`. GDPR
  delete is a separate path (the `purged` state is forward compat
  for it, not used in T1).

## See Also

- ADR-0022 memory subsystem (declared the three-layer model)
- ADR-0027 memory privacy contract (consent + disclosure rules
  that the summarizer must respect: a summary cannot leak content
  that wasn't already disclosable to the same audience)
- ADR-0027 amendment epistemic metadata (claim_type + confidence
  inform consolidation policy — observations consolidate
  aggressively, promises never auto-consolidate)
- ADR-0050 encryption-at-rest (summary entries inherit the envelope)
- ADR-0075 scheduler scale (the consolidator runs as a scheduled
  task with budget_per_minute cap)
- ADR-0076 vector index (consolidated rows still feed the
  semantic index — T2 selector + index hook must coordinate)
