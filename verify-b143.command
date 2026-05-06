#!/usr/bin/env bash
# B143 verification — restart daemon to load per-thread connection
# proxy, then exercise the exact path that produced SQLITE_MISUSE +
# ConversationOut all-None: POST /conversations/{id}/turns with
# auto_respond=true.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
# B149 (T25 follow-on): load FSF_API_TOKEN from .env if not in shell env
if [[ -f dev-tools/_fsf-env.sh ]]; then source dev-tools/_fsf-env.sh; fi

bar() { printf '\n========== %s ==========\n' "$1"; }

bar "1. restart Forest daemon to load B143 code"
launchctl kickstart -k "gui/$(id -u)/dev.forest.daemon" \
  && echo "  ✓ kickstart -k sent" \
  || { echo "  ✗ kickstart failed"; echo "Press return to close."; read -r _; exit 1; }

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

bar "3. exercise the failing path: create + add + send turn (auto_respond)"
TOKEN="${FSF_API_TOKEN:-}"
auth() { [[ -n "$TOKEN" ]] && echo "-H X-FSF-Token: $TOKEN" || echo ""; }

create_resp=$(curl -s -X POST http://127.0.0.1:7423/conversations \
  -H "Content-Type: application/json" $(auth) \
  -d '{"domain":"b143-verify","operator_id":"alex","retention_policy":"full_7d"}')
test_cid=$(echo "$create_resp" | python3 -c "import json,sys; print(json.load(sys.stdin).get('conversation_id',''))" 2>/dev/null)
if [[ -z "$test_cid" ]]; then
  echo "  ✗ create failed: ${create_resp:0:240}"
  echo ""
  echo "Press return to close."
  read -r _
  exit 1
fi
echo "  ✓ created conversation: $test_cid"

agent_id=$(curl -s http://127.0.0.1:7423/agents \
  | python3 -c "import json,sys; agents=json.load(sys.stdin).get('agents',[]); m=[a['instance_id'] for a in agents if a.get('role')=='status_reporter']; print(m[0] if m else '')")
if [[ -z "$agent_id" ]]; then
  echo "  ✗ no status_reporter agent"; echo ""; echo "Press return to close."; read -r _; exit 1
fi
echo "  using agent: $agent_id"

curl -s -X POST "http://127.0.0.1:7423/conversations/$test_cid/participants" \
  -H "Content-Type: application/json" $(auth) \
  -d "{\"instance_id\":\"$agent_id\",\"operator_id\":\"alex\"}" >/dev/null
echo "  ✓ added participant"

bar "4. POST /turns with auto_respond=true (the test that 422'd before)"
echo "  Sending..."
turn_resp=$(curl -s -w "\n__HTTP__%{http_code}" -X POST \
  "http://127.0.0.1:7423/conversations/$test_cid/turns" \
  -H "Content-Type: application/json" $(auth) \
  -d "{\"speaker\":\"alex\",\"body\":\"Hi, please give me a one-line status check.\",\"auto_respond\":true,\"addressed_to\":[\"$agent_id\"]}")
turn_code=$(echo "$turn_resp" | grep -oE '__HTTP__[0-9]+' | tr -d '_HTP')
turn_body=$(echo "$turn_resp" | sed 's/__HTTP__[0-9]*//')
echo "  HTTP code: $turn_code"
echo "  body (first 800 chars):"
echo "$turn_body" | head -c 800
echo ""
if [[ "$turn_code" == "201" ]]; then
  echo ""
  echo "  ✅ B143 FIX VERIFIED — turn POST returned 201 Created (was 422)"
elif [[ "$turn_code" == "422" ]]; then
  echo ""
  echo "  ❌ B143 FIX FAILED — still 422. Check daemon err log:"
  tail -40 /tmp/forest-daemon.err.log | sed 's/^/    /'
else
  echo ""
  echo "  ⚠️  unexpected HTTP code $turn_code — check daemon logs"
fi

bar "5. cleanup test conversation (archive)"
curl -s -X POST "http://127.0.0.1:7423/conversations/$test_cid/status" \
  -H "Content-Type: application/json" $(auth) \
  -d '{"status":"archived","reason":"b143 verify cleanup"}' >/dev/null
echo "  archived $test_cid"

bar "6. dump tail of err log to workspace for follow-up"
tail -100 /tmp/forest-daemon.err.log > _diagnostic_b143_err.txt 2>&1
echo "  wrote $(wc -l < _diagnostic_b143_err.txt | tr -d ' ') lines to _diagnostic_b143_err.txt"

echo ""
echo "Done. Press return to close."
read -r _
