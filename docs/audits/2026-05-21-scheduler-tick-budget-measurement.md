# Scheduler tick-budget measurement correction — 2026-05-21

**Driver:** B451 — `scheduler_lag(reason="tick_over_budget")` firing
1,559 times in the live audit chain.
**HEAD at start:** `3611dca` (fix: health endpoint /api/health →
/healthz in monitor scripts).
**Scope:** `src/forest_soul_forge/daemon/scheduler/runtime.py` +
`tests/unit/test_scheduler_scale.py`. Documentation: ADR-0075
amendment, scheduler-scale runbook, CHANGELOG.

## Symptom

`examples/audit_chain.jsonl` (22,036 entries / 13MB) carried 1,559
`scheduler_lag` events, all `reason="tick_over_budget"`,
`tick_budget_ms: 500`. Observed `tick_duration_ms`:

| stat | value |
|---|---:|
| count | 1,559 |
| min | 511 ms |
| median | 1,328 ms |
| mean | 1,986 ms |
| max | 42,431 ms |

`dispatches_in_tick` was 1 for 1,542 events and 2 for 17. 139
events fired on 2026-05-21 alone.

## Profiling

Three scheduled tasks are active (all `tool_call` type, all invoke
`llm_think.v1`):

| task_id | dispatches | schedule |
|---|---:|---|
| `dashboard_watcher_healthz_5m` | 4,358 | every 5m |
| `signal_listener_audit_hourly` | 367 | every 1h |
| `status_reporter_daily_brief` | 17 | every 24h |

Tracing one over-budget tick in the chain (seq 22032–22036):

```
22032  scheduled_task_dispatched   10:42:34Z  dashboard_watcher_healthz_5m
22033  tool_call_dispatched        10:42:34Z  llm_think.v1
22034  tool_call_succeeded         10:42:45Z  (+11s)
22035  scheduled_task_completed    10:42:45Z
22036  scheduler_lag               10:42:45Z  tick_duration_ms: 11237.89
```

The 11.2-second "tick" is the `llm_think.v1` LLM inference, start to
finish. The 42.4-second worst case is a tick where two `llm_think`
tasks came due together and ran serially.

### Root cause

`Scheduler._tick()` measured raw wall-clock:

```python
tick_started = time.monotonic()
...
for task in tasks_snapshot:
    ...
    await self._dispatch(task, now)   # <- blocks on await runner(...)
tick_duration_ms = (time.monotonic() - tick_started) * 1000.0
```

`_dispatch()` does `await runner(...)`; the `tool_call` runner does
`await dispatcher.dispatch(...)`, which executes `llm_think.v1` — a
synchronous LLM call of several seconds. That runner time is inside
the measured region, so `tick_duration_ms` was dominated by task
execution, not scheduler work.

The 500ms budget (ADR-0075 Decision 3) exists to answer *"is the
scheduler keeping up?"* — i.e. the scheduler's own per-tick
bookkeeping. ADR-0075's own Context section states the premise:
*"the in-memory iteration itself is still microseconds."* Measuring
wall-clock conflated that microsecond-scale overhead with
multi-second task execution. Every `llm_think` dispatch tripped the
budget by construction.

### Ruled out

- **Audit-chain append cost.** `AuditChain.append()` opens the
  JSONL in append mode and writes one line — O(1) regardless of the
  13MB file size. Not the bottleneck.
- **O(n) task scans.** `_tick` iterates an in-memory dict of 3
  tasks. Microseconds.
- **Persistence.** One SQLite upsert per dispatch. Single-digit ms.

Scheduler overhead per dispatch (2 audit emits + 1 upsert) is a few
milliseconds — three orders of magnitude under the 500ms budget.

## Fix

The tick budget now measures **scheduler overhead only**.

- `Scheduler._dispatch()` brackets `await runner(...)` with
  `time.monotonic()` and returns the runner duration in ms (`0.0`
  when no runner is registered).
- `Scheduler._tick()` sums the returned runner durations and
  computes `overhead_ms = max(0.0, wall_clock_ms - runner_total_ms)`.
  The `tick_over_budget` check compares `overhead_ms` to the budget.
- The `scheduler_lag` payload keeps its locked shape. The
  previously-unused `details` field now carries
  `{wall_clock_ms, runner_total_ms}`. `tick_duration_ms` becomes the
  overhead figure — so for any emitted event the invariant
  `tick_duration_ms > tick_budget_ms` still holds.

No event-type, schema, or migration change. Confined to `runtime.py`.

## Verification

- `tests/unit/test_scheduler_scale.py`: rewrote
  `test_tick_over_budget_emits_scheduler_lag` to drive scheduler
  overhead (a slow audit-chain `append`) rather than a slow runner;
  added `test_slow_runner_alone_does_not_emit_scheduler_lag` as the
  regression guard for the 1,559-false-positive class.
- Scheduler suites: `test_scheduler_scale.py` +
  `test_scheduler_runtime.py` — 84 pass (the one failure,
  `test_schema_version_is_v22`, pre-dates this change: schema is now
  v23; confirmed identical on the unmodified baseline).
- Full unit suite: 76 failed / 4185 passed before-vs-after is
  76 failed / 4184 passed on the baseline — **identical pre-existing
  failures, +1 net new test, zero regressions.**

## Left for operator decision (not implemented)

Two architecture-level issues surfaced during profiling. Both are
beyond a measurement correction and need an operator call:

1. **`write_lock` held across the LLM call.** The `tool_call`
   runner (`task_types/tool_call.py`) does
   `with write_lock: await dispatcher.dispatch(...)` — the lock is
   held for the entire multi-second LLM inference. Every HTTP write
   route blocks for that duration. The scheduler's `_dispatch`
   deliberately releases `write_lock` around the runner (B199
   comment); the runner re-acquires it, defeating that intent. (The
   HTTP dispatch routes do the same thing, so this is a system-wide
   pattern, not scheduler-specific — narrowing it is a dispatcher
   change.)
2. **Serial in-tick dispatch.** A slow task blocks the tick loop
   and any other task due in the same tick (the 42s worst case).
   Moving runner execution to background `asyncio` tasks would fix
   this but trades away the serial, easy-to-audit dispatch model
   ADR-0075 deliberately keeps ("What this ADR does NOT do").

See ADR-0075 "## Amendment — B451" for the full write-up.
