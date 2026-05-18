# Runbook — Telemetry pipeline

**ADR:** ADR-0064
**Tranches shipped:** T1 (B348 substrate) → T2 (B349 macOS adapter)
→ T3 (B377 chain hookup + verify CLI) → T4 (B379 telemetry_steward
role) → **T5 this**. T6 (micro-batching + threat_intel_curator)
closes Phase B.
**Audience:** SOC operator (Alex) running the kernel locally.

## Pipeline shape

```
adapter subprocess ──stdout──▶ AdapterIngestor.parse()
                                       │
                                       ▼
                              accumulate to batch
                              (size or time-based flush)
                                       │
                                       ▼
                       SqliteTelemetryStore.ingest_batch()
                       (commits ONE transaction; returns batch_id)
                                       │
                                       ▼
                       AuditChain.append("telemetry_batch_ingested",
                         {batch_id, source, event_count,
                          integrity_root, first/last_timestamp})
                                       │
                                       ▼
                        chain anchor durable; store-side write
                        already committed before this point
```

**Store-first / anchor-second** is intentional (ADR-0064 D5 +
B377 design). Anchor-without-data is harder to recover from than
data-without-anchor; the verify CLI surfaces the latter as a
clear `CHAIN_ENTRY_MISSING` verdict.

## Daily operator workflow

1. **Glance**: telemetry_steward summary (when dispatched) reports
   per-source volume / freshness / integrity-anchor sanity in
   metadata terms.
2. **Spot-verify**: pick a batch_id from a steward brief flagged
   as `unverified_anchor` and run
   `dev-tools/fsf-telemetry-verify.command <batch_id>`. Verdict
   tells you whether the store + chain agree.
3. **Investigate**: a `MISMATCH` or `CHAIN_ENTRY_MISSING` triggers
   the recovery flows below.

## Backups

The telemetry store is `data/telemetry.sqlite`. Back it up via the
same operator cadence as `data/registry.sqlite`:

```bash
# Pre-restart safety copy:
cp data/telemetry.sqlite data/telemetry.sqlite.bak-$(date +%Y%m%d)

# Pre-migration safety copy (when a future T6 introduces a
# micro-batching schema bump):
cp data/telemetry.sqlite data/telemetry.sqlite.pre-bN
```

The store SHOULD be backed up alongside the audit chain — they're
hash-linked through `integrity_root`. Restoring one without the
other guarantees a verify-CLI mismatch on every batch. If only one
backup exists, prefer the chain (it carries the anchors that prove
what the store *should* contain).

## Retention sweep

`SqliteTelemetryStore.retention_sweep(now, retention_policy)`
deletes events by `retention_class`. Default policy lives in
`security/telemetry/retention.py` and classifies each event at
ingest time as one of:

| Class | Retention (default) | Examples |
|---|---|---|
| `security_relevant` | indefinite | auth_event, policy_decision, severity=critical |
| `standard` | 30 days | process_spawn, network_connection at default severity |
| `ephemeral` | 24 hours | log_line / process_spawn at info severity |

**Run cadence:** the sweep is operator-driven today (no scheduler
hookup yet — that's a future ADR). Recommend daily:

```bash
PYTHONPATH=src python3 -c "
import time
from pathlib import Path
from forest_soul_forge.security.telemetry.store import SqliteTelemetryStore
store = SqliteTelemetryStore(Path('data/telemetry.sqlite'))
n = store.retention_sweep(now=time.time(), retention_policy={
    'ephemeral': 86400,           # 1 day
    'standard': 30 * 86400,        # 30 days
    'security_relevant': None,     # keep forever
})
print(f'swept {n} rows')
store.close()
"
```

**Why ephemeral exists:** process_spawn + log_line at info-level
on a busy box accumulate into millions of rows per week. Holding
them at standard retention blows up the SQLite file and slows
queries. Ephemeral keeps the store usable; security_relevant
captures the events you'd actually want to forensic-walk.

## Adapter management

Adapters live at
`src/forest_soul_forge/security/telemetry/adapters/*.py`. Each
declares:

- `SOURCE` — string allowlisted in `config/telemetry_sources.yaml`.
- `command()` — the subprocess command (e.g.
  `["log", "stream", "--predicate", "..."]`).
- `parse(line)` — turns one stdout line into a `TelemetryEvent`.
- `retention_override(event)` — optional per-event class
  override.

**Adding a new adapter:**

1. Drop the source name into `config/telemetry_sources.yaml`
   under `allowlisted_sources`.
2. Create the Python module under
   `src/forest_soul_forge/security/telemetry/adapters/<source>.py`
   implementing the `Adapter` protocol (see
   `macos_unified_log.py` as the reference).
3. Wire the ingestor in the daemon's lifespan:
   ```python
   ing = AdapterIngestor(
       MyAdapter(), store,
       batch_size=100,
       flush_interval_s=5.0,
       audit_chain=app.state.audit_chain,
       chain_agent_dna=None,  # system-emitted today; T4
                              # telemetry_steward could attribute
   )
   ing.start()
   app.state.telemetry_ingestors.append(ing)
   ```
4. Restart the daemon. The first flush emits a
   `telemetry_batch_ingested` event; spot-verify via
   `dev-tools/fsf-telemetry-verify.command`.

**Pausing an adapter:** stop the ingestor in lifespan (`ing.stop()`
in a teardown hook). There's no "soft pause" today; the steward's
`forbid_silent_pipeline_pause` policy makes that an operator
action, not an agent action.

**Crash isolation:** the ingestor subprocess is independent of the
daemon (B377 didn't change this). A misbehaving parser flagged in
`stats.last_error` is the operator's signal to investigate; the
ingestor stays running.

## Recovery flows

### `MISMATCH` (verify CLI reports tampered batch)

```
[FAIL] integrity_root mismatch — store recomputed XXX... but
       chain anchor at seq=N claims YYY...
```

Real tampering or per-event mutation. Investigation order:

1. Check who/what touched `data/telemetry.sqlite`. The store's
   single-writer discipline only writes via `ingest_batch`; any
   other write is suspicious.
2. Walk the chain backward from seq=N — was there a
   `chain_repair_event` or `telemetry_store_export` that touched
   the batch? (None of those exist as event types today; their
   presence would be a substrate-level addition that should be
   accompanied by an ADR.)
3. Restore from the latest backup that pre-dates the mismatch
   timestamp. Re-run verify to confirm clean.
4. Document in `docs/audits/YYYY-MM-DD-telemetry-mismatch.md`
   with the integrity_roots before/after + the suspected source.

### `CHAIN_ENTRY_MISSING` (store has batch but chain lacks anchor)

```
[FAIL] no telemetry_batch_ingested entry in
       examples/audit_chain.jsonl references batch_id=XXX
```

Mid-flush crash window: the store transaction committed before
`audit_chain.append` ran (per `ingestor.flush_pending` docstring).
Recovery:

1. The store data is durable — it's not corrupt, just unanchored.
   Operator option (a): re-emit an anchor by running a manual
   verify-and-attest pass (script TBD; today's verify CLI is
   read-only).
2. Option (b): accept the gap, document it in a one-line audit
   entry (`docs/audits/YYYY-MM-DD-telemetry-anchor-gap.md`), and
   ignore that batch's metadata in steward briefs.
3. Don't re-ingest the events — that creates duplicate event_ids
   and a duplicate batch_id, which the store rejects.

### `BATCH_EMPTY` (verify CLI says no events for that batch_id)

Likely a typo in the operator's `batch_id` argument, or the batch
was retention-swept. Check the chain anchor's `first_timestamp`
against the retention horizon for its class.

### `STORE_UNAVAILABLE` (verify CLI can't open the store)

Operational. Check disk space + file permissions on
`data/telemetry.sqlite`. The daemon won't recover automatically;
restart after fixing.

## Cross-references

- **ADR-0064** — the design doc. Section "Decisions" carries
  D1-D7; this runbook implements D4-D7's operator-facing surface.
- **ADR-0050** B199 — single-writer chain discipline. Same
  invariant the telemetry store inherits.
- **B348** `3bee6cc` — T1 substrate
- **B349** `9980ce9` — T2 reference adapter (macos_unified_log)
- **B377** `31b39e0` — T3 chain hookup + verify CLI
- **B379** `44e846e` — T4 telemetry_steward role
- **B384** (this commit) — T5 runbook
- **T6** (queued) — micro-batching + threat_intel_curator;
  closes ADR-0064 + D3 Phase B.

## Verification

After this runbook lands + the daemon has been running long
enough for at least one adapter flush:

1. `dev-tools/fsf-telemetry-verify.command <some_batch_id>`
   → verdict `OK` on every recent batch.
2. `dev-tools/diagnostic/diagnostic-all.command` → section-08
   PASS, section-04 PASS, section-13 PASS (15 tabs after B381 +
   `Capabilities` after B381, total 16).
3. Birth TelemetryStreward-D3 if not already
   (`dev-tools/birth-telemetry-steward.command`); dispatch
   `telemetry_steward_brief.v1` against a recent batches list;
   confirm the steward writes a memory entry tagged
   `telemetry_steward_brief`.
