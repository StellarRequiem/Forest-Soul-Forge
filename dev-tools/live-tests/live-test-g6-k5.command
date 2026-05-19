#!/usr/bin/env bash
# Forest Soul Forge — G6 (suggest_agent) + K5 (chronicle) live smoke test.
#
# What this verifies against a running daemon:
#
#  G6 (suggest_agent.v1):
#    1. /tools/catalog includes suggest_agent.v1
#    2. /genres includes web_observer / web_researcher / web_actuator
#    3. POST /agents/{id}/tools/call with suggest_agent returns ranked
#       candidates for a natural-language task description
#
#  K5 (fsf chronicle):
#    4. The chronicle module renders HTML from the live audit chain
#    5. The output is a single self-contained file, >0 bytes, valid head/tail
#
# Prereqs:
#   - daemon up at $FSF_DAEMON_URL (default http://127.0.0.1:7423)
#   - daemon RESTARTED since G6 + K5 code landed
#   - jq + curl + python3 on PATH
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

DAEMON="${FSF_DAEMON_URL:-http://127.0.0.1:7423}"
TOKEN="${FSF_API_TOKEN:-}"
auth_header() { [[ -n "$TOKEN" ]] && echo "-H X-FSF-Token: $TOKEN" || echo ""; }

bar() { printf "\n========== %s ==========\n" "$1"; }
ok()  { printf "  ✓ %s\n" "$1"; }
no()  { printf "  ✗ %s\n" "$1" >&2; }
die() { no "$1"; echo ""; echo "Press return to close."; read -r _; exit 1; }

require() { command -v "$1" >/dev/null 2>&1 || die "missing: $1"; }
require curl
require jq
require python3

# ---- Step 0: daemon health ----------------------------------------------
bar "0. Daemon health"
curl -sf "$DAEMON/healthz" > /dev/null || die "daemon not reachable at $DAEMON"
ok "daemon reachable at $DAEMON"

# ---- Step 1: suggest_agent.v1 in catalog --------------------------------
bar "1. /tools/catalog has suggest_agent.v1"
catalog=$(curl -sf "$DAEMON/tools/catalog")
has_sa=$(echo "$catalog" | jq -r '.tools | map(select(.name == "suggest_agent")) | length')
if [[ "$has_sa" != "1" ]]; then
  die "suggest_agent.v1 NOT in /tools/catalog (count=$has_sa).
        Daemon was started before G6 code landed — restart via stop.command + run.command."
fi
ok "suggest_agent.v1 present in catalog"

# ---- Step 2: 3 web genres in /genres -----------------------------------
bar "2. Three web genres present in /genres"
genres=$(curl -sf "$DAEMON/genres")
for g in web_observer web_researcher web_actuator; do
  count=$(echo "$genres" | jq --arg g "$g" '.genres | map(select(.name == $g)) | length')
  if [[ "$count" != "1" ]]; then
    die "genre '$g' missing from /genres (count=$count). Restart needed."
  fi
  ok "genre '$g' present"
done

# ---- Step 3: birth a test agent with suggest_agent.v1 in kit ----------
bar "3. Birth test agent with suggest_agent.v1 in tools_add"
SUFFIX="$(date +%s)"
NAME="SuggestTest_$SUFFIX"
payload=$(jq -n --arg name "$NAME" '{
  profile: {role: "operator_companion", trait_values: {}, domain_weight_overrides: {}},
  agent_name: $name,
  agent_version: "v1",
  enrich_narrative: false,
  tools_add: [{name: "suggest_agent", version: "1"}]
}')
tmp="$(mktemp)"
http_code=$(curl -s -o "$tmp" -w "%{http_code}" -X POST "$DAEMON/birth" \
  -H "Content-Type: application/json" $(auth_header) -d "$payload")
body="$(cat "$tmp")"; rm -f "$tmp"
if [[ "$http_code" != "200" && "$http_code" != "201" ]]; then
  die "birth failed (http=$http_code): ${body:0:300}"
fi
INSTANCE_ID=$(echo "$body" | jq -r '.instance_id')
ok "$NAME born  instance=$INSTANCE_ID"

# ---- Step 4: call suggest_agent.v1 via /tools/call ---------------------
bar "4. POST /agents/{id}/tools/call suggest_agent.v1"
sa_payload=$(jq -n '{
  tool_name: "suggest_agent",
  tool_version: "1",
  args: {
    task: "find me an agent that watches logs for anomalies",
    top_k: 5
  },
  session_id: "live-test-g6"
}')
tmp="$(mktemp)"
http_code=$(curl -s -o "$tmp" -w "%{http_code}" -X POST \
  "$DAEMON/agents/$INSTANCE_ID/tools/call" \
  -H "Content-Type: application/json" $(auth_header) -d "$sa_payload")
sa_body="$(cat "$tmp")"; rm -f "$tmp"
if [[ "$http_code" != "200" ]]; then
  die "suggest_agent call failed (http=$http_code): ${sa_body:0:600}"
fi
status=$(echo "$sa_body" | jq -r '.status // ""')
if [[ "$status" != "succeeded" ]]; then
  die "suggest_agent returned status=$status (expected succeeded). body=${sa_body:0:600}"
fi
candidates_count=$(echo "$sa_body" | jq '.result.output.candidates | length')
matched=$(echo "$sa_body" | jq '.result.output.matched')
scanned=$(echo "$sa_body" | jq '.result.output.scanned')
ok "tool call returned status=$status, candidates=$candidates_count, matched=$matched, scanned=$scanned"
echo ""
echo "  Top candidates:"
echo "$sa_body" | jq -r '.result.output.candidates[] | "    \(.score | tostring | .[0:5])  \(.agent_name)  (\(.role))"'
top_role=$(echo "$sa_body" | jq -r '.result.output.candidates[0].role')
ok "top candidate role: $top_role"

# ---- Step 5: chronicle renderer against the live chain -----------------
bar "5. K5 — render chronicle from live audit chain"
out_dir="$HERE/data/chronicles"
mkdir -p "$out_dir"
chron_html="$out_dir/live-test-g6-k5__$SUFFIX.html"

# Use the project's venv python if available, otherwise system python3
if [[ -x ".venv/bin/python3" ]]; then
  PY=".venv/bin/python3"
elif [[ -x "venv/bin/python3" ]]; then
  PY="venv/bin/python3"
else
  PY="python3"
fi

PYTHONPATH="src" "$PY" - "$chron_html" <<'PYEOF'
import sys
from pathlib import Path
from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.chronicle import render_html

# Find the live chain. Daemon writes to examples/audit_chain.jsonl per
# current settings.
candidates = [Path("examples/audit_chain.jsonl"), Path("data/audit_chain.jsonl")]
chain_path = next((p for p in candidates if p.exists()), None)
if chain_path is None:
    print("FAIL: no audit chain JSONL found", file=sys.stderr)
    sys.exit(1)

chain = AuditChain(chain_path)
entries = chain.read_all()
html = render_html(
    entries,
    title="Forest Soul Forge — live K5 smoke chronicle",
    subtitle=f"{len(entries)} events from {chain_path.name}",
)
out = Path(sys.argv[1])
out.write_text(html)
print(f"OK: rendered {len(entries)} events → {out} ({out.stat().st_size} bytes)")
PYEOF

if [[ ! -s "$chron_html" ]]; then
  die "chronicle file not written or empty: $chron_html"
fi
size_kb=$(wc -c < "$chron_html" | awk '{printf "%.1f", $1/1024}')
ok "chronicle written: $chron_html ($size_kb KB)"

# Sanity-check the file is valid HTML head/tail
head -c 100 "$chron_html" | grep -q '<!doctype html>' || die "chronicle missing <!doctype html> head"
tail -c 50 "$chron_html"  | grep -q '</html>'        || die "chronicle missing </html> tail"
ok "chronicle has valid HTML structure"

# ---- Cleanup -----------------------------------------------------------
bar "6. Cleanup — archive test agent"
arch_payload=$(jq -n --arg id "$INSTANCE_ID" '{instance_id: $id, reason: "live-test-g6-k5 cleanup"}')
http_code=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
  "$DAEMON/archive" -H "Content-Type: application/json" $(auth_header) -d "$arch_payload")
if [[ "$http_code" == "200" || "$http_code" == "201" ]]; then
  ok "archived $INSTANCE_ID"
else
  no "archive returned $http_code (manual cleanup may be needed)"
fi

bar "G6 + K5 LIVE TEST PASSED"
echo "G6 verified end-to-end:"
echo "  - suggest_agent.v1 in catalog + 3 web genres loaded"
echo "  - tool call returns ranked candidates for natural-language task"
echo ""
echo "K5 verified end-to-end:"
echo "  - chronicle renderer reads live audit chain and produces"
echo "    self-contained HTML ($size_kb KB)"
echo ""
echo "Open the chronicle in your browser:"
echo "  $chron_html"
echo ""
echo "Press return to close."
read -r _
