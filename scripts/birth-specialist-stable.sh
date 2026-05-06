#!/usr/bin/env bash
# Forest Soul Forge — birth the 24/7 specialist agent stable.
#
# Births 6 specialist agents from the SESSION-HANDOFF §4d roster. Each
# uses its role's archetype kit from tool_catalog.yaml — no per-request
# tool overrides, no trait sliders set (engine uses role defaults).
#
# These agents are immediately useful for:
#   - Manual dispatch via the Forge UI / direct HTTP (POST /agents/{id}/tools/call)
#   - Skill manifest invocation
#   - Scheduled tasks via ADR-0041 (see config/scheduled_tasks.yaml.example)
#
# Idempotent — re-runs create new agent instances (different DNAs since
# Forest's DNA includes a birth seed by default). For a fixed-DNA stable,
# pass a deterministic agent_name and use the dna_seed override per
# birth (not implemented here — future improvement).
#
# Usage:
#   ./scripts/birth-specialist-stable.sh
#
# Prereqs:
#   - daemon up at $FSF_DAEMON_URL (default http://127.0.0.1:7423)
#   - jq, curl
#
# Env:
#   FSF_DAEMON_URL   override daemon URL
#   FSF_API_TOKEN    auth token if writes are protected
#   FSF_ENRICH       "true" for LLM-enriched soul.md (slow); default "false"
#
# Exit codes:
#   0  all 6 agents born
#   1  any birth failed; partial output preserved on stderr

set -uo pipefail

DAEMON="${FSF_DAEMON_URL:-http://127.0.0.1:7423}"
TOKEN="${FSF_API_TOKEN:-}"
ENRICH="${FSF_ENRICH:-false}"

# role:DisplayName mapping. Roles must exist in trait_tree.yaml (added in
# Burst 124 role expansion). Genres claim them per genres.yaml.
ROLES=(
  "dashboard_watcher:DashboardWatcher"
  "signal_listener:SignalListener"
  "incident_correlator:IncidentCorrelator"
  "paper_summarizer:PaperSummarizer"
  "vendor_research:VendorResearch"
  "status_reporter:StatusReporter"
)

require() { command -v "$1" >/dev/null 2>&1 || { echo "missing: $1" >&2; exit 2; }; }
require curl
require jq

auth() { [[ -n "$TOKEN" ]] && echo "-H X-FSF-Token: $TOKEN" || echo ""; }

# Sanity: daemon reachable?
if ! curl -fsS --max-time 3 "$DAEMON/healthz" >/dev/null 2>&1; then
  echo "FAIL: daemon not reachable at $DAEMON/healthz" >&2
  echo "  Check 'launchctl list | grep dev.forest.daemon' or run ./run.command" >&2
  exit 2
fi

birth() {
  local role="$1" name="$2"
  local payload http_code body tmp

  payload=$(jq -n --arg role "$role" --arg name "$name" --argjson enrich "$ENRICH" '{
    profile: {role: $role, trait_values: {}, domain_weight_overrides: {}},
    agent_name: $name,
    agent_version: "v1",
    enrich_narrative: $enrich
  }')

  tmp="$(mktemp)"
  http_code=$(curl -s -o "$tmp" -w "%{http_code}" -X POST "$DAEMON/birth" \
    -H "Content-Type: application/json" \
    $(auth) \
    -d "$payload")
  body="$(cat "$tmp")"; rm -f "$tmp"

  if [[ "$http_code" != "200" && "$http_code" != "201" ]]; then
    printf 'FAIL  %-22s  http=%s  body=%s\n' "$role" "$http_code" "${body:0:280}" >&2
    return 1
  fi

  echo "$body" | jq -r --arg role "$role" --arg name "$name" \
    '"OK    \($role | tostring | .[:22] | (. + "                      ")[:22])  \($name)  instance=\(.instance_id)  dna=\(.dna)"'
}

failed=0
for entry in "${ROLES[@]}"; do
  role="${entry%%:*}"
  name="${entry##*:}"
  birth "$role" "$name" || failed=$((failed + 1))
done

if (( failed > 0 )); then
  echo "$failed births failed" >&2
  exit 1
fi

echo ""
echo "All 6 specialist agents born."
echo ""
echo "Next steps:"
echo "  1. Inspect via:  curl -s $DAEMON/agents | jq '.agents[] | select(.role | IN(\"dashboard_watcher\",\"signal_listener\",\"incident_correlator\",\"paper_summarizer\",\"vendor_research\",\"status_reporter\"))'"
echo "  2. Configure scheduled tasks per config/scheduled_tasks.yaml.example"
echo "  3. Restart Forest daemon (via launchd:  launchctl kickstart -k gui/\$(id -u)/dev.forest.daemon)"
echo "     to pick up scheduled tasks at startup"
echo ""
