#!/bin/bash
# Burst 398 - ADR-0081 T5: scheduled task + runbook.
#
# Fifth implementation tranche. The piece that turns the
# WiringSentinel from "exists and has a skill" into "runs
# automatically every 4 hours." Plus the operator playbook.
#
# What this commit adds:
#
# 1. dev-tools/run-wiring-audit.command (NEW)
#    3-step wrapper invoked by launchd:
#      (1) Run section-15-wiring-cross-check (regenerates
#          coverage.json from current substrate).
#      (2) Resolve WiringSentinel's instance_id via /agents.
#      (3) Read coverage.json from disk + dispatch wiring_audit.v1
#          via curl with coverage as inputs + triggered_by from
#          FSF_WIRING_AUDIT_TRIGGER env var (default: "scheduled").
#    Soft-fail: section-15 rc=0/1 both valid; rc>=2 aborts. No
#    sentinel = exit 3. Skill ok=false = exit 4. The launchd plist
#    does NOT KeepAlive on failure; next 4-hour tick retries.
#
# 2. dev-tools/launchd/dev.forest.wiring-audit.plist.template (NEW)
#    LaunchAgent plist template. StartInterval=14400 (4 hours,
#    ADR-0081 D7). RunAtLoad=false (cadence-only, no piling-up
#    at restarts). Logs to /tmp/forest-wiring-audit.{out,err}.log.
#    Install path: ~/Library/LaunchAgents/dev.forest.wiring-audit.plist.
#    Bootstrap: launchctl bootstrap gui/$(id -u) <plist>.
#
# 3. docs/runbooks/wiring-audit.md (NEW)
#    Operator playbook covering:
#      - At-a-glance: 3 moving parts (section-15 + sentinel + plist).
#      - What each section-15 check catches.
#      - Severity scale (info/low/medium/high).
#      - One-time install: birth + plist install + first run.
#      - Reading outputs: HTML, coverage.json, sentinel memory,
#        launchd logs.
#      - Recovery for 5 common failure modes (no sentinel,
#        section-15 crash, skill ok=false, false positives,
#        expected-but-unsolved gaps).
#      - Extending the audit.
#      - Reference links to ADR-0081 + all 6 burst commits.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: T3 sentinel + T4 skill exist, but without T5 they
#     only run when the operator remembers to dispatch. The B363
#     gap class went undetected for months because of exactly
#     that — operator-driven manual triggers don't scale. The
#     4-hour cadence is the discipline ADR-0081 was written to
#     systematize.
#   Prove non-load-bearing: ADDITIONS only. Wrapper script +
#     plist template + runbook. No substrate or pipeline change.
#     Plist install is opt-in (operator step in the runbook);
#     this commit doesn't auto-activate the cadence.
#   Prove alternative is strictly better:
#     (a) "operator runs the wrapper by hand" - what we had;
#         doesn't scale, that's the B363 lesson.
#     (b) "fold cadence into daemon scheduler" - the daemon
#         scheduler runs tool_call tasks, not multi-step
#         shell+skill pipelines. Adding a new task_type for
#         this would couple substrate code to filesystem reads
#         that the guardian read_only ceiling forbids.
#     (c) "system cron instead of launchd" - launchd is the
#         macOS-native scheduler + matches the existing
#         dev.forest.daemon plist pattern. Cron would diverge.
#
# Verification after this commit lands:
#   1. bash dev-tools/run-wiring-audit.command (manual run)
#      Expected: section-15 runs, sentinel ID resolved, skill
#      dispatched with ok=true, lineage memory gains one
#      wiring_audit_outcome entry.
#   2. Install the plist:
#      cp dev-tools/launchd/dev.forest.wiring-audit.plist.template \
#         ~/Library/LaunchAgents/dev.forest.wiring-audit.plist
#      launchctl bootstrap gui/\$(id -u) \
#         ~/Library/LaunchAgents/dev.forest.wiring-audit.plist
#   3. Verify it's loaded:
#      launchctl print gui/\$(id -u)/dev.forest.wiring-audit | head
#      Expected: state=running, no spawn errors.
#   4. (After 4 hours OR launchctl kickstart -k <label>):
#      tail /tmp/forest-wiring-audit.out.log
#      Expected: 3 steps complete + ok=true + sentinel memory
#      gains an outcome entry.
#   5. Read the runbook end-to-end. Confirm install steps + recovery
#      modes match disk state.
#
# What this UNBLOCKS / queues next:
#   T6: CLOSE - live verify (operator runs the wrapper once + checks
#       the lineage memory has the expected outcome shape) + north-
#       star update (ADR-0081 6/6 closed) + ADR-0081 status: Accepted.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/run-wiring-audit.command \
        dev-tools/launchd/dev.forest.wiring-audit.plist.template \
        docs/runbooks/wiring-audit.md \
        dev-tools/commit-bursts/commit-burst398-adr0081-t5-scheduled-task-runbook.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(schedule): 4-hour wiring audit cadence + runbook (ADR-0081 T5, B398)

Burst 398. Fifth tranche of ADR-0081. Turns the WiringSentinel
from 'exists and has a skill' into 'runs automatically every 4
hours.' Plus the operator playbook.

dev-tools/run-wiring-audit.command (NEW):
  3-step launchd wrapper:
    1. Run section-15-wiring-cross-check (regenerate coverage.json).
    2. Resolve WiringSentinel instance_id via /agents.
    3. Read coverage.json + dispatch wiring_audit.v1 via curl
       with coverage as inputs + triggered_by from
       FSF_WIRING_AUDIT_TRIGGER env (default 'scheduled').
  Soft-fail: section-15 rc=0/1 both valid; rc>=2 aborts. No
  sentinel = exit 3. Skill ok=false = exit 4. Plist has no
  KeepAlive; next 4-hour tick retries.

dev-tools/launchd/dev.forest.wiring-audit.plist.template (NEW):
  LaunchAgent template. StartInterval=14400 (4h, ADR-0081 D7).
  RunAtLoad=false (cadence-only). Logs to /tmp/forest-wiring-
  audit.{out,err}.log. Bootstrap by hand per runbook.

docs/runbooks/wiring-audit.md (NEW):
  Operator playbook: at-a-glance (3 moving parts), what
  section-15 catches, severity scale, one-time install, reading
  outputs (HTML/JSON/sentinel memory/launchd logs), recovery
  for 5 common failure modes, extending the audit, reference
  links.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: T3+T4 run only when operator remembers. B363 went
    months because manual triggers don't scale. The 4-hour
    cadence IS the discipline ADR-0081 systematizes.
  Prove non-load-bearing: ADDITIONS only. Plist install is opt-
    in operator step; commit doesn't auto-activate.
  Prove alternative is better:
    (a) operator-by-hand = what we had.
    (b) daemon scheduler = couples substrate code to filesystem
        reads that read_only ceiling forbids.
    (c) cron = diverges from existing launchd pattern.

T6 queued: CLOSE - live verify + north-star + Accepted."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 398 complete - ADR-0081 T5 shipped ==="
echo "=========================================================="
echo "Verify:"
echo "  bash dev-tools/run-wiring-audit.command"
echo "  Then install the launchd plist per docs/runbooks/wiring-audit.md."
echo ""
echo "Next: T6 (CLOSE - live verify + north-star + ADR Accepted)."
echo ""
echo "Press any key to close."
read -n 1 || true
