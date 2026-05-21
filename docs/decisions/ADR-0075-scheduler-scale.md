# ADR-0075 — Scheduler Scale + Indexing

**Status:** Accepted (2026-05-14). Phase α scale substrate. Sized
for the hundreds of recurring tasks the ten-domain platform will
spawn (per-domain heartbeats, observer polls, weekly digests,
maintenance cycles).

**Amended 2026-05-21 (B451):** Decision 3's `tick_over_budget`
measurement was respecified — the budget measures scheduler
*overhead*, not raw tick wall-clock. See "## Amendment — B451"
below.

## Context

ADR-0041's set-and-forget scheduler (Bursts 86-92) ships:

- An asyncio poll loop in `daemon/scheduler/runtime.py:Scheduler`
  that holds tasks in an in-memory `dict[str, ScheduledTask]` and
  iterates them on every tick (`_tick()` → `for task in tasks:
  if task.due(now): await self._dispatch(task, now)`).
- A `scheduled_task_state` SQLite table (schema v13) mirroring
  each task's `TaskState` for restart hydration. Columns:
  `task_id PK, last_run_at TEXT, next_run_at TEXT,
  consecutive_failures INTEGER, circuit_breaker_open INTEGER,
  total_runs INTEGER, total_successes INTEGER, total_failures
  INTEGER, last_failure_reason TEXT, last_run_outcome TEXT,
  updated_at TEXT`. The table is a queryable view, not the
  dispatch driver — `_dispatch` upserts a row per outcome.
- One existing index: `idx_scheduled_task_state_breaker` partial
  on `WHERE circuit_breaker_open = 1` (Burst 90, for the operator
  "show me what's stuck" query).

Today the scheduler holds ~15 tasks (swarm patrols, daemon
healthchecks, dashboard_watcher polling). At that scale the
in-memory linear scan + per-outcome SQLite upsert is fine.

The ten-domain platform pushes the task count to hundreds:

- D2 Daily Life OS — morning briefing + inbox triage + evening
  reflection + weekly review per operator
- D3 SOC — telemetry collector polls + detection rule sweeps +
  threat intel feed refreshes
- D8 Compliance Auditor — daily license scans + weekly framework
  checks + monthly report generation
- D5 Smart Home — routine triggers per device per day
- D1 Knowledge Forge — daily delta computation per topic
- D9 Learning Coach — spaced repetition scheduling per concept
- D10 Research Lab — long-running hypothesis test heartbeats
- Cross-domain — every domain emits its own heartbeat

At hundreds of tasks the in-memory iteration itself is still
microseconds, but two real costs surface:

1. **Per-dispatch SQLite write cost.** Every successful tick that
   fires N tasks does N independent `upsert()` calls — each its
   own SQLite transaction. SQLite's single-writer discipline
   means a tick that fires 50 tasks does 50 sequential `BEGIN…
   COMMIT` cycles serialized against every other write_lock
   holder in the daemon.
2. **No back-pressure on a misbehaving task.** A broken collector
   configured to poll every 5s (or worse, an interval-0 bug)
   monopolizes the dispatch loop — the loop processes serially,
   so one runaway task starves the rest. ADR-0041's circuit
   breaker only fires after `max_consecutive_failures` failures;
   a task that *succeeds* every dispatch but fires too often
   evades the breaker entirely.

A third concern is forward-looking — `/scheduler/tasks` and any
future "what's due in the next minute?" query reads from the
table. Without a `next_run_at` index those queries are O(n) over
the table; cheap today, measurable at thousands of rows. Adding
the index now is cheap insurance against a future refactor that
moves dispatch onto SQL pulls.

## Decision

This ADR locks **three** decisions, all schema-additive:

### Decision 1 — Index on `next_run_at`

`CREATE INDEX IF NOT EXISTS idx_scheduled_task_state_next_run_at
ON scheduled_task_state(next_run_at) WHERE next_run_at IS NOT
NULL;`

Partial index — only rows with a scheduled next run are indexed
(never-run tasks have `next_run_at IS NULL` and don't belong in
the "what's due" set anyway). The index supports:

- The future `/scheduler/status` "next 10 due tasks" view (T4).
- Any future refactor that moves dispatch onto a SQL pull
  (`SELECT … WHERE next_run_at <= now() ORDER BY next_run_at`).
- Operator queries via `fsf` CLI for "what's queued."

**This is NOT a dispatch-loop optimization.** The current
dispatch loop iterates the in-memory `self._tasks` dict, which
stays O(n) but at hundreds of entries is microseconds. The index
is substrate for the SQL-driven dispatch path queued for a future
ADR, plus the operator visibility surfaces T4 ships.

### Decision 2 — `budget_per_minute` column

`ALTER TABLE scheduled_task_state ADD COLUMN budget_per_minute
INTEGER NOT NULL DEFAULT 6 CHECK (budget_per_minute >= 0);`

Default 6 = ten-second floor between dispatches. Stored on
`scheduled_task_state` (not the YAML config) because the value
is observable runtime state, not author-time intent — operator
can adjust budget for a misbehaving task without editing config
and reloading. `0` means "indefinitely paused" (rate-limited to
nothing); useful as a soft-disable that survives restart.

Enforcement is T3 work. T1 ships the column so the data model is
in place before the enforcement code; existing rows pick up the
default at migration time, and the
`scheduled_task_state.upsert()` payload gets the field added in
T3.

### Decision 3 — `scheduler_lag` audit event type

Register `scheduler_lag` in `KNOWN_EVENT_TYPES`. Fires when (T2/T3
work):

- A specific task's `budget_per_minute` gets enforced (operator
  visibility into "this task wanted to fire but was rate-limited").
- The dispatch loop's per-tick *overhead* exceeds a configurable
  threshold (default 500ms — operator visibility into "scheduler
  can't keep up"). Note: the original B295 implementation measured
  raw tick wall-clock here, which conflated scheduler overhead with
  task-runner execution; corrected in B451 — see the Amendment
  section below.

Event payload shape (locked in this ADR so T2/T3 can target it):

```json
{
  "reason":          "budget_enforced" | "tick_over_budget",
  "task_id":         string | null,
  "tick_duration_ms": number | null,
  "budget_per_minute": number | null,
  "dispatches_in_window": number | null,
  "details":         object | null
}
```

`task_id` populated for `budget_enforced`, null for
`tick_over_budget`. The `_emit_audit` helper already exists on
`Scheduler`; T2/T3 just route through it.

## Implementation Tranches

| #  | Tranche                                                                                | Effort  |
|----|----------------------------------------------------------------------------------------|---------|
| T1 | Schema v22 (index + budget column) + `scheduler_lag` event type + tests                | 1 burst |
| T2 | Tick-wall-clock measurement + `scheduler_lag(reason="tick_over_budget")` emit           | 1 burst |
| T3 | Per-task sliding-window enforcement + `scheduler_lag(reason="budget_enforced")` emit    | 1 burst |
| T4 | `/scheduler/status` endpoint surfaces budget + lag history + operator runbook           | 1 burst |

Total: 4 bursts.

## Consequences

**Positive:**

- Substrate ready for thousands of scheduled tasks without code
  changes downstream of T1 (index + column both exist; T3
  layers logic on top).
- A misbehaving task can't starve the dispatch loop once T3
  lands — its budget caps the firing rate independent of its
  configured schedule.
- Operator gets observability into both per-tick and per-task
  scheduler health via the audit chain.
- Future dispatch refactor (SQL-pull instead of in-memory scan)
  has the index it needs from day one.

**Negative:**

- Schema migration v22 is pure additive but bumps the registry
  version (rebuild-from-artifacts handles existing operators).
- Existing tasks default to `budget=6/minute` on migration; any
  task previously firing >6/min sees rate-limiting once T3 lands.
  T4 runbook covers operator override.
- The index adds disk + write cost on every `upsert()`; trivial
  at hundreds of rows, monitored at thousands.

**Neutral:**

- Doesn't change task-definition surface — operators still author
  scheduled tasks via `scheduled_tasks.yaml` (ADR-0041 patterns
  intact).
- Doesn't change cron-expression semantics.
- Pure SQLite layer; no new dependency.
- Dispatch model unchanged in T1 — still in-memory iteration via
  `Scheduler._tick()`. T2/T3 add measurement + enforcement
  hooks but keep the iteration model.

## What this ADR does NOT do

- **Does not pre-compute task work.** Budget caps the invocation
  rate; the actual work each task does is the task's own concern.
- **Does not parallel-dispatch.** Forest's single-writer SQLite
  discipline holds — tasks fire serially within a tick. Tasks
  that need parallelism spawn agents that themselves run async.
- **Does not implement priority queues.** All tasks fire on
  `next_run_at` ordering. Operators who need priority queue
  semantics layer that on top via task tags.
- **Does not move dispatch onto SQL.** The index supports such a
  refactor; deciding when to do it is out of scope for ADR-0075.

## Amendment — B451 (2026-05-21): tick budget measures scheduler overhead

**Problem.** T2 (B295) implemented Decision 3's tick-over-budget
check by measuring raw tick wall-clock: `_tick()` timed from loop
entry to loop exit, and the loop body `await _dispatch()` blocks on
`await runner(...)`. Every scheduled `tool_call` task running
`llm_think.v1` does a synchronous LLM inference of several seconds;
that runner time landed inside the measured region, so the tick
"duration" was dominated by task execution, not scheduler work.

Result: the 500ms budget was tripped by every single `llm_think`
dispatch. The live `examples/audit_chain.jsonl` accumulated **1,559
`scheduler_lag(reason="tick_over_budget")` events**, with
`tick_duration_ms` ranging 511ms–42,431ms — each one a normal
LLM-bound dispatch mislabeled as scheduler lag. The signal the
event exists to carry ("is the scheduler keeping up?") was drowned
in false positives.

The Context section of this ADR already states the premise the fix
restores: *"the in-memory iteration itself is still microseconds."*
The scheduler's own per-tick overhead is small; the wall-clock
measurement was a spec error that conflated it with task execution.

**Decision.** The tick budget measures **scheduler overhead only**
— the task scan, `due()` / `_consume_budget` checks, audit emits,
and state persistence. Task-runner execution (`await runner(...)`)
is measured separately and excluded. `Scheduler._dispatch()`
returns the wall-clock ms spent inside the runner; `_tick()` sums
those and subtracts them from total wall-clock.
`overhead_ms = wall_clock_ms - runner_total_ms` is the figure
compared to `tick_budget_ms`.

**Payload.** The locked shape from Decision 3 is preserved. The
previously-unused `details` field now carries the breakdown for
`tick_over_budget` events:

```json
{
  "reason": "tick_over_budget",
  "tick_duration_ms": "<scheduler overhead ms — compared to budget>",
  "tick_budget_ms": 500.0,
  "dispatches_in_tick": "<n>",
  "details": {
    "wall_clock_ms": "<total tick wall-clock incl. runner time>",
    "runner_total_ms": "<summed runner execution this tick>"
  }
}
```

`tick_duration_ms` is now the overhead figure (the quantity
actually compared to the budget); operators who want total
wall-clock read `details.wall_clock_ms`.

**Scope note.** This amendment is a measurement correction; it does
not change the dispatch model (still serial within a tick, per
"What this ADR does NOT do"). Two adjacent issues surfaced during
the B451 investigation and are left for operator decision, since
both are architecture changes rather than measurement fixes:

1. The `tool_call` runner holds `app.state.write_lock` for the
   full `await dispatcher.dispatch()` — including the LLM call — so
   a slow scheduled task blocks every HTTP writer for its whole
   duration. The scheduler's own `_dispatch` is careful *not* to
   hold the lock across the runner (B199 comment); the runner
   re-acquires it, defeating that intent.
2. Moving runner execution off the tick loop into background
   `asyncio` tasks would stop a slow task from blocking the loop
   and let independent tasks overlap — at the cost of the serial,
   easy-to-audit dispatch model this ADR deliberately keeps.

## See Also

- ADR-0041 Set-and-Forget Orchestrator (the scheduler this scales)
- ADR-0073 audit chain segmentation (sister scale ADR; sealing
  is a scheduler-driven monthly task)
- ADR-0074 memory consolidation (sister scale ADR; consolidation
  is a scheduler-driven daily task)
- ADR-0076 vector index (background indexer is scheduler-driven)
