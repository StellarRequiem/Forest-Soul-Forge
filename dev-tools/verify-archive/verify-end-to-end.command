#!/usr/bin/env bash
# Forest Soul Forge — end-to-end verification.
#
# What this does:
#   1. Probes the running daemon and reports current state
#   2. If swarm is missing → runs full swarm-bringup (births 9 agents +
#      installs 21 skills + runs synthetic-incident smoke)
#   3. If swarm is present → just runs the synthetic-incident smoke
#      (LogLurker → AnomalyAce → ResponseRogue → VaultWarden chain)
#   4. Renders a chronicle of the resulting events into HTML
#   5. Prints the chronicle path so you can open it in a browser
#
# This is the "show me everything is wired up" smoke. Single double-
# click; no flags; safe to re-run as many times as you like.
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

# ---- Step 1: daemon health + state probe --------------------------------
bar "1. Daemon state probe"
curl -sf "$DAEMON/healthz" > /dev/null || die "daemon not reachable at $DAEMON — start via run.command"
ok "daemon reachable"

# Count agents by role to decide whether the swarm is already born
agents_json=$(curl -sf "$DAEMON/agents")
total_agents=$(echo "$agents_json" | jq '.agents | length')
ok "registry has $total_agents total agents"

# The 9 swarm role names
SWARM_ROLES=(patch_patrol gatekeeper log_lurker anomaly_ace net_ninja response_rogue zero_zero vault_warden deception_duke)
swarm_present=0
for r in "${SWARM_ROLES[@]}"; do
  count=$(echo "$agents_json" | jq --arg r "$r" '.agents | map(select(.role == $r and .status == "active")) | length')
  if [[ "$count" -gt 0 ]]; then
    swarm_present=$((swarm_present + 1))
  fi
done
ok "swarm roles active: $swarm_present / 9"

# ---- Step 2: bring up the swarm if needed -------------------------------
if [[ "$swarm_present" -lt 9 ]]; then
  bar "2. Swarm incomplete — running full bringup (birth + install + smoke)"
  echo "  This will take ~30-60s the first time."
  echo ""
  if [[ ! -x "./swarm-bringup.command" ]]; then
    die "swarm-bringup.command not found or not executable"
  fi
  # Run inline (don't fork a new Terminal window); capture exit
  bash ./swarm-bringup.command || die "swarm-bringup failed — see output above"
else
  bar "2. Swarm already present — running synthetic-incident smoke"
  if [[ ! -x "./scripts/security-smoke.sh" ]]; then
    die "scripts/security-smoke.sh not found or not executable"
  fi
  ./scripts/security-smoke.sh || die "smoke test failed — see output above"
fi

# ---- Step 3: snapshot the audit chain after the run ---------------------
bar "3. Audit chain — recent events"
audit_tail=$(curl -sf "$DAEMON/audit/tail?n=80")
total=$(echo "$audit_tail" | jq '.count')
ok "chain has $total events in the last 80-event tail"

# Count event types we care about
print_count() {
  local label="$1" type="$2"
  local n
  n=$(echo "$audit_tail" | jq --arg t "$type" '.events | map(select(.event_type == $t)) | length')
  printf "    %-30s %d\n" "$label" "$n"
}
echo ""
echo "  Event-type breakdown (last 80):"
print_count "agent_created"          "agent_created"
print_count "skill_invoked"          "skill_invoked"
print_count "skill_step_started"     "skill_step_started"
print_count "skill_step_completed"   "skill_step_completed"
print_count "skill_completed"        "skill_completed"
print_count "tool_call_dispatched"   "tool_call_dispatched"
print_count "tool_call_succeeded"    "tool_call_succeeded"
print_count "agent_delegated"        "agent_delegated"
print_count "ceremony"               "ceremony"
print_count "out_of_triune_attempt"  "out_of_triune_attempt"
print_count "hardware_bound"         "hardware_bound"
print_count "hardware_mismatch"      "hardware_mismatch"
print_count "hardware_unbound"       "hardware_unbound"

# ---- Step 4: render a chronicle of the recent activity ------------------
bar "4. Render chronicle of recent activity"
out_dir="$HERE/data/chronicles"
mkdir -p "$out_dir"
TS="$(date +%s)"
chron_html="$out_dir/end-to-end__$TS.html"

PYTHONPATH="src" python3 - "$chron_html" <<'PYEOF'
import sys
from pathlib import Path
from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.chronicle import render_html

candidates = [Path("examples/audit_chain.jsonl"), Path("data/audit_chain.jsonl")]
chain_path = next((p for p in candidates if p.exists()), None)
if chain_path is None:
    print("FAIL: no audit chain JSONL", file=sys.stderr); sys.exit(1)

chain = AuditChain(chain_path)
entries = chain.read_all()

# Just the last 100 events keeps the chronicle focused on the verify run
recent = entries[-100:]
html = render_html(
    recent,
    title="Forest Soul Forge — end-to-end verification",
    subtitle=f"{len(recent)} most-recent events from {chain_path.name} (full chain: {len(entries)} entries)",
)
out = Path(sys.argv[1])
out.write_text(html)
print(f"OK: rendered {len(recent)} events → {out} ({out.stat().st_size} bytes)")
PYEOF

if [[ ! -s "$chron_html" ]]; then
  die "chronicle file not written"
fi
size_kb=$(wc -c < "$chron_html" | awk '{printf "%.1f", $1/1024}')
ok "chronicle written ($size_kb KB)"

# ---- Step 5: open the chronicle in the default browser ------------------
bar "5. Open chronicle in browser"
open "$chron_html" 2>/dev/null && ok "opened $chron_html" || \
  no "could not open with 'open'; copy the path and open manually"
echo ""
echo "  Chronicle path:"
echo "    $chron_html"

# ---- Done ----------------------------------------------------------------
bar "END-TO-END VERIFICATION DONE"
echo ""
echo "Summary:"
echo "  - Daemon reachable at $DAEMON"
echo "  - Swarm: $swarm_present / 9 roles active before this run"
echo "  - Audit chain: $total events in the last 80-event window"
echo "  - Chronicle: $chron_html"
echo ""
echo "Next: open the chronicle in the browser. The vertical timeline"
echo "shows every event from the swarm chain (orange dots = milestones,"
echo "red = warnings, blue = routine). The 'show milestones only'"
echo "checkbox at the top filters to ceremonies + births + delegations."
echo ""
echo "Press return to close."
read -r _
