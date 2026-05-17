#!/usr/bin/env bash
# live-test-d3-phase-a.command — autonomous smoke test for
# ADR-0078 Phase A (forensic_archivist + archive_evidence.v1).
#
# Run this AFTER:
#   1. Commits B343-B347 have landed (the role / skill / runbook).
#   2. force-restart-daemon.command picked up the new role.
#   3. birth-d3-phase-a.command (or birth-forensic-archivist.command)
#      put ForensicArchivist-D3 in the registry.
#
# This script then:
#   1. Preflight checks the daemon + the seven kit tools are registered.
#   2. Finds ForensicArchivist-D3 (or aborts with a clear "birth first" message).
#   3. Installs archive_evidence.v1 into data/forge/skills/installed/
#      + POSTs /skills/reload so the catalog picks it up. Idempotent.
#   4. Creates a small test artifact under data/forensics/TEST-001/.
#   5. Dispatches archive_evidence.v1 against the agent with
#      transition_type=acquire.
#   6. Verifies the response shape: status=succeeded + verdict_block
#      contains "VERDICT: ATTEST".
#   7. Reads back the agent's memory tagged with the artifact_id to
#      confirm the attestation entry landed in private memory.
#   8. Tails the audit chain for the dispatch events.
#
# Logs to data/test-runs/d3-phase-a-001/.
#
# Bug ledger from prior live-test scripts (encoded in fix shape, not
# in the code body):
#   - curl -sf swallows error response bodies. We drop -f and surface.
#   - python3 - <<HEREDOC replaces stdin; we use python3 -c with single-
#     quoted scripts so stdin stays free for any pipes.
#   - /agents/{id}/skills/run requires session_id (unique per call).
#   - skill not installed at data/forge/skills/installed/ → 404 with a
#     clear detail message; we install before dispatch.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

# ---- config ---------------------------------------------------------------
DAEMON="${FSF_DAEMON_URL:-http://127.0.0.1:7423}"
ENV_FILE="$HERE/.env"
TOKEN="${FSF_API_TOKEN:-$(grep -E '^FSF_API_TOKEN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2)}"
RUN_ID="d3-phase-a-001"
TARGET_DIR="$HERE/data/test-runs/$RUN_ID"
ARTIFACT_ROOT="$HERE/data/forensics/TEST-001"
ARTIFACT_PATH="$ARTIFACT_ROOT/smoke.txt"
ARTIFACT_ID="TEST-001_smoke"
RUN_LOG="$TARGET_DIR/run.log"
AUDIT_TAIL="$TARGET_DIR/audit-tail.log"
DISPATCH_RESP="$TARGET_DIR/dispatch-response.json"
MEMORY_DUMP="$TARGET_DIR/memory-dump.json"
SESSION_ID="$RUN_ID-$(date +%s)"
SKILL_SRC="$HERE/examples/skills/archive_evidence.v1.yaml"
SKILL_DST="$HERE/data/forge/skills/installed/archive_evidence.v1.yaml"

mkdir -p "$TARGET_DIR" "$ARTIFACT_ROOT" "$HERE/data/forge/skills/installed"
: > "$RUN_LOG"
: > "$AUDIT_TAIL"

# ---- helpers --------------------------------------------------------------
auth_header() { [[ -n "$TOKEN" ]] && echo "-H X-FSF-Token: $TOKEN" || echo ""; }
log()     { local msg="$1"; printf "[%s] %s\n" "$(date -u +%H:%M:%S)" "$msg" | tee -a "$RUN_LOG"; }
section() { printf "\n=========== %s ===========\n" "$1" | tee -a "$RUN_LOG"; }
die()     { log "ABORT: $1"; echo ""; echo "Press return to close."; read -r _; exit 1; }

# ---- 0: preflight ---------------------------------------------------------
section "0. preflight"
log "RUN_ID=$RUN_ID"
log "DAEMON=$DAEMON"
log "TARGET_DIR=$TARGET_DIR"
log "ARTIFACT_PATH=$ARTIFACT_PATH"

if [[ -z "$TOKEN" ]]; then
  die "FSF_API_TOKEN not set + not in $ENV_FILE. Cannot auth."
fi

# Drop -f intentionally — error bodies surface.
healthz=$(curl -s --max-time 5 "$DAEMON/healthz" 2>&1)
if ! echo "$healthz" | grep -q '"status"'; then
  die "daemon $DAEMON not reachable. Got: $(echo "$healthz" | head -c 200)"
fi
log "✓ daemon reachable"

# Verify the seven kit tools are registered.
tools_json=$(curl -s --max-time 5 "$DAEMON/tools/registered" $(auth_header) 2>&1)
EXPECTED_TOOLS=(memory_recall memory_write file_integrity audit_chain_verify llm_think delegate code_read)
for t in "${EXPECTED_TOOLS[@]}"; do
  has=$(echo "$tools_json" | python3 -c '
import sys, json
data = json.loads(sys.stdin.read())
tools = data.get("tools", [])
name = sys.argv[1]
hits = [x for x in tools if x.get("name") == name]
print(len(hits))
' "$t" 2>/dev/null || echo 0)
  if [[ "$has" -ge 1 ]]; then
    log "✓ tool $t registered"
  else
    die "tool $t NOT registered (got $(echo "$tools_json" | head -c 200)). Restart daemon."
  fi
done

# ---- 1: locate ForensicArchivist-D3 ---------------------------------------
section "1. locate ForensicArchivist-D3"
agents_json=$(curl -s --max-time 5 "$DAEMON/agents?limit=500" $(auth_header) 2>&1)
ARCHIVIST_ID=$(echo "$agents_json" | python3 -c '
import sys, json
data = json.loads(sys.stdin.read())
agents = data.get("agents", [])
hits = [a for a in agents if a.get("agent_name") == "ForensicArchivist-D3"]
print(hits[0]["instance_id"] if hits else "")
' 2>/dev/null)

if [[ -z "$ARCHIVIST_ID" ]]; then
  die "ForensicArchivist-D3 not found in /agents. Run birth-d3-phase-a.command first."
fi
log "✓ ForensicArchivist-D3 found: instance_id=$ARCHIVIST_ID"

# Read the agent's constitution_path so we can sanity-check the kit.
agent_detail=$(curl -s --max-time 5 "$DAEMON/agents/$ARCHIVIST_ID" $(auth_header) 2>&1)
CONST_PATH=$(echo "$agent_detail" | python3 -c '
import sys, json
print(json.loads(sys.stdin.read()).get("constitution_path", ""))
' 2>/dev/null)
log "  constitution: $CONST_PATH"

# ---- 2: install archive_evidence.v1 skill --------------------------------
section "2. install archive_evidence.v1"
if [[ -f "$SKILL_DST" ]]; then
  log "✓ skill already at $SKILL_DST (re-copying to refresh)"
else
  log "  copying $SKILL_SRC → $SKILL_DST"
fi
cp "$SKILL_SRC" "$SKILL_DST" || die "cp failed"

reload_resp=$(curl -s --max-time 10 -X POST "$DAEMON/skills/reload" $(auth_header) 2>&1)
log "  reload response: $(echo "$reload_resp" | head -c 200)"

# Verify the skill is now in the catalog.
skills_json=$(curl -s --max-time 5 "$DAEMON/skills" $(auth_header) 2>&1)
SKILL_PRESENT=$(echo "$skills_json" | python3 -c '
import sys, json
data = json.loads(sys.stdin.read())
skills = data.get("skills", [])
hits = [s for s in skills if s.get("name") == "archive_evidence"]
print("yes" if hits else "no")
' 2>/dev/null || echo "no")

if [[ "$SKILL_PRESENT" != "yes" ]]; then
  log "WARN: /skills doesnt list archive_evidence. Continuing — the runtime"
  log "      loads the manifest directly from disk so it may still dispatch."
else
  log "✓ archive_evidence.v1 present in /skills catalog"
fi

# ---- 3: create test artifact ----------------------------------------------
section "3. create test artifact"
cat > "$ARTIFACT_PATH" <<EOF
ADR-0078 Phase A live smoke test artifact.
Created: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
Run: $RUN_ID
Purpose: exercise archive_evidence.v1 against a real path with a real hash.
EOF
ARTIFACT_HASH=$(shasum -a 256 "$ARTIFACT_PATH" | cut -d' ' -f1)
log "✓ wrote $ARTIFACT_PATH"
log "  size: $(wc -c < "$ARTIFACT_PATH") bytes"
log "  sha256 (local): $ARTIFACT_HASH"

# ---- 4: dispatch archive_evidence.v1 -------------------------------------
section "4. dispatch archive_evidence.v1 (transition=acquire)"
# Use REPO-RELATIVE artifact_path so the per-tool allowed_paths constraint
# (data/forensics/) matches. The daemon resolves paths against its CWD.
REL_ARTIFACT_PATH="data/forensics/TEST-001/smoke.txt"
payload=$(python3 -c '
import json, sys
print(json.dumps({
    "skill_name": "archive_evidence",
    "skill_version": "1",
    "session_id": sys.argv[1],
    "inputs": {
        "artifact_id": sys.argv[2],
        "artifact_path": sys.argv[3],
        "transition_type": "acquire",
        "attestor_reason": "smoke test: first live archive_evidence dispatch (B347 live-test driver)",
    },
}))
' "$SESSION_ID" "$ARTIFACT_ID" "$REL_ARTIFACT_PATH")
log "  payload: $payload"
log "  POST $DAEMON/agents/$ARCHIVIST_ID/skills/run"

# Drop -f so the body surfaces on 4xx/5xx.
dispatch_resp=$(curl -s --max-time 120 -X POST \
  "$DAEMON/agents/$ARCHIVIST_ID/skills/run" \
  -H "Content-Type: application/json" $(auth_header) \
  -d "$payload" 2>&1)
echo "$dispatch_resp" > "$DISPATCH_RESP"
log "  response saved → $DISPATCH_RESP"

# Pretty-print + extract key fields.
status=$(echo "$dispatch_resp" | python3 -c '
import sys, json
try:
    print(json.loads(sys.stdin.read()).get("status", "<no_status>"))
except Exception as e:
    print(f"<parse_error: {e}>")
' 2>/dev/null)
log "  dispatch status: $status"

if [[ "$status" != "succeeded" ]]; then
  log "  FULL RESPONSE BODY:"
  echo "$dispatch_resp" | python3 -m json.tool 2>/dev/null | tee -a "$RUN_LOG" || echo "$dispatch_resp" | tee -a "$RUN_LOG"
  die "skill dispatch returned status=$status (expected 'succeeded')"
fi
log "✓ dispatch succeeded"

# Extract verdict block from output.
verdict=$(echo "$dispatch_resp" | python3 -c '
import sys, json
data = json.loads(sys.stdin.read())
out = data.get("output", {})
print(out.get("verdict_block", "<missing_verdict_block>"))
' 2>/dev/null)
log "  verdict_block:"
echo "$verdict" | sed 's/^/      /' | tee -a "$RUN_LOG"

if ! echo "$verdict" | grep -q "VERDICT: ATTEST"; then
  log "  WARN: verdict is NOT ATTEST. Skill ran cleanly but emitted HALT."
  log "        For a first-acquire on a fresh artifact_id this should be ATTEST."
  log "        Inspect the verdict_block above for the HALT_CODE."
else
  log "✓ verdict = ATTEST"
fi

# ---- 5: verify attestation in memory --------------------------------------
section "5. verify attestation in private memory"
memory_resp=$(curl -s --max-time 5 \
  "$DAEMON/agents/$ARCHIVIST_ID/memory?limit=20" \
  $(auth_header) 2>&1)
echo "$memory_resp" > "$MEMORY_DUMP"

custody_count=$(echo "$memory_resp" | python3 -c '
import sys, json
try:
    data = json.loads(sys.stdin.read())
    entries = data.get("entries", [])
    hits = [
        e for e in entries
        if (sys.argv[1] in (e.get("tags") or []))
        or sys.argv[1] in (e.get("content") or "")
    ]
    print(len(hits))
except Exception as e:
    print(f"0  # parse_error: {e}")
' "$ARTIFACT_ID" 2>/dev/null || echo "0")

log "  memory entries tagged with $ARTIFACT_ID: $custody_count"
if [[ "$custody_count" -ge 1 ]]; then
  log "✓ chain-of-custody entry recorded in private memory"
else
  log "  WARN: no memory entry found for $ARTIFACT_ID."
  log "        Either memory_write didnt fire, or the /agents/<id>/memory"
  log "        endpoint filters scope=private differently than expected."
  log "        Memory dump saved → $MEMORY_DUMP"
fi

# ---- 6: tail audit chain for dispatch events -----------------------------
section "6. audit chain tail (events from this dispatch)"
CHAIN_PATH="$HERE/examples/audit_chain.jsonl"
if [[ -f "$CHAIN_PATH" ]]; then
  grep "$SESSION_ID" "$CHAIN_PATH" 2>/dev/null | tail -20 > "$AUDIT_TAIL"
  evcount=$(wc -l < "$AUDIT_TAIL" | tr -d ' ')
  log "  audit chain events for session $SESSION_ID: $evcount"
  if [[ "$evcount" -ge 1 ]]; then
    log "  (full tail → $AUDIT_TAIL)"
    log "  event types:"
    python3 -c '
import json, sys
for line in open(sys.argv[1]):
    try:
        e = json.loads(line)
        print(f"      {e.get(\"event_type\", \"?\")}\t{e.get(\"timestamp\", \"\")}")
    except Exception:
        pass
' "$AUDIT_TAIL" | tee -a "$RUN_LOG"
  fi
else
  log "  WARN: $CHAIN_PATH not found. Check daemon FSF_AUDIT_CHAIN_PATH."
fi

# ---- 7: summary ----------------------------------------------------------
section "7. summary"
log "ARCHIVIST_ID:     $ARCHIVIST_ID"
log "SESSION_ID:       $SESSION_ID"
log "ARTIFACT_PATH:    $ARTIFACT_PATH"
log "ARTIFACT_HASH:    $ARTIFACT_HASH"
log "DISPATCH_STATUS:  $status"
log "VERDICT:          $(echo "$verdict" | grep '^VERDICT:' || echo '<no verdict line>')"
log "MEMORY_HITS:      $custody_count"
log ""
log "Full run log:     $RUN_LOG"
log "Dispatch body:    $DISPATCH_RESP"
log "Memory dump:      $MEMORY_DUMP"
log "Audit tail:       $AUDIT_TAIL"
log ""
log "If everything above is green, ADR-0078 Phase A is LIVE end-to-end."
log "Next: ADR-0064 (telemetry pipeline) substrate work."
echo ""
echo "Press return to close."
read -r _
