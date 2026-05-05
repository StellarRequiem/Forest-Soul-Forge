#!/usr/bin/env bash
# Burst 85: file ADR-0041 — Set-and-Forget Orchestrator design.
#
# T1 of the ADR-0041 arc. Implementation lands in Bursts 86-89.
# This is design only — captures the architecture so subsequent
# implementation bursts have a clear contract.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 85 — ADR-0041 design doc (Set-and-Forget Orchestrator) ==="
echo
clean_locks
git add docs/decisions/ADR-0041-set-and-forget-orchestrator.md
git add commit-burst85.command
clean_locks
git status --short
echo
clean_locks
git commit -m "ADR-0041: Set-and-Forget Orchestrator (scheduled-task substrate)

Files the design ADR for the daemon-internal scheduler. T1 of a
5-tranche arc; T2-T5 are implementation in Bursts 86-89.

Context: ADR-0036 T4 (verifier scheduling) was deferred because
'the existing scheduled-task surface' didn't exist. Y5 ambient
mode shipped as scaffolding for the same reason. Run 001 had to
use a bash loop driver. The same gap kept surfacing — there's no
in-daemon recurring-driver substrate. This ADR specifies that
substrate.

Decision summary:

- In-process scheduler running in the daemon's asyncio loop
  (NOT a separate service). Lifecycle bound to FastAPI lifespan.
- Tasks configured via config/scheduled_tasks.yaml, dispatched
  on schedule, persisted to schema v13 'scheduled_task_state'
  table for survive-restart.
- Two task types in v0.4 minimum: 'tool_call' (single dispatch
  against existing agent — closes ADR-0036 T4) and 'scenario'
  (multi-step birth->seed->loop->archive lifecycle, codifies what
  live-test-fizzbuzz.command does today).
- Schedule format: interval-based ('every 5m', 'every 24h').
  Cron syntax queued for a later ADR.
- Failure handling: max_consecutive_failures triggers circuit
  breaker; operator manually resets via HTTP. Audit chain
  records every dispatch, every outcome, every breaker trip.
- Six new audit event types:
    scheduled_task_dispatched / completed / failed
    scheduled_task_circuit_breaker_tripped / reset
    scheduled_task_disabled / enabled
- HTTP control surface under /scheduler/: status, list, trigger,
  enable, disable, reset.

Tranche plan:
  T1 (Burst 85, this) — design ADR
  T2 (Burst 86) — runtime + persistence + lifespan integration
                  + tool_call task type + status/list HTTP
  T3 (Burst 87) — scenario task type + YAML loader + step interpreter
  T4 (Burst 88) — port FizzBuzz scenario to YAML + run through scheduler
  T5 (Burst 89) — operator control endpoints + tests + runbook

What this unblocks:
- Operator's stated workflow: 'set and forget orchestrator to
  start and restart coding agents for testing'.
- ADR-0036 T4 (verifier 24h scan).
- Y5 ambient mode (proper periodic nudges instead of scaffolding).
- Future periodic work: drift checks, cleanup, regression suites.

Why in-process not separate service: one process to monitor, one
audit chain, no IPC, governance invariants stay intact. The
multi-tenant cloud story for v0.4 app platform is solved at THAT
layer, not here.

Open questions deferred to T2 implementation:
- approval-queue interaction (T2 picks 'refuse to schedule tools
  with requires_human_approval' for safety; permissive option
  queued for later)
- rate-limit interaction (scheduler rotates session_ids daily to
  avoid hitting per-session call caps)
- config reload (T2 picks 'requires daemon restart' for simplicity)

Test suite stays at 2072 (no source code changed, doc-only burst).

Next: Burst 86 — T2 runtime substrate + tool_call type + minimal HTTP."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 85 landed. ADR-0041 design filed."
echo "Next: Burst 86 — T2 scheduler runtime + tool_call type."
echo ""
read -rp "Press Enter to close..."
