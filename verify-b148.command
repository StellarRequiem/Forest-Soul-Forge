#!/usr/bin/env bash
# B148 verification — restart daemon to load auth-required-by-default,
# confirm token auto-generated to .env, exercise write endpoint with
# and without token.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

bar() { printf '\n========== %s ==========\n' "$1"; }

bar "1. capture pre-state of .env"
if [[ -f .env ]]; then
  echo "  current FSF_API_TOKEN line in .env (if any):"
  grep -E '^FSF_API_TOKEN=' .env | sed 's/^/    /' || echo "    (none)"
else
  echo "  .env does not exist"
fi

bar "2. restart Forest daemon to load B148"
launchctl kickstart -k "gui/$(id -u)/dev.forest.daemon" \
  && echo "  ✓ kickstart -k sent" \
  || { echo "  ✗ kickstart failed"; echo "Press return to close."; read -r _; exit 1; }

bar "3. wait up to 20s for /healthz"
for i in $(seq 1 20); do
  if curl -fsS --max-time 1 http://127.0.0.1:7423/healthz >/dev/null 2>&1; then
    echo "  ✓ daemon back up after ${i}s"
    break
  fi
  printf '.'; sleep 1
done
echo

bar "4. read auth section of /healthz startup_diagnostics"
curl -fsS http://127.0.0.1:7423/healthz \
  | python3 -c "
import json, sys
d = json.load(sys.stdin)
diags = d.get('startup_diagnostics', [])
auth_diags = [x for x in diags if x.get('component') == 'auth']
if not auth_diags:
    print('  (no auth diagnostic found — bug?)')
else:
    for a in auth_diags:
        print(f\"  status={a.get('status')}  msg={a.get('message') or a.get('warning') or ''}\")
"

bar "5. read updated .env to find the auto-generated token"
if [[ -f .env ]]; then
  TOKEN=$(grep -E '^FSF_API_TOKEN=' .env | tail -1 | cut -d= -f2- | tr -d '"' | tr -d "'")
  if [[ -n "$TOKEN" ]]; then
    # Show only first 8 + last 4 chars so logs aren't a leak vector
    echo "  ✓ token in .env: ${TOKEN:0:8}…${TOKEN: -4}"
  else
    echo "  ✗ no FSF_API_TOKEN line in .env after restart"
    TOKEN=""
  fi
else
  echo "  ✗ .env doesn't exist after restart"
  TOKEN=""
fi

bar "6. attempt write WITHOUT token (should 401)"
http_code=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
  http://127.0.0.1:7423/conversations \
  -H "Content-Type: application/json" \
  -d '{"domain":"b148-verify-noauth","operator_id":"alex","retention_policy":"full_7d"}')
if [[ "$http_code" == "401" ]]; then
  echo "  ✅ B148 ENFORCEMENT VERIFIED — write rejected with 401 (no token)"
else
  echo "  ⚠️ unexpected HTTP $http_code (expected 401). Auth may not be enforcing."
fi

bar "7. attempt write WITH token (should 201)"
if [[ -z "$TOKEN" ]]; then
  echo "  (skipped — no token captured in step 5)"
else
  resp=$(curl -s -w "\n__HTTP__%{http_code}" -X POST \
    http://127.0.0.1:7423/conversations \
    -H "Content-Type: application/json" \
    -H "X-FSF-Token: $TOKEN" \
    -d '{"domain":"b148-verify-auth","operator_id":"alex","retention_policy":"full_7d"}')
  http_code=$(echo "$resp" | grep -oE '__HTTP__[0-9]+' | tr -d '_HTP')
  test_cid=$(echo "$resp" | sed 's/__HTTP__[0-9]*//' \
    | python3 -c "import json,sys; print(json.load(sys.stdin).get('conversation_id',''))" 2>/dev/null)
  if [[ "$http_code" == "201" ]]; then
    echo "  ✅ token-authenticated write succeeded (HTTP 201)"
    echo "     created test conversation: $test_cid"
    # Cleanup
    curl -s -X POST "http://127.0.0.1:7423/conversations/$test_cid/status" \
      -H "Content-Type: application/json" \
      -H "X-FSF-Token: $TOKEN" \
      -d '{"status":"archived","reason":"b148 verify cleanup"}' >/dev/null
    echo "     archived $test_cid"
  else
    echo "  ⚠️ write WITH token returned HTTP $http_code (expected 201)"
    echo "     body: $(echo "$resp" | sed 's/__HTTP__[0-9]*//' | head -c 300)"
  fi
fi

echo ""
echo "Done. Press return to close."
read -r _
