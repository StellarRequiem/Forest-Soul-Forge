#!/usr/bin/env bash
# Birth the 24/7 specialist agent stable. Thin Finder-launchable
# wrapper around scripts/birth-specialist-stable.sh. Closes T6.
#
# 6 agents land via POST /birth:
#   dashboard_watcher    (observer-tier)     — DashboardWatcher
#   signal_listener      (observer-tier)     — SignalListener
#   incident_correlator  (investigator-tier) — IncidentCorrelator
#   paper_summarizer     (researcher-tier)   — PaperSummarizer
#   vendor_research      (researcher-tier)   — VendorResearch
#   status_reporter      (communicator-tier) — StatusReporter
#
# All 6 use their role's archetype kit from tool_catalog.yaml. No
# trait sliders set — engine uses role defaults.
#
# Idempotent at the data level (re-runs create NEW instances; original
# instances stay intact and you can /archive any duplicates via the
# Forge UI Agents tab).

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
# B149 (T25 follow-on): load FSF_API_TOKEN from .env if not in shell env
if [[ -f dev-tools/_fsf-env.sh ]]; then source dev-tools/_fsf-env.sh; fi

bar() { printf '\n========== %s ==========\n' "$1"; }

bar "1. preflight: daemon reachable?"
if curl -fsS --max-time 3 http://127.0.0.1:7423/healthz >/dev/null 2>&1; then
  echo "  ✓ daemon up at 127.0.0.1:7423"
else
  echo "  ✗ daemon not reachable — check 'launchctl list | grep dev.forest.daemon'"
  echo ""
  echo "Press return to close."
  read -r _
  exit 1
fi

bar "2. running scripts/birth-specialist-stable.sh"
./scripts/birth-specialist-stable.sh

bar "3. verify (current agents in registry)"
echo "  All specialist roles after this run:"
curl -fsS http://127.0.0.1:7423/agents \
  | jq -r '.agents[] | select(.role | IN("dashboard_watcher","signal_listener","incident_correlator","paper_summarizer","vendor_research","status_reporter")) | "    \(.role | (. + "                      ")[:22])  \(.agent_name | (. + "                ")[:18])  instance=\(.instance_id)"' \
  2>/dev/null || echo "    (jq parse failed — check curl manually)"

bar "4. next steps"
cat <<'EOF'
  The 6 specialists are now born and available. They can:
    - Dispatch tools manually via the Forge UI (Agents tab → click agent
      → use Skills tab to run skills against them) or HTTP:
        POST /agents/{instance_id}/tools/call
    - Be invited into Chat-tab conversations as participants
    - Have skill manifests installed against them

  To activate scheduled (24/7 cron-style) work for them:
    1. cp config/scheduled_tasks.yaml.example config/scheduled_tasks.yaml
    2. Replace REPLACE_WITH_*_INSTANCE_ID placeholders with the
       instance_ids printed above (use jq from the verify section)
    3. Set 'enabled: true' on the tasks you want to fire
    4. Restart the Forest daemon to pick up the config:
        launchctl kickstart -k gui/$(id -u)/dev.forest.daemon
    5. Verify via:
        curl -s http://127.0.0.1:7423/scheduler/tasks | jq '.tasks[] | {id, enabled, state}'

  Trigger a task on demand (without waiting for its schedule):
    curl -X POST http://127.0.0.1:7423/scheduler/tasks/<id>/trigger \
      -H "X-FSF-Token: $FSF_API_TOKEN"
EOF

echo ""
echo "Press return to close."
read -r _
