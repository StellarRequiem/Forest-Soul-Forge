#!/usr/bin/env bash
# Burst 92: lock v0.4.0-rc — STATE/README/CHANGELOG refresh + annotated tag.
#
# This is the checkpoint between the now-shipped scheduler arc
# (Bursts 85-91 = ADR-0041 T1+T2+T3+T5+T6) and the still-outstanding
# T4 scenario task type. Tagging here gives operators a usable
# version with the production-grade tool_call-only scheduler before
# the speculative scenario surface lands.
#
# What this commit lands:
#
# 1. STATE.md refresh — last-updated date 2026-05-04, test count
#    2072 → 2129, schema version v12 → v13, audit event types
#    55 → 62, .command count 88 → 104, total commits 234 → 247,
#    ADR-0041 row added to the ADR table, ADR-0036 T4 status
#    updated to reflect that ADR-0041 closes it.
#
# 2. README.md refresh — same numerics (test count, ADR count,
#    audit event types, schema version, .command count). Plus
#    explicit ADR-0041 mention in the headline ADR list.
#
# 3. CHANGELOG.md — full [0.4.0-rc] section covering all 6 bursts
#    (85, 86, 89, 90, 91 for ADR-0041 + 86.1/86.2/86.3/88 for the
#    frontend hotfixes + 87 for the roadmap doc). Includes the
#    Chrome MCP audit-pattern method note as durable artifact for
#    future arcs.
#
# 4. v0.4.0-rc annotated tag — points at the commit lands above.
#    Tag message captures the ADR-0041 status and explicitly
#    notes T4 still outstanding so operators know what they're
#    getting.
#
# Why -rc rather than v0.4.0:
# - T4 (scenario task type) is a substantial chunk on its own
#   (multi-step DSL, scenario YAML loader, FizzBuzz port). Better
#   to ship two clean tags than one delayed one.
# - Operators using the scheduler today only need tool_call —
#   verifier_scan, future health-check tasks, anything that maps
#   1:1 to a tool dispatch. Scenario-class workflows (FizzBuzz
#   coding loops, etc.) are useful but not the load-bearing case.
# - -rc signals: "production-ready for the listed surface, more
#   coming." If T4 reveals new requirements that need an ADR
#   amendment, doing it before v0.4.0 final is cleaner than
#   shipping a half-finished feature.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 92 — v0.4.0-rc lock: docs refresh + tag ==="
echo
clean_locks
git add STATE.md README.md CHANGELOG.md
git add tag-v0.4.0-rc.command
clean_locks
git status --short
echo
clean_locks
git commit -m "docs: refresh STATE/README/CHANGELOG for v0.4.0-rc

Locks the checkpoint between the now-shipped scheduler arc
(Bursts 85-91 = ADR-0041 T1+T2+T3+T5+T6) and the still-outstanding
T4 scenario task type.

STATE.md:
- Last-updated 2026-05-02 → 2026-05-04
- Tests 2072 → 2129 (+57 across the v0.4-rc arc)
- Schema version v12 → v13 (scheduled_task_state added)
- Audit event types 55 → 62 (7 ADR-0041 scheduler events)
- Operator .command scripts 88 → 104
- Total main commits 234 → 247
- ADR table: ADR-0041 row added with T1+T2+T3+T5+T6 shipped + T4
  outstanding; ADR-0036 T4 status updated to reflect closure by
  ADR-0041 T3 (configure a tool_call task with tool_name=verifier_scan)
- Header summary rewritten to lead with the v0.4-rc arc

README.md:
- Tests 2,072 → 2,129
- ADRs filed 37/35 → 38/36 (ADR-0041 added)
- Audit event types 55 → 62 with explicit scheduler-event list
- Schema version v12 → v13
- Operator .command scripts 88 → 104

CHANGELOG.md:
- Full [0.4.0-rc] section covering 9 bursts (85, 86, 86.1, 86.2,
  86.3, 87, 88, 89, 90, 91 — chronologically the work in the arc)
- Per-burst commit hashes recorded so the chain is followable
- Method note: the Chrome MCP frontend audit pattern that found
  6 latent UX bugs in one session — should become a pre-tag
  checklist for future v0.x.0 releases

The tag itself lands as annotated v0.4.0-rc via tag-v0.4.0-rc.command
right after this commit pushes. Tagging in a separate step keeps
the commit-vs-tag history clean (the tag points at THIS commit's
hash, not at the tag-script's commit)."

clean_locks
git push origin main
clean_locks

# ---- Tag the freshly-pushed commit -----------------------------------
echo
echo "=== Annotated tag: v0.4.0-rc ==="

# Use a heredoc-free message (commit-script-backtick-gotcha lesson
# encoded in CLAUDE.md) — single-quote the entire -m so backticks
# don't trigger command substitution.
clean_locks
git tag -a v0.4.0-rc -m 'v0.4.0-rc: ADR-0041 Set-and-Forget Orchestrator (tool_call-only)

Production-grade scheduler for tool_call tasks. Configure tasks
in config/scheduled_tasks.yaml; the daemon dispatches them on
cadence through the standard ToolDispatcher. State survives
daemon restarts via SQLite v13. Operators trigger / pause /
resume / unblock without bouncing the daemon.

Closes ADR-0036 T4 (Verifier Loop scheduled scans), deferred
since Burst 67 because the substrate did not exist.

ADR-0041 tranches in this -rc:
  T1 design (Burst 85)
  T2 runtime + lifespan integration (Burst 86)
  T3 tool_call task type + audit emit (Burst 89)
  T5 SQLite v13 persistence (Burst 90)
  T6 operator control endpoints (Burst 91)

Outstanding (will land in v0.4.0):
  T4 scenario task type — multi-step birth + seed + iterate +
  archive DSL; FizzBuzz YAML port to replace
  live-test-fizzbuzz.command bash driver.

Test suite: 2072 → 2129 passing (+57). Zero regressions.
Schema bump v12 → v13 (pure addition, scheduled_task_state).
6 latent frontend UX bugs found and fixed via Chrome MCP audit
across Bursts 86.1, 86.2, 86.3, 88.

See CHANGELOG.md for the full per-burst breakdown.'

clean_locks
git push origin v0.4.0-rc
clean_locks
git log -1 --oneline
echo
echo "v0.4.0-rc landed. ADR-0041 production-grade tool_call-only scheduler is now"
echo "tagged as a checkpoint. T4 scenario task type follows in v0.4.0 final."
echo ""
read -rp "Press Enter to close..."
