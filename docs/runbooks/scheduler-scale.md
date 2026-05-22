# Runbook — Scheduler Scale (ADR-0075)

**Scope.** Operating the scheduler at the ten-domain platform's
hundreds-of-tasks scale: tuning the per-tick wall-clock budget,
adjusting per-task dispatch-rate budgets, diagnosing
`scheduler_lag` events.

**Audience.** Operator on a running daemon.

---

## At a glance

The scheduler enforces two budgets:

1. **Tick budget** — wall-clock ceiling on a single dispatch
   loop iteration. Default 500ms. Exceeded → `scheduler_lag(reason=
   "tick_over_budget")` fires.
2. **Per-task budget** — sliding 60-second cap on a task's
   dispatch count. Default 6/min. Exceeded → `scheduler_lag(reason=
   "budget_enforced")` fires and the task's `next_run_at` pushes
   forward instead of firing.

Both are observable from one place: `GET /scheduler/status`.

---

## Reading `/scheduler/status`

```bash
curl -s http://127.0.0.1:7423/scheduler/status | jq
```

Returns:

```json
{
  "running": true,
  "poll_interval_seconds": 30.0,
  "tick_budget_ms": 500.0,
  "registered_runners": ["scenario", "tool_call"],
  "task_count": 47,
  "tasks_enabled": 45,
  "tasks_breaker_open": 1,
  "tasks_paused": 0,
  "dispatch_windows": {
    "total_in_window": 12,
    "per_task": {"verifier_24h": 1, "soc_telemetry_poll": 6, "...": "..."}
  }
}
```

What to look at:

- **`tasks_breaker_open > 0`** — a task's circuit breaker tripped.
  See "Reset a tripped breaker" below.
- **`total_in_window` climbing toward `task_count * 6`** — most
  tasks at or near budget. Either the workload is healthy and
  there's headroom to raise budgets, or tasks are misbehaving.
  Drill into specific tasks via `GET /scheduler/tasks/{id}`.
- **`tasks_paused > 0`** — operator has soft-paused (budget=0)
  one or more tasks. Audit the list to make sure no critical
  task is accidentally paused.

Per-task detail:

```bash
curl -s http://127.0.0.1:7423/scheduler/tasks/verifier_24h | jq
```

Returns the same `_serialize_task` shape with `budget_per_minute`
and `dispatches_in_window` populated.

---

## Tuning the tick budget

The tick budget is governed by `FSF_SCHEDULER_TICK_BUDGET_MS`.
Default 500. `inf` disables the check entirely.

**The budget measures scheduler overhead, not task execution
(B451).** A tick's wall-clock has two parts: the scheduler's own
bookkeeping (task scan, `due()` checks, budget accounting, audit
emits, state persistence) and the time blocked inside task
runners (`await runner(...)` — typically a multi-second
`llm_think` LLM inference). Only the first part is compared to
the budget. A scheduled task that legitimately runs for 11s does
*not* trip a 500ms budget — runner time is measured separately
and excluded. This corrects the original B295 behavior, which
compared raw wall-clock and so emitted `scheduler_lag` on every
scheduled `llm_think` dispatch (1,559 false positives in the
live chain before the fix).

When to raise the default:

- The daemon's own per-tick overhead genuinely exceeds 500ms —
  e.g. the audit chain is on slow storage, or `write_lock` is so
  contended that audit emits stall. Confirm via the
  `details.wall_clock_ms` vs `tick_duration_ms` split (see
  "Diagnosing" below) first: if `tick_duration_ms` (overhead) is
  small but `wall_clock_ms` is large, the budget is fine — the
  signal is a slow *task*, not a slow scheduler, and raising the
  budget fixes nothing.

When to lower the default:

- Latency-sensitive deployment. You want to know when scheduler
  overhead goes past e.g. 200ms so you can investigate.

How to change without restart: not currently supported. The env
var is read at scheduler construction (boot time). Restart the
daemon after editing the env.

---

## Adjusting per-task budgets

Per-task `budget_per_minute` is stored in
`scheduled_task_state.budget_per_minute` (schema v22). Operator
overrides survive restart — the scheduler reads the column at
hydrate time and the upsert path deliberately does NOT update
the column from outcome data (ADR-0075 Decision 2).

To change a task's budget today (T4 ships the data path; the
`fsf scheduler budget` CLI lands in a future tranche):

```sql
sqlite3 data/registry.sqlite \
  "UPDATE scheduled_task_state SET budget_per_minute = 30
   WHERE task_id = 'soc_telemetry_poll';"
```

Restart the daemon to pick up the new value (the runtime read
is at hydrate, not per-tick).

Special values:

- `0` — soft-pause. The task stays enabled and visible in
  `/scheduler/tasks` but never dispatches. No `scheduler_lag`
  emit (deliberate operator action isn't an anomaly).
- `1-5` — heavily rate-limited. Use for tasks that don't
  need to run more than every 12-60 seconds.
- `6` — default, ~10 second floor between dispatches.
- `30-60` — high-frequency runners (observers, healthchecks).

---

## Diagnosing `scheduler_lag` events

Pull recent lag events from the audit chain:

```bash
curl -s http://127.0.0.1:7423/audit/tail?event_type=scheduler_lag&limit=50 | jq
```

For `reason="tick_over_budget"`:

- `tick_duration_ms` — the scheduler's own per-tick *overhead*
  (B451): task scan + `due()` checks + budget accounting + audit
  emits + state persistence. This is the figure compared to
  `tick_budget_ms`; it excludes time blocked inside task runners.
- `tick_budget_ms` — configured budget at the time of the event.
- `dispatches_in_tick` — how many tasks the tick fired.
- `details.wall_clock_ms` — total tick wall-clock, including
  runner execution.
- `details.runner_total_ms` — summed time spent inside task
  runners this tick (`wall_clock_ms - runner_total_ms ≈
  tick_duration_ms`).

This event now means the scheduler *itself* is slow — investigate
audit-chain storage latency or `write_lock` contention. A slow
*task* (a long LLM call) no longer surfaces here: that is normal
operation, and its timing is visible via the `scheduled_task_*` /
`tool_call_*` event timestamps instead. `dispatches_in_tick >
task_count / 2` means many tasks came due simultaneously —
consider staggering schedules.

For `reason="budget_enforced"`:

- `task_id` — which task got rate-limited.
- `budget_per_minute` — the cap that was hit.
- `dispatches_in_window` — how many dispatches were already in
  the 60-second window.

`dispatches_in_window == budget_per_minute` is normal — the
task hit its cap. If you see the same `task_id` repeating in the
chain, the task is configured to fire more frequently than its
budget allows. Either raise the budget or adjust the schedule.

---

## Soft-pause workflow

To pause a task without disabling it (e.g. during a vendor
outage, but keep the registration intact):

```sql
sqlite3 data/registry.sqlite \
  "UPDATE scheduled_task_state SET budget_per_minute = 0
   WHERE task_id = 'soc_telemetry_poll';"
```

Restart daemon. Task stays in `/scheduler/tasks`, stays
`enabled=true`, but `_consume_budget` returns False on every
attempt and `next_run_at` advances per schedule. No audit-chain
spam.

To resume:

```sql
sqlite3 data/registry.sqlite \
  "UPDATE scheduled_task_state SET budget_per_minute = 6
   WHERE task_id = 'soc_telemetry_poll';"
```

Restart daemon. (Per-tick reread is queued for a future tranche.)

---

## Reset a tripped breaker

If `tasks_breaker_open > 0`, identify the task:

```bash
curl -s http://127.0.0.1:7423/scheduler/tasks | jq \
  '.tasks[] | select(.state.circuit_breaker_open) |
   {id, last_failure_reason: .state.last_failure_reason}'
```

Fix the underlying cause (broken provider, missing secret, bad
config). Then reset:

```bash
curl -X POST -H "X-Forest-Token: $FSF_API_TOKEN" \
  http://127.0.0.1:7423/scheduler/tasks/$TASK_ID/reset
```

This zeroes the failure counter, clears the breaker, persists
the cleared state, and emits
`scheduled_task_circuit_breaker_reset` to the chain.

---

## See also

- ADR-0041 — set-and-forget orchestrator (the scheduler).
- ADR-0075 — scheduler scale + indexing (this runbook's home ADR).
- `docs/runbooks/encryption-at-rest.md` — sibling Phase α runbook.
- `dev-tools/check-drift.sh` — verifies registry integrity if a
  badly-applied `UPDATE` corrupts state.
