#!/usr/bin/env bash
# Forest Soul Forge — Security Swarm bring-up (ADR-0033 Phase D3).
#
# Births all 9 swarm agents against a running daemon. Each birth uses
# the role's archetype kit from tool_catalog.yaml — no per-request
# tool overrides needed since Phase D1 wired everything in.
#
# Usage:
#   ./scripts/security-swarm-birth.sh
#
# Prereqs:
#   - daemon up at $FSF_DAEMON_URL (default http://127.0.0.1:7423)
#   - jq, curl
#
# Env:
#   FSF_DAEMON_URL   override the daemon URL
#   FSF_API_TOKEN    auth token if writes are protected
#   FSF_ENRICH       set to "true" to LLM-enrich each soul.md (slow);
#                    default "false" for fast deterministic birth.
#
# Outputs each agent's instance_id + dna to stdout, suitable for piping
# into the smoke test or the install-skills runbook.
#
# Exit codes:
#   0  all 9 agents born
#   1  any birth failed; partial output preserved

set -uo pipefail

DAEMON="${FSF_DAEMON_URL:-http://127.0.0.1:7423}"
TOKEN="${FSF_API_TOKEN:-}"
ENRICH="${FSF_ENRICH:-false}"

ROLES=(
  "patch_patrol:PatchPatrol"
  "gatekeeper:Gatekeeper"
  "log_lurker:LogLurker"
  "anomaly_ace:AnomalyAce"
  "net_ninja:NetNinja"
  "response_rogue:ResponseRogue"
  "zero_zero:ZeroZero"
  "vault_warden:VaultWarden"
  "deception_duke:DeceptionDuke"
)

require() { command -v "$1" >/dev/null 2>&1 || { echo "missing: $1" >&2; exit 2; }; }
require curl
require jq

auth() { [[ -n "$TOKEN" ]] && echo "-H X-FSF-Token: $TOKEN" || echo ""; }

birth() {
  local role="$1" name="$2"
  local payload
  payload=$(jq -n --arg role "$role" --arg name "$name" --argjson enrich "$ENRICH" '{
    profile: {role: $role, trait_values: {}, domain_weight_overrides: {}},
    agent_name: $name,
    agent_version: "v1",
    enrich_narrative: $enrich
  }')
  local resp
  resp=$(curl -sf -X POST "$DAEMON/birth" \
    -H "Content-Type: application/json" \
    $(auth) \
    -d "$payload")
  if [[ -z "$resp" ]]; then
    echo "FAIL  $role" >&2
    return 1
  fi
  echo "$resp" | jq -r --arg role "$role" --arg name "$name" \
    '"OK    \($role | tostring | .[:14] | (. + "              ")[:14])  \($name)  instance=\(.instance_id)  dna=\(.dna)"'
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
echo "all 9 swarm agents born"
