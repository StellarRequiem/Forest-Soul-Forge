# Runbook — Memory Consolidation (ADR-0074)

**Scope.** Operating the consolidation runner at the ten-domain
platform's hundreds-of-entries-per-day scale: reading status,
controlling per-entry inclusion via pin/unpin, diagnosing failed
runs, tuning the policy.

**Audience.** Operator on a running daemon.

---

## At a glance

Consolidation folds old episodic memories into summary entries.
Each pass:

1. Selects pending entries older than `min_age_days` (default 14)
   matching the policy's layer + claim_type filter (default
   `episodic` + `observation/user_statement`).
2. Groups by `(instance_id, layer)` — each agent gets its own
   summary per layer.
3. Calls the LLM with the batch + a faithful-rollup prompt.
4. Atomically inserts the summary entry + flips every source row
   to `consolidated` with a `consolidated_into` pointer.
5. Emits `memory_consolidation_run_started` + per-entry
   `memory_consolidated` + `memory_consolidation_run_completed`
   bookend events.

A pinned entry (state=`pinned`) is invisible to the selector —
critical operator-asserted facts survive every pass.

---

## Reading `/memory/consolidation/status`

```bash
curl -s -H "X-Forest-Token: $FSF_API_TOKEN" \
  http://127.0.0.1:7423/memory/consolidation/status | jq
```

Returns:

```json
{
  "schema_version": 1,
  "counts_by_state": {
    "pending":      2741,
    "consolidated": 9832,
    "summary":      318,
    "pinned":         12,
    "purged":          0
  },
  "last_run": {
    "run_id":            "8c7a59f1d6...",
    "completed_at":      "2026-05-15T03:00:14+00:00",
    "summaries_created": 7,
    "entries_consolidated": 142
  }
}
```

What to look at:

- **`pending` climbing without bound** — the runner isn't keeping
  up. Either the scheduled task isn't firing (check
  `/scheduler/status` for a tripped breaker on the
  `memory_consolidate` task) or the LLM provider is too slow for
  the budget. Bump `budget_per_minute` or raise the LLM timeout.

- **`last_run.summaries_created == 0` + nonzero `pending`** — the
  selector found nothing eligible. Likely the `min_age_days`
  policy is too high or the layer/claim_type filter excludes
  current writes. Pull recent `memory_consolidation_run_started`
  events for the `candidate_count` field — if zero, policy is
  too tight.

- **`last_run.entries_consolidated` ≪ `last_run.summaries_created`
  × expected batch size** — partial failures occurred (per-group
  errors). Pull the `memory_consolidation_run_completed` event's
  `errors` field for the per-group reason strings.

### Last few summaries

```bash
curl -s -H "X-Forest-Token: $FSF_API_TOKEN" \
  "http://127.0.0.1:7423/memory/consolidation/recent-summaries?limit=10" \
  | jq
```

Returns each summary entry's `entry_id`, the `instance_id` of the
agent it absorbed memories from, the `layer`, the `created_at`,
the `run_id` that produced it, and the `source_count` of memories
it folded.

---

## Pinning critical entries

When an entry must never be auto-consolidated (operator
preferences, decisions, dated commitments), pin it.

### Via HTTP (live daemon)

```bash
curl -X POST -H "X-Forest-Token: $FSF_API_TOKEN" \
  http://127.0.0.1:7423/memory/consolidation/pin/<entry_id>
```

Returns `200` + `{ok: true, previous_state: "pending",
consolidation_state: "pinned"}`.

Refuses (`409`) if the entry is already `consolidated` /
`summary` / `purged` — those states have lineage semantics that
pin doesn't compose with cleanly. To preserve a summary verbatim,
pin its source children before they get consolidated next time
(the summary itself is then a derived artifact, not a load-
bearing record).

### Via CLI (offline / pre-daemon-boot)

```bash
fsf memory pin <entry_id>
fsf memory unpin <entry_id>
fsf memory pin <entry_id> --registry-path /custom/path/registry.sqlite
```

Operates directly on `data/registry.sqlite`. The CLI bypasses the
HTTP layer, useful for post-crash cleanup or when the daemon
won't boot. Same state-transition rules as the HTTP endpoint.

### Bulk pinning

For now, bulk pins go through SQL:

```bash
sqlite3 data/registry.sqlite \
  "UPDATE memory_entries SET consolidation_state = 'pinned'
   WHERE consolidation_state = 'pending'
     AND tags_json LIKE '%\"important\"%';"
```

A `fsf memory pin-tag <tag>` CLI extension lands in a future
tranche when the operator workflow demands it.

---

## Diagnosing a failed run

Pull the bookend events from the audit chain:

```bash
curl -s -H "X-Forest-Token: $FSF_API_TOKEN" \
  "http://127.0.0.1:7423/audit/tail?event_type=memory_consolidation_run_completed&limit=10" \
  | jq
```

Each entry's `event_data.errors` is a list of `[instance_id,
layer, message]` tuples for per-group failures. Common patterns:

- `summarize: provider.complete failed: ...` — LLM call failed.
  Check provider health via `/healthz`. The selected batch stays
  in `pending` for the next pass.

- `summarize: provider returned an empty summary` — the LLM
  returned blank/whitespace. Bump `max_tokens` (default 200) or
  adjust the prompt rubric.

- `sql: ...` — SQLite error during the atomic transaction.
  Usually means a FOREIGN KEY violation (the
  `agent_dna_for_summary` value or the agent ran no longer
  exists) — check the agents table.

A crashed runner shows up as an `unstarted` pair: a
`run_started` without a matching `run_completed`. That's the
signal to investigate (process crashed mid-loop, OS killed the
daemon, etc.).

---

## Tuning the policy

The policy lives in code today (`ConsolidationPolicy` defaults
in `core/memory_consolidation.py`). Operator overrides land via
the scheduled task wiring (T5b — queued; the runner is currently
callable but not yet on a schedule). When the scheduled task
wiring ships, the policy will be parameterized through env vars:

- `FSF_CONSOLIDATION_MIN_AGE_DAYS` (default 14)
- `FSF_CONSOLIDATION_MAX_BATCH_SIZE` (default 200)
- `FSF_CONSOLIDATION_ELIGIBLE_LAYERS` (default `episodic`)
- `FSF_CONSOLIDATION_ELIGIBLE_CLAIM_TYPES` (default
  `observation,user_statement`)

For now, code changes + daemon restart are the only way to
adjust.

---

## What this runbook does NOT cover

- **GDPR delete path.** The `purged` state is reserved for that
  workflow but no automation exists yet. Delete-by-operator
  still flows through `DELETE /agents/{id}/memory/{entry_id}`
  which sets `deleted_at` (tombstone, not state flip).
- **Encrypted entries.** The runner skips `content_encrypted=1`
  rows — they accumulate as pending without consolidating. A
  future tranche will wire decryption-key access for the runner.
- **Scheduled-task wiring.** B307 ships the runner as a callable
  function; the scheduled task that invokes it monthly under
  ADR-0075 budget cap is T5b (queued).

---

## See also

- ADR-0074 — memory consolidation (this runbook's home ADR)
- ADR-0022 — memory subsystem (the three-layer model)
- ADR-0027 amendment — epistemic-metadata rules (why summaries
  are `agent_inference`)
- ADR-0050 — encryption-at-rest (why encrypted rows skip the
  runner)
- ADR-0075 — scheduler scale (the budget cap the scheduled
  runner respects)
- `docs/runbooks/encryption-at-rest.md` — sibling Phase α
  runbook
- `docs/runbooks/scheduler-scale.md` — sibling Phase α runbook
