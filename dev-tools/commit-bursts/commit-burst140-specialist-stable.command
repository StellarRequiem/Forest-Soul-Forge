#!/bin/bash
# Burst 140 — birth the 24/7 specialist agent stable.
#
# Closes T6 of the cross-session task list. SESSION-HANDOFF §4d
# documented this as DESIGN-ONLY at handoff (no births yet); this
# burst delivers the working stable.
#
# What ships:
#
#   scripts/birth-specialist-stable.sh — POST /birth for 6 roles in
#     sequence: dashboard_watcher, signal_listener, incident_correlator,
#     paper_summarizer, vendor_research, status_reporter. Modeled on
#     scripts/security-swarm-birth.sh (same payload shape, same auth
#     flow, same OK/FAIL output line format).
#
#   birth-specialist-stable.command (new at repo root) — Finder-
#     launchable thin wrapper. Calls the .sh script + does a verify
#     pass via /agents + prints next-steps text covering manual
#     dispatch + scheduled-task activation.
#
#   config/scheduled_tasks.yaml.example — example task config for the
#     ADR-0041 set-and-forget orchestrator. One task per specialist,
#     all using the read_only llm_think tool (scheduler refuses tools
#     that need human approval), all enabled=false with
#     REPLACE_WITH_*_INSTANCE_ID placeholders. Operator activates by
#     copying to scheduled_tasks.yaml + filling instance IDs +
#     setting enabled: true + restarting daemon.
#
#   birth-dashboard-watcher.command (new at repo root) — small
#     follow-on helper. The first specialist-stable birth run
#     surfaced a kit-tier violation on dashboard_watcher (web_fetch in
#     standard_tools but observer-genre ceiling is read_only). After
#     fixing the kit, this script restarts the daemon to pick up the
#     new catalog + births dashboard_watcher individually.
#
#   config/tool_catalog.yaml — removes web_fetch.v1 from
#     dashboard_watcher's standard_tools. The kit-tier violation was
#     introduced in B124 role expansion; the role description ("passive
#     monitor of operator dashboards") doesn't need web reach. If a
#     future variant needs web reach, move it to web_observer genre
#     rather than widening observer's ceiling.
#
#   dev-tools/commit-bursts/commit-burst140-specialist-stable.command
#     — this file.
#
# Verified live 2026-05-05:
#   - First birth run: 5/6 OK, 1/6 FAIL (dashboard_watcher kit-tier
#     violation) — surfaced the catalog bug
#   - Kit fix landed; daemon restart via launchctl kickstart -k
#   - Second birth run: dashboard_watcher OK
#   - Final state: all 6 specialists in /agents registry
#   - Memory used 11.24 GB after both runs; pressure GREEN
#   - Specialists ready for manual dispatch via UI/HTTP/Chat
#
# Constitution-hash impact: removing web_fetch from
# dashboard_watcher's kit changes the constitution hash for any
# FUTURE dashboard_watcher births. The 5 already-born (non-dashboard)
# specialists are unaffected — their hashes were locked at birth time
# and constitutions are immutable. Only future dashboard_watcher
# instances pick up the new kit.
#
# Closes T6. Layer 4 (post-T6 follow-on) for Alex: edit
# scheduled_tasks.yaml.example into a real scheduled_tasks.yaml +
# substitute instance IDs + restart daemon. The substrate is here;
# activation is operator-side.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add scripts/birth-specialist-stable.sh \
        birth-specialist-stable.command \
        birth-dashboard-watcher.command \
        config/scheduled_tasks.yaml.example \
        config/tool_catalog.yaml \
        dev-tools/commit-bursts/commit-burst140-specialist-stable.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(specialist-stable): birth 6 specialist agents + kit fix (B140)

Burst 140. Closes T6 of the cross-session task list. SESSION-HANDOFF
section 4d documented this as DESIGN-ONLY at handoff (no births
yet); this burst delivers the working stable.

Ships:

- scripts/birth-specialist-stable.sh: POST /birth for 6 roles in
  sequence: dashboard_watcher, signal_listener, incident_correlator,
  paper_summarizer, vendor_research, status_reporter. Modeled on
  scripts/security-swarm-birth.sh (same payload shape, same auth,
  same output format).

- birth-specialist-stable.command (new at repo root): Finder-
  launchable wrapper. Calls the .sh + verifies via /agents + prints
  next-steps for manual dispatch + scheduled-task activation.

- config/scheduled_tasks.yaml.example: example task config for the
  ADR-0041 orchestrator. One read_only llm_think task per specialist,
  all enabled=false with REPLACE_WITH_*_INSTANCE_ID placeholders.
  Operator activates by cp + fill IDs + enabled: true + daemon
  restart.

- birth-dashboard-watcher.command (new at repo root): post-fix
  helper. First birth surfaced a kit-tier violation on
  dashboard_watcher (web_fetch network-class but observer ceiling
  is read_only). After kit fix, this restarts daemon to pick up the
  new catalog + births dashboard_watcher individually.

- config/tool_catalog.yaml: removes web_fetch.v1 from
  dashboard_watcher's standard_tools. Bug introduced in B124 role
  expansion. Role description (passive dashboard monitor) doesn't
  need web reach. If a variant needs it, move to web_observer genre
  rather than widening observer.

Verified live 2026-05-05:
- First run: 5/6 OK, 1/6 FAIL surfacing the catalog bug
- Kit fix + launchctl kickstart -k dev.forest.daemon
- Second run: dashboard_watcher OK
- All 6 specialists in /agents registry
- Memory 11.24 GB used, pressure GREEN
- Specialists ready for manual dispatch via UI/HTTP/Chat

Constitution-hash impact: removing web_fetch changes the hash for
FUTURE dashboard_watcher births. The 5 already-born non-dashboard
specialists are unaffected (their hashes locked at birth, immutable).
Only future dashboard_watcher births pick up the new kit.

Closes T6. Operator-side follow-on: copy scheduled_tasks.yaml.example
to scheduled_tasks.yaml + substitute instance IDs + restart daemon
to activate the cron-style runs. The substrate is here; activation
is the operator's choice."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 140 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
