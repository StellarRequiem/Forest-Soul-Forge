#!/usr/bin/env bash
# Burst 95: lock v0.4.0 final — STATE/README/CHANGELOG refresh + annotated tag.
#
# Supersedes v0.4.0-rc. Now that T4 (Burst 93) and the FizzBuzz
# YAML port (Burst 94) have landed, ADR-0041 is implementation-
# complete. v0.4.0 is the milestone tag covering the entire
# Set-and-Forget Orchestrator arc.
#
# What this commit lands:
#
# 1. STATE.md refresh — last-updated date + summary now reflects
#    all 5 implementation tranches shipped + FizzBuzz YAML port.
#    Test count 2129 → 2177. .command count 104 → 107. Total
#    commits 247 → 250. ADR-0041 row updated to mark T4 done and
#    drop the "outstanding" caveat.
#
# 2. README.md refresh — same numerics. ADR-0041 status updated
#    to "all 5 implementation tranches shipped + FizzBuzz YAML
#    scenario port".
#
# 3. CHANGELOG.md — new [0.4.0] section folds in Bursts 93 + 94
#    over the v0.4.0-rc material. Lists the new step type
#    (extract_code_block), new stop_when kind (pytest_passed),
#    and the FizzBuzz scenario YAML with operator usage example.
#    [Unreleased] reverts to "Nothing yet" stub. [0.4.0-rc] entry
#    preserved as-is for the historical record.
#
# 4. v0.4.0 annotated tag — points at the commit landed above.
#    Tag message captures the full arc: T1+T2+T3+T4+T5+T6 +
#    FizzBuzz YAML closing Burst 81 P1.
#
# This is the milestone where the operator's Run 001 / "set and
# forget orchestrator" request is structurally complete.
# Verifier-class agents from ADR-0036 can finally run on schedule
# (configure a tool_call task with tool_name=verifier_scan).
# Coding-loop scenarios run inside the daemon instead of as bash.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 95 — v0.4.0 final lock: docs refresh + tag ==="
echo
clean_locks
git add STATE.md README.md CHANGELOG.md
git add tag-v0.4.0.command
clean_locks
git status --short
echo
clean_locks
git commit -m "docs: refresh STATE/README/CHANGELOG for v0.4.0 final

Supersedes v0.4.0-rc. T4 (Burst 93) and the FizzBuzz YAML port
(Burst 94) have landed; ADR-0041 is implementation-complete.

STATE.md:
- Header summary now reads 'all 5 implementation tranches shipped'
  with explicit list (T1-T6, no longer T1+T2+T3+T5+T6 with T4
  outstanding)
- Tests 2129 → 2177 (+48 across Bursts 93 + 94)
- .command count 104 → 107
- Total main commits 247 → 250
- ADR-0041 row: T4 marked done, FizzBuzz YAML port noted as
  closing Burst 81 P1, v0.4.0 supersedes v0.4.0-rc

README.md:
- Tests 2,129 → 2,177
- ADR-0041 mention updated to 'all 5 implementation tranches'
- .command count 104 → 107

CHANGELOG.md:
- New [0.4.0] section covers what's-new since v0.4.0-rc:
  Burst 93 (T4 scenario task type runtime, +34 tests, runtime
  substrate with 4 step types and 3 stop_when kinds) and
  Burst 94 (FizzBuzz scenario YAML port, +14 tests, two minimal
  extensions extract_code_block and pytest_passed). Includes
  operator usage example for the scheduled FizzBuzz task.
- [Unreleased] reverts to 'Nothing yet' stub.
- [0.4.0-rc] entry preserved as-is for the historical record.

The tag itself lands as annotated v0.4.0 via tag-v0.4.0.command
right after this commit pushes."

clean_locks
git push origin main
clean_locks

# ---- Annotated tag ---------------------------------------------------
echo
echo "=== Annotated tag: v0.4.0 ==="

clean_locks
git tag -a v0.4.0 -m 'v0.4.0: ADR-0041 Set-and-Forget Orchestrator complete

The set-and-forget orchestrator the operator asked for during
the v0.3 arc. Configure tasks (tool_call or scenario) in
config/scheduled_tasks.yaml; the daemon dispatches them on
cadence through the standard ToolDispatcher (so all governance
applies); state survives daemon restarts via SQLite v13;
operators trigger / pause / resume / unblock without bouncing
the daemon.

Closes ADR-0036 T4 (Verifier Loop scheduled scans), deferred
since Burst 67 because the substrate did not exist.

ADR-0041 implementation tranches (all shipped):
  T1 design (Burst 85)
  T2 runtime + lifespan integration (Burst 86)
  T3 tool_call task type + audit emit (Burst 89)
  T4 scenario task type runtime (Burst 93)
  T5 SQLite v13 persistence (Burst 90)
  T6 operator control endpoints (Burst 91)

Plus the FizzBuzz scenario YAML port (Burst 94) — closes the
Burst 81 P1 audit item. The canonical autonomous coding loop
now runs inside the daemons asyncio loop on a schedule instead
of as a bash one-shot, with structured pytest stop-checks and
daily-rotating session_id semantics.

Test suite: 2072 -> 2177 passing (+105). Zero regressions.
Schema bump v12 -> v13 (pure addition, scheduled_task_state).
6 latent frontend UX bugs found and fixed via Chrome MCP audit
across the v0.4 arc.

Scenario step types in v0.4: read_file, write_file, dispatch_tool,
extract_code_block, iterate. stop_when kinds: var_truthy,
var_equals, pytest_passed.

See CHANGELOG.md [0.4.0] and [0.4.0-rc] for the full per-burst
breakdown.'

clean_locks
git push origin v0.4.0
clean_locks
git log -1 --oneline
echo
echo "v0.4.0 landed. ADR-0041 Set-and-Forget Orchestrator is feature-complete."
echo "Scheduler arc closed. Next development moves are non-scheduler-blocking."
echo ""
read -rp "Press Enter to close..."
