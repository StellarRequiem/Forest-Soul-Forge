# ADR-0041 — Set-and-Forget Orchestrator (scheduled-task substrate)

**Status:** Accepted (2026-05-03). Implementation across Bursts 86-89.
Supersedes the deferred status of ADR-0036 T4: this ADR provides the
substrate that ADR-0036 T4 was waiting on.

## Context

Forest agents fire when something pokes them. The dispatcher
serves `POST /agents/{id}/tools/call`, the conversations runtime
serves messages, the writes router serves `/birth` and `/spawn`.
None of these run on a schedule. There is no "the daemon, on its
own initiative, every N minutes, dispatches X" surface.

This came up multiple times before this ADR was filed:

- **ADR-0036 T4** explicitly deferred ("Scheduler: per-Verifier
  cron via the existing scheduled-task surface. Defaults to
  24-hour cadence.") because the substrate didn't exist.
- **Y5 ambient mode** (ADR-003Y) shipped as scaffolding only —
  the periodic nudge mechanism was stubbed.
- **Run 001 (FizzBuzz autonomous coding loop)** required a bash
  loop driver because the daemon couldn't loop itself.
- **Operator request 2026-05-03**: "set and forget orchestrator
  program on a timer to start and restart the coding agents for
  testing run and such."

The pattern across these is the same: a recurring driver that
runs on the daemon's clock, dispatches against the existing tool
runtime, logs every action to the audit chain. What changes from
use case to use case is what the task IS, not how the timer
behaves.

This ADR specifies that timer + driver substrate, plus the two
task types we need first (`tool_call` and `scenario`).

## Decision

Add a daemon-internal scheduler that runs in the same asyncio
loop as the FastAPI app. Tasks are configured in
`config/scheduled_tasks.yaml`. Each task is dispatched on its
schedule, every dispatch lands in the audit chain, failures
trigger bounded restart with circuit-breaker semantics. The
operator controls tasks via HTTP endpoints under `/scheduler/`.

The scheduler is **in-process**, not a separate service:

- Uses the daemon's existing `app.state.write_lock`,
  `audit_chain`, `registry`, and provider registry.
- Lifecycle bound to the daemon — start/stop in the FastAPI
  lifespan context.
- Crash/restart story: when the daemon comes back, the scheduler
  reads the persisted last-run state from SQLite and resumes.

Why in-process instead of a separate scheduler service:

- One process to monitor, one log to read, one set of audit
  invariants to maintain.
- Audit chain integration is automatic — the scheduler emits
  events through the same `audit_chain.append` path as every
  other dispatcher.
- The work being scheduled is mostly tool dispatches against the
  daemon anyway. A separate scheduler would just be a process
  that calls localhost HTTP — strictly more moving parts for no
  isolation benefit on a local-first single-machine deployment.
- The eventual production deployment story (multi-tenant cloud
  per the v0.4 app planning) needs a different shape, but that's
  a v0.4 problem solved at that layer; the local-first single-
  daemon case is what this ADR covers.

## Architecture

### Components

```
┌─────────────────────────────────────────────────┐
│  daemon/app.py (FastAPI app)                    │
│                                                 │
│  lifespan:                                      │
│    on startup:                                  │
│      scheduler = Scheduler(...)                  │
│      await scheduler.start()                    │
│      app.state.scheduler = scheduler             │
│    on shutdown:                                 │
│      await scheduler.stop()                     │
└──────────┬──────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────┐
│  daemon/scheduler/runtime.py                    │
│                                                 │
│    Scheduler                                    │
│      - poll loop (asyncio task)                 │
│      - tasks: dict[task_id, ScheduledTask]      │
│      - each tick: scan tasks, dispatch due ones │
│                                                 │
│    ScheduledTask                                │
│      - id, schedule, task_type, config          │
│      - last_run, next_run, consecutive_failures │
│      - circuit_breaker_open: bool               │
└──────────┬──────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────┐
│  daemon/scheduler/task_types/                   │
│                                                 │
│    tool_call_task.py                            │
│      - dispatch one /agents/{id}/tools/call     │
│      - args from task.config                    │
│                                                 │
│    scenario_task.py                             │
│      - YAML-defined multi-step scenario         │
│      - birth → seed → loop → archive            │
└─────────────────────────────────────────────────┘
```

### Schedule format

Initial implementation: interval-based only.

```yaml
schedule: "every 5m"
schedule: "every 1h"
schedule: "every 24h"
```

Parsed into `interval_seconds: int`. Cron syntax (`"0 */6 * * *"`)
is queued for a follow-up; not in the v0.4 minimum.

`next_run = last_run + interval_seconds` (or `now + interval`
on first registration).

### Task types

**`tool_call`** — dispatch a single tool call against an
existing agent.

```yaml
- id: verifier_24h_scan
  description: "Verifier scans memory for contradictions daily"
  schedule: "every 24h"
  enabled: true
  type: tool_call
  config:
    agent_id: verifier_loop_001
    tool_name: verifier_scan
    tool_version: "1"
    args:
      lookback_hours: 24
      max_pairs: 100
```

This closes ADR-0036 T4 directly. Dispatch goes through the
standard `ToolDispatcher`, so all governance (constitution,
genre kit-tier, initiative) applies.

**`scenario`** — multi-step scenario with birth + seed + loop +
archive lifecycle.

```yaml
- id: fizzbuzz_smoke_6h
  description: "FizzBuzz coding loop every 6h to verify autonomous loop health"
  schedule: "every 6h"
  enabled: true
  type: scenario
  config:
    scenario_path: config/scenarios/fizzbuzz.yaml
    inputs:
      max_turns: 50
      target_dir: data/test-runs/scheduled-fizzbuzz
```

Scenario YAML format (defined in T3 implementation):

```yaml
# config/scenarios/fizzbuzz.yaml
name: fizzbuzz
description: "Stub-and-test FizzBuzz coding loop"
inputs:
  required: [target_dir]
  optional: [max_turns]
defaults:
  max_turns: 50

steps:
  - birth_agent:
      role: software_engineer
      name_prefix: ScenarioFB_
      constitution_patches:
        allowed_paths: ["${target_dir}"]

  - seed_files:
      target_dir: "${target_dir}"
      files:
        fizzbuzz.py: |
          def fizzbuzz(n: int) -> list[str]:
              raise NotImplementedError
        test_fizzbuzz.py: |
          ... (test cases) ...

  - iterate:
      max_turns: "${max_turns}"
      stop_when:
        - pytest_passes
        - livelock_identical_edits: 3
        - livelock_unchanged_pytest: 5
      step:
        - read_files: [fizzbuzz.py, test_fizzbuzz.py]
        - dispatch_tool:
            tool: llm_think
            args:
              prompt: "${BUILT_PROMPT}"
              max_tokens: 600
        - extract_python_block: result.output.response
        - write_file: fizzbuzz.py
        - dispatch_tool:
            tool: pytest_run
            args:
              target: test_fizzbuzz.py

  - archive_agent:
      reason: "scenario complete (${exit_reason})"
```

This codifies what `live-test-fizzbuzz.command` does today, but
runs inside the daemon's asyncio loop instead of as bash.

### Persistence

A new SQLite table `scheduled_task_state` (schema v13):

```sql
CREATE TABLE scheduled_task_state (
  task_id TEXT PRIMARY KEY,
  last_run_at TEXT,            -- ISO 8601
  next_run_at TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  circuit_breaker_open INTEGER NOT NULL DEFAULT 0,
  total_runs INTEGER NOT NULL DEFAULT 0,
  total_successes INTEGER NOT NULL DEFAULT 0,
  total_failures INTEGER NOT NULL DEFAULT 0,
  last_failure_reason TEXT,
  updated_at TEXT NOT NULL
);
```

Updated atomically on every dispatch outcome inside the
write_lock. On daemon restart, the scheduler reads this table,
reconstructs in-memory ScheduledTask state, and resumes.

### Failure handling + circuit breaker

Each task has a `max_consecutive_failures` (default 3). On
failure:

1. `consecutive_failures += 1`
2. Audit event `scheduled_task_failed` with reason
3. If `consecutive_failures >= max_consecutive_failures`:
   - `circuit_breaker_open = true`
   - Audit event `scheduled_task_circuit_breaker_tripped`
   - Task is skipped on subsequent ticks until operator
     manually re-enables via `POST /scheduler/tasks/{id}/reset`

On success:

1. `consecutive_failures = 0`
2. `circuit_breaker_open = false` (auto-reset on success)
3. Audit event `scheduled_task_completed`

### Audit events (new)

Six event types, all emitted by the scheduler:

| Event type | Emitted when |
|---|---|
| `scheduled_task_dispatched` | A task tick fires, before the work runs |
| `scheduled_task_completed` | Task work succeeded |
| `scheduled_task_failed` | Task work raised or returned failure |
| `scheduled_task_circuit_breaker_tripped` | `consecutive_failures >= max` |
| `scheduled_task_circuit_breaker_reset` | Operator reset the breaker |
| `scheduled_task_disabled` / `scheduled_task_enabled` | Operator toggled |

Audit chain emits put the scheduler on the same evidence
footing as every other state-changing surface in the daemon —
operators can see "what fired, when, what happened" by tailing
the chain.

### HTTP endpoints

```
GET  /scheduler/status            — Scheduler status + task summary
GET  /scheduler/tasks             — List all tasks + their state
GET  /scheduler/tasks/{id}        — One task's full state
POST /scheduler/tasks/{id}/trigger — Force-run now (out-of-band)
POST /scheduler/tasks/{id}/disable
POST /scheduler/tasks/{id}/enable
POST /scheduler/tasks/{id}/reset  — Clear circuit breaker + counters
```

All POST endpoints are `require_writes_enabled + require_api_token`
gated, same as the writes routes.

## Tranche plan

| Tranche | Scope | Burst |
|---|---|---|
| **T1** | This ADR (design) | 85 (this) |
| **T2** | Scheduler runtime + persistence + lifespan integration + `tool_call` task type + minimal HTTP (status + list). End-to-end working for the simplest case. | 86 |
| **T3** | Scenario task type runtime + scenario YAML loader + step interpreter (read_files, dispatch_tool, write_file, iterate, archive_agent) | 87 |
| **T4** | Port FizzBuzz scenario from `live-test-fizzbuzz.command` to `config/scenarios/fizzbuzz.yaml`, run through scheduler, verify end-to-end | 88 |
| **T5** | Operator control endpoints (trigger, enable/disable, reset), tests, runbook, STATE/README updates | 89 |

After T5, the operator can configure
`config/scheduled_tasks.yaml` to run the FizzBuzz scenario every
N hours unattended, observe results via `/scheduler/tasks/{id}`,
and the audit chain captures every dispatch.

## Consequences

**Positive.**

- Closes ADR-0036 T4 (verifier scheduled scans).
- Unblocks the user's stated workflow ("set and forget orchestrator
  to start and restart coding agents for testing").
- Provides the substrate for any future periodic work — drift
  checks, cleanup runs, regression suites, telemetry rollups.
- Audit chain integration means scheduled work is as evidenced
  as on-demand work; no observability blind spot.
- In-process simplicity: one process, one set of governance
  invariants, no IPC.

**Negative.**

- Daemon process now does periodic work even when no operator
  request is in flight. Battery drain on laptops; CPU on servers.
  Mitigation: scheduler is opt-in (default `enabled: false` in
  config), and individual tasks default `enabled: false`.
- Scenario YAML interpreter is a small DSL — adds maintenance
  surface. Mitigation: starts narrow (FizzBuzz needs 5 step types);
  expansion is gated by need.
- Schema v12 → v13 migration. Pure addition; no risk to existing
  agent state.
- Tasks with side_effects beyond `read_only` need to play nicely
  with the existing approval queue. Open question — see below.

## Open questions (deferred to T2 or beyond)

- **Approval queue interaction.** If a scheduled task tries to
  dispatch a tool that `requires_human_approval`, what happens?
  Two options: (a) scheduler refuses to schedule tools with
  human-approval gates (cleanest, most restrictive); (b)
  scheduler dispatches and the call lands in the approval queue
  same as a human-driven call (most permissive, requires UI to
  surface "this came from scheduled task X"). T2 picks (a) for
  v0.4; (b) is queued for a later ADR if operators want it.

- **Rate-limit interaction.** Per-session counters on tools are
  per-`session_id`. The scheduler uses a stable session ID per
  task — hits the same counter every dispatch — so a daily task
  that runs forever would eventually exhaust `max_calls_per_session`.
  Mitigation: scheduler rotates session IDs daily (deterministic:
  `<task_id>-<YYYYMMDD>`). T2 implementation note.

- **Configuration reload.** If the operator edits
  `scheduled_tasks.yaml`, do running tasks pick it up? T2 picks
  "no, requires daemon restart" for simplicity. A
  `POST /scheduler/reload` endpoint can come later.

## References

- ADR-0036 T4 (Verifier Loop scheduler — superseded by this ADR)
- ADR-003Y Y5 (ambient mode scaffolding — partially fulfilled by this)
- ADR-0019 (Tool Execution Runtime — the substrate scheduled tasks dispatch through)
- `live-test-fizzbuzz.command` (the bash-driven equivalent we're replacing for scenarios)
- Run 001 audit doc (`docs/audits/2026-05-03-full-audit.md` — the run that surfaced the need)

---

**Decision:** Build the daemon-internal scheduler per the
architecture above, in 5 tranches. T1 = this ADR. T2 starts
implementation in Burst 86.
