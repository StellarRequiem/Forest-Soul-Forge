#!/usr/bin/env bash
# Reproduce the chat-tab errors via direct API calls.
#
# Captures:
#   - Daemon health
#   - Existing conversations + statuses
#   - launchd plist state (logs path, last exit, etc.)
#   - End-to-end test: create room, add status_reporter, send a turn
#     with auto_respond=true, capture HTTP code + body
#
# Output goes to terminal so we can screenshot.
# Read-only on existing data; only mutates by creating a TEST room.

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
# B149 (T25 follow-on): load FSF_API_TOKEN from .env if not in shell env
if [[ -f dev-tools/_fsf-env.sh ]]; then source dev-tools/_fsf-env.sh; fi

DAEMON="http://127.0.0.1:7423"
TOKEN="${FSF_API_TOKEN:-}"
auth() { [[ -n "$TOKEN" ]] && echo "-H X-FSF-Token: $TOKEN" || echo ""; }

bar() { printf '\n========== %s ==========\n' "$1"; }

bar "1. daemon /healthz"
curl -s "$DAEMON/healthz" | python3 -m json.tool 2>/dev/null | head -20 || echo "  ✗ daemon not reachable"

bar "2. existing conversations (last 10)"
curl -s "$DAEMON/conversations?limit=10" 2>/dev/null \
  | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    convs = d.get('conversations', [])
    print(f'  count: {len(convs)}')
    for c in convs[:10]:
        print(f\"    {c['conversation_id'][:12]}  status={c['status']}  domain={c.get('domain','?')}  created={c.get('created_at','?')[:19]}\")
except Exception as e:
    print(f'  parse failed: {e}')
"

bar "3. launchd state for dev.forest.daemon"
launchctl print "gui/$(id -u)/dev.forest.daemon" 2>/dev/null | grep -E "(state|last exit|stdout|stderr|path|program =)" | head -10 || echo "  (launchctl print failed — agent not loaded?)"

bar "4. log file presence + tails"
for f in /tmp/forest-daemon.out.log /tmp/forest-daemon.err.log /tmp/ollama.out.log /tmp/ollama.err.log; do
  if [[ -f "$f" ]]; then
    sz=$(wc -c < "$f" | tr -d ' ')
    echo "  ✓ $f ($sz bytes)"
  else
    echo "  ✗ $f (missing)"
  fi
done

bar "5. END-TO-END TEST: create room → add agent → send turn"

echo "  5a. creating test conversation..."
# Schema requires retention_policy as literal string, not object.
create_resp=$(curl -s -X POST "$DAEMON/conversations" \
  -H "Content-Type: application/json" $(auth) \
  -d '{"domain":"diag","operator_id":"alex","retention_policy":"full_7d"}')
test_cid=$(echo "$create_resp" | python3 -c "import json,sys; print(json.load(sys.stdin).get('conversation_id',''))" 2>/dev/null)
if [[ -z "$test_cid" ]]; then
  echo "    ✗ create failed:"
  echo "      ${create_resp:0:300}"
  echo ""
  echo "Press return to close."
  read -r _
  exit 1
fi
echo "    ✓ created: $test_cid"

# Pick the first born specialist agent
agent_id=$(curl -s "$DAEMON/agents" \
  | python3 -c "import json,sys; agents=json.load(sys.stdin).get('agents',[]);
print([a['instance_id'] for a in agents if a.get('role')=='status_reporter'][0] if any(a.get('role')=='status_reporter' for a in agents) else '')")
if [[ -z "$agent_id" ]]; then
  echo "    ✗ no status_reporter agent found"
  echo ""
  echo "Press return to close."
  read -r _
  exit 1
fi
echo "    using agent: $agent_id"

echo "  5b. adding status_reporter as participant..."
add_resp=$(curl -s -X POST "$DAEMON/conversations/$test_cid/participants" \
  -H "Content-Type: application/json" $(auth) \
  -d "{\"instance_id\":\"$agent_id\",\"operator_id\":\"alex\"}")
add_code=$(echo "$add_resp" | python3 -c "import json,sys; d=json.load(sys.stdin); print('OK' if 'instance_id' in d else f\"FAIL: {d.get('detail','?')}\")" 2>/dev/null)
echo "    $add_code"

echo "  5c. sending turn with auto_respond=true..."
echo "       (this is the call the user reports failing)"
turn_resp=$(curl -s -w "\n__HTTP_CODE__%{http_code}" -X POST "$DAEMON/conversations/$test_cid/turns" \
  -H "Content-Type: application/json" $(auth) \
  -d "{\"role\":\"operator\",\"author_id\":\"alex\",\"body\":\"Hi, please give me a brief one-line status check.\",\"auto_respond\":true,\"addressed_to\":[\"$agent_id\"]}")
turn_code=$(echo "$turn_resp" | grep -oE '__HTTP_CODE__[0-9]+' | tr -d '_' | tr -d '[A-Z]')
turn_body=$(echo "$turn_resp" | sed 's/__HTTP_CODE__[0-9]*//')
echo "    HTTP code: $turn_code"
echo "    body (first 600 chars):"
echo "$turn_body" | head -c 600
echo ""

bar "6. cleanup test conversation (archive, since true delete doesn't exist)"
curl -s -X POST "$DAEMON/conversations/$test_cid/status" \
  -H "Content-Type: application/json" $(auth) \
  -d '{"status":"archived","reason":"diagnostic test cleanup"}' >/dev/null
echo "  archived $test_cid"

bar "7. tail any new log entries"
[[ -f /tmp/forest-daemon.err.log ]] && tail -20 /tmp/forest-daemon.err.log | sed 's/^/    /'
[[ -f /tmp/forest-daemon.out.log ]] && tail -10 /tmp/forest-daemon.out.log | sed 's/^/    /'

echo ""
echo "Done. Press return to close."
read -r _
