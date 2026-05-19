#!/usr/bin/env bash
# Quick debug — show full response body when birthing system_architect.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
DAEMON="${FSF_DAEMON_URL:-http://127.0.0.1:7423}"

echo "=== POST /birth system_architect (verbose) ==="
payload=$(jq -n --arg name "Atlas_debug_$(date +%s)" '{
  profile: {role: "system_architect", trait_values: {}, domain_weight_overrides: {}},
  agent_name: $name,
  agent_version: "v1",
  enrich_narrative: false
}')
echo "Payload: $payload"
echo ""
echo "Response:"
curl -s -w "\n=== HTTP %{http_code} ===\n" -X POST "$DAEMON/birth" \
  -H "Content-Type: application/json" -d "$payload"

echo ""
echo "=== GET /traits — verify system_architect role appears ==="
curl -sf "$DAEMON/traits" | python3 -c "
import json, sys
d = json.load(sys.stdin)
roles = d.get('roles', [])
print(f'Total roles: {len(roles)}')
# Find new ones
new_roles = [r for r in roles if r.get('name') in ('system_architect', 'software_engineer', 'code_reviewer')]
print(f'New SW roles found: {len(new_roles)}')
for r in new_roles:
    print(f'  ✓ {r[\"name\"]}')
"

echo ""
echo "=== GET /tools/registered — verify llm_think ==="
curl -sf "$DAEMON/tools/registered" | python3 -c "
import json, sys
d = json.load(sys.stdin)
tools = d.get('tools', [])
llm = [t for t in tools if t.get('name') == 'llm_think']
print(f'llm_think registered: {len(llm) > 0}')
for t in llm:
    print(f'  ✓ {t[\"name\"]}.v{t[\"version\"]}  side_effects={t[\"side_effects\"]}')
"

echo ""
echo "Press return to close."
read -r _
