#!/usr/bin/env bash
# Restart Forest daemon (to reload tool_catalog.yaml after kit fix) and
# birth the dashboard_watcher specialist that failed in
# birth-specialist-stable.command's first run.
#
# Why a separate script: the kit-tier violation discovered in T6 close
# was a config bug in dashboard_watcher's standard_tools (web_fetch.v1
# is network-class, but observer-genre's ceiling is read_only). The fix
# landed in tool_catalog.yaml; the daemon caches the parsed catalog at
# startup, so a restart is needed to pick it up.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
# B149 (T25 follow-on): load FSF_API_TOKEN from .env if not in shell env
if [[ -f dev-tools/_fsf-env.sh ]]; then source dev-tools/_fsf-env.sh; fi

bar() { printf '\n========== %s ==========\n' "$1"; }

bar "1. restart Forest daemon via launchctl"
launchctl kickstart -k "gui/$(id -u)/dev.forest.daemon" \
  && echo "  ✓ kickstart -k sent to dev.forest.daemon" \
  || echo "  ✗ kickstart failed (is the LaunchAgent loaded?)"

bar "2. wait up to 20s for /healthz"
for i in $(seq 1 20); do
  if curl -fsS --max-time 1 http://127.0.0.1:7423/healthz >/dev/null 2>&1; then
    echo "  ✓ daemon back up after ${i}s"
    break
  fi
  printf '.'
  sleep 1
done
echo

bar "3. birth dashboard_watcher"
TOKEN="${FSF_API_TOKEN:-}"
auth() { [[ -n "$TOKEN" ]] && echo "-H X-FSF-Token: $TOKEN" || echo ""; }

payload='{
  "profile": {"role": "dashboard_watcher", "trait_values": {}, "domain_weight_overrides": {}},
  "agent_name": "DashboardWatcher",
  "agent_version": "v1",
  "enrich_narrative": false
}'

tmp="$(mktemp)"
http_code=$(curl -s -o "$tmp" -w "%{http_code}" -X POST http://127.0.0.1:7423/birth \
  -H "Content-Type: application/json" $(auth) -d "$payload")
body="$(cat "$tmp")"; rm -f "$tmp"

if [[ "$http_code" == "200" || "$http_code" == "201" ]]; then
  echo "$body" | jq -r '"  ✓ born: instance=\(.instance_id)  dna=\(.dna)"' 2>/dev/null \
    || echo "  ✓ born (jq parse failed, raw body length=${#body})"
else
  echo "  ✗ birth failed (http=$http_code):"
  echo "    ${body:0:400}"
fi

bar "4. confirm 6/6 specialists in registry"
curl -fsS http://127.0.0.1:7423/agents \
  | jq -r '.agents[] | select(.role | IN("dashboard_watcher","signal_listener","incident_correlator","paper_summarizer","vendor_research","status_reporter")) | "    \(.role | (. + "                      ")[:22])  \(.agent_name | (. + "                ")[:18])  instance=\(.instance_id)"' \
  2>/dev/null || echo "    (jq parse failed — check /agents manually)"

echo ""
echo "Done. Press return to close."
read -r _
