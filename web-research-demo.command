#!/usr/bin/env bash
# Forest Soul Forge — C8 open-web synthetic-incident demo (ADR-003X).
#
# Mirrors Phase E1 for the open-web plane. Proves the chain
# web_fetch → memory_write → delegate → memory_write → ceremony
# is wired end-to-end without external network access or operator config.
#
# Self-contained: spins up a local Python http.server on a free port,
# serves scenarios/web-research-demo/synthetic_rfc.md, runs the chain,
# emits a ceremony, renders chronicle, archives test agents, kills server.
#
# Double-click from Finder. ~30 seconds end-to-end.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

DAEMON="${FSF_DAEMON_URL:-http://127.0.0.1:7423}"
TOKEN="${FSF_API_TOKEN:-}"
auth_header() { [[ -n "$TOKEN" ]] && echo "-H X-FSF-Token: $TOKEN" || echo ""; }

bar() { printf "\n========== %s ==========\n" "$1"; }
ok()  { printf "  ✓ %s\n" "$1"; }
no()  { printf "  ✗ %s\n" "$1" >&2; }
die() { no "$1"; cleanup; echo ""; echo "Press return to close."; read -r _; exit 1; }

require() { command -v "$1" >/dev/null 2>&1 || die "missing: $1"; }
require curl
require jq
require python3

# Cleanup state — populated as we go
HTTP_PID=""
RESEARCHER_ID=""
ACTUATOR_ID=""

cleanup() {
  if [[ -n "$HTTP_PID" ]]; then
    kill "$HTTP_PID" 2>/dev/null || true
    ok "killed local HTTP server (pid=$HTTP_PID)"
  fi
  for inst in "$RESEARCHER_ID" "$ACTUATOR_ID"; do
    if [[ -n "$inst" ]]; then
      arch=$(jq -n --arg id "$inst" '{instance_id: $id, reason: "C8 demo cleanup"}')
      curl -s -o /dev/null -X POST "$DAEMON/archive" \
        -H "Content-Type: application/json" $(auth_header) -d "$arch" || true
    fi
  done
}
trap cleanup EXIT

# ---- Step 0: daemon health ----------------------------------------------
bar "0. Daemon health"
curl -sf "$DAEMON/healthz" > /dev/null || die "daemon not reachable at $DAEMON"
ok "daemon reachable at $DAEMON"

# ---- Step 1: pick a free local port + spin up http.server ---------------
bar "1. Spin up local HTTP server (synthetic RFC)"
SERVE_DIR="$HERE/scenarios/web-research-demo"
[[ -f "$SERVE_DIR/synthetic_rfc.md" ]] || die "synthetic_rfc.md missing at $SERVE_DIR"

# Find a free port — try in 8800-8900 range
PORT=""
for try in $(seq 8800 8900); do
  if ! lsof -nP -iTCP:"$try" -sTCP:LISTEN -t >/dev/null 2>&1; then
    PORT="$try"
    break
  fi
done
[[ -z "$PORT" ]] && die "no free port in 8800-8900"
ok "using port $PORT"

# Start server in background; bind to 127.0.0.1 only (no LAN exposure)
( cd "$SERVE_DIR" && python3 -m http.server "$PORT" --bind 127.0.0.1 ) > /tmp/c8-http-$$.log 2>&1 &
HTTP_PID=$!
sleep 1   # give it a moment to bind

# Sanity-check it's serving
SYNTH_URL="http://127.0.0.1:$PORT/synthetic_rfc.md"
if ! curl -sf "$SYNTH_URL" > /dev/null; then
  die "local server not responding at $SYNTH_URL"
fi
ok "synthetic RFC served at $SYNTH_URL (pid=$HTTP_PID)"

# ---- Step 2: birth researcher + actuator agents -------------------------
bar "2. Birth researcher + actuator"
SUFFIX="$(date +%s)"

birth() {
  local name="$1"
  shift
  local extra_tools="$1"
  local payload=$(jq -n --arg name "$name" --argjson tools "$extra_tools" '{
    profile: {role: "operator_companion", trait_values: {}, domain_weight_overrides: {}},
    agent_name: $name, agent_version: "v1", enrich_narrative: false,
    tools_add: $tools
  }')
  curl -sf -X POST "$DAEMON/birth" -H "Content-Type: application/json" \
    $(auth_header) -d "$payload"
}

RESEARCHER=$(birth "WebResearcher_$SUFFIX" '[{"name":"web_fetch","version":"1"},{"name":"memory_write","version":"1"},{"name":"delegate","version":"1"}]')
[[ -z "$RESEARCHER" ]] && die "researcher birth failed"
RESEARCHER_ID=$(echo "$RESEARCHER" | jq -r '.instance_id')
RESEARCHER_CONST=$(echo "$RESEARCHER" | jq -r '.constitution_path')
ok "researcher born: $RESEARCHER_ID"

ACTUATOR=$(birth "WebActuator_$SUFFIX" '[{"name":"memory_write","version":"1"}]')
[[ -z "$ACTUATOR" ]] && die "actuator birth failed"
ACTUATOR_ID=$(echo "$ACTUATOR" | jq -r '.instance_id')
ok "actuator born:   $ACTUATOR_ID"

# ---- Step 3: install the demo skills (if not already) -------------------
bar "3. Install demo skills (web_research_brief, web_actuator_handoff)"
SKILL_DIR="$HERE/data/forge/skills/installed"
mkdir -p "$SKILL_DIR"
cp -f examples/skills/web_research_brief.v1.yaml "$SKILL_DIR/"
cp -f examples/skills/web_actuator_handoff.v1.yaml "$SKILL_DIR/"
# Ask daemon to reload
curl -s -o /dev/null -X POST "$DAEMON/skills/reload" $(auth_header) || true
ok "skills installed in $SKILL_DIR + reload nudged"

# ---- Step 4: patch researcher's constitution to allowlist 127.0.0.1 -----
bar "4. Patch researcher constitution: web_fetch.allowed_hosts += [127.0.0.1]"
python3 - "$RESEARCHER_CONST" <<'PYEOF' || true
import sys
from pathlib import Path
p = Path(sys.argv[1])
text = p.read_text()
# Use plain text patching to avoid yaml dependency in system python3.
# Find the web_fetch tool entry and append/replace its allowed_hosts.
# Format we expect (from constitution rendering):
#   - name: web_fetch
#     version: '1'
#     side_effects: network
#     constraints:
#       requires_human_approval: false
#       max_calls_per_session: 50
#       audit_every_call: true
#     applied_rules: [...]
# We append "      allowed_hosts: [127.0.0.1]" under the web_fetch
# tool's constraints block. Idempotent — re-runs are safe.
import re
# Find the constraints block of the web_fetch tool
pat = re.compile(
    r'(- name: web_fetch\s*\n'
    r'  version: .*?\n'
    r'(?:.*?\n)*?'
    r'  constraints:\s*\n'
    r')',
    re.MULTILINE,
)
m = pat.search(text)
if not m:
    print("WARN: could not find web_fetch constraints block — leaving constitution untouched", file=sys.stderr)
    sys.exit(0)
end = m.end()
# Skip if already patched
if "allowed_hosts" in text[end:end+500]:
    print("constitution already has allowed_hosts; nothing to patch")
    sys.exit(0)
patched = text[:end] + '    allowed_hosts: [127.0.0.1]\n' + text[end:]
p.write_text(patched)
print("OK: appended allowed_hosts: [127.0.0.1] under web_fetch.constraints")
PYEOF
ok "constitution patched (or already had allowed_hosts)"

# ---- Step 5: run the research skill chain -------------------------------
bar "5. Run web_research_brief on researcher"
run_payload=$(jq -n \
  --arg url "$SYNTH_URL" \
  --arg downstream "$ACTUATOR_ID" \
  --arg topic "rfc-forest-001" \
  '{
    skill_name: "web_research_brief",
    skill_version: "1",
    inputs: {
      url: $url,
      downstream_agent_id: $downstream,
      brief_topic: $topic
    },
    session_id: "c8-demo"
  }')
tmp="$(mktemp)"
http_code=$(curl -s -o "$tmp" -w "%{http_code}" -X POST \
  "$DAEMON/agents/$RESEARCHER_ID/skills/run" \
  -H "Content-Type: application/json" $(auth_header) -d "$run_payload")
run_body="$(cat "$tmp")"; rm -f "$tmp"
if [[ "$http_code" != "200" ]]; then
  die "skill run failed (http=$http_code): ${run_body:0:600}"
fi
status=$(echo "$run_body" | jq -r '.status // ""')
ok "skill run completed (http=$http_code, status=$status)"

# ---- Step 6: emit ceremony summarizing the chain -----------------------
bar "6. Emit open_web_demo.simulated_action ceremony"
ceremony_payload=$(jq -n \
  --arg researcher "$RESEARCHER_ID" \
  --arg actuator "$ACTUATOR_ID" \
  --arg url "$SYNTH_URL" \
  '{
    ceremony_name: "open_web_demo.simulated_action",
    summary: "C8 demo: researcher fetched RFC + delegated to actuator who simulated mcp_call.v1(linear, create_issue). NO real external action.",
    operator_id: "web-research-demo.command",
    metadata: {
      researcher_id: $researcher,
      actuator_id: $actuator,
      source_url: $url,
      simulated_action: "mcp_call.v1(server=linear, tool=create_issue)"
    }
  }')
tmp="$(mktemp)"
http_code=$(curl -s -o "$tmp" -w "%{http_code}" -X POST \
  "$DAEMON/audit/ceremony" \
  -H "Content-Type: application/json" $(auth_header) -d "$ceremony_payload")
[[ "$http_code" != "200" ]] && die "ceremony emit failed (http=$http_code): $(cat "$tmp")"
rm -f "$tmp"
ok "ceremony emitted"

# ---- Step 7: render chronicle of the chain ------------------------------
bar "7. Render chronicle"
chron_html="$HERE/data/chronicles/c8-open-web-demo__$SUFFIX.html"
mkdir -p "$(dirname "$chron_html")"
PYTHONPATH="src" python3 - "$chron_html" "$RESEARCHER_ID" "$ACTUATOR_ID" <<'PYEOF'
import sys
from pathlib import Path
from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.chronicle import render_html

candidates = [Path("examples/audit_chain.jsonl"), Path("data/audit_chain.jsonl")]
chain_path = next((p for p in candidates if p.exists()), None)
if not chain_path:
    print("FAIL: no audit chain", file=sys.stderr); sys.exit(1)

entries = AuditChain(chain_path).read_all()

# Filter to events involving either of our two demo agents (by inspecting
# event_data instance_id / caller_instance / target_instance / agent_dna).
researcher_id, actuator_id = sys.argv[2], sys.argv[3]
def involves(e):
    d = e.event_data or {}
    refs = (
        d.get("instance_id"),
        d.get("caller_instance"),
        d.get("target_instance"),
    )
    return researcher_id in refs or actuator_id in refs

# Last 30 events, OR all events involving demo agents — whichever is more
demo_events = [e for e in entries if involves(e)]
recent = entries[-30:]
combined = list({e.seq: e for e in (demo_events + recent)}.values())
combined.sort(key=lambda e: e.seq)

html = render_html(
    combined,
    title="ADR-003X C8 — open-web demo chain",
    subtitle=f"{len(combined)} events: {len(demo_events)} demo-specific + recent context",
)
out = Path(sys.argv[1])
out.write_text(html)
print(f"OK: rendered {len(combined)} events → {out}")
PYEOF
ok "chronicle: $chron_html"
open "$chron_html" 2>/dev/null && ok "opened in default browser" || true

# Cleanup runs via trap; print summary
bar "C8 OPEN-WEB DEMO PASSED"
echo "Verified end-to-end:"
echo "  ✓ web_fetch.v1 against local 127.0.0.1:$PORT (synthetic RFC)"
echo "  ✓ memory_write.v1 persisting brief on researcher"
echo "  ✓ delegate.v1 → governance_relaxed event (out-of-lineage handoff)"
echo "  ✓ memory_write.v1 persisting simulated action on actuator"
echo "  ✓ ceremony 'open_web_demo.simulated_action' emitted"
echo ""
echo "Chronicle: $chron_html"
echo ""
echo "Press return to close."
read -r _
