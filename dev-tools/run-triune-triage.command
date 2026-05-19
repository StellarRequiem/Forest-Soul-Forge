#!/bin/bash
# Triune-Main scheduled triage driver.
#
# Daily 7am cadence (via launchd plist). Fires wiring_audit_triage.v1
# against Engineer-Main, passing:
#   - the most-recent wiring_audit_outcome content from WiringSentinel's
#     lineage memory (via GET /agents/<sentinel>/memory)
#   - Reviewer-Main + Architect-Main instance_ids
#
# The skill delegates extract→rank→synthesize across the triune and
# records the outcome in Engineer-Main's lineage memory.
#
# Soft-fail: any step returning non-200 or status=failed exits non-zero;
# the launchd plist treats non-zero as a soft fail and retries next tick.

set -uo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
ENV_FILE="$REPO_ROOT/.env"
TOKEN=""
[ -f "$ENV_FILE" ] && TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "Triune-Main wiring triage"
echo "  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "=========================================================="

# Self-heal install (gitignored installed/ dir)
SKILL_SRC="$REPO_ROOT/examples/skills/wiring_audit_triage.v1.yaml"
SKILL_DST="$REPO_ROOT/data/forge/skills/installed/wiring_audit_triage.v1.yaml"
if [ ! -f "$SKILL_DST" ] && [ -f "$SKILL_SRC" ]; then
  echo "[0/4] Installing wiring_audit_triage.v1"
  mkdir -p "$(dirname "$SKILL_DST")"
  cp "$SKILL_SRC" "$SKILL_DST"
fi

resolve_id() {
  local name="$1"
  curl -s --max-time 5 "${DAEMON}/agents?limit=300" \
    -H "X-FSF-Token: $TOKEN" 2>/dev/null \
    | python3 -c "import sys,json; data=json.load(sys.stdin); ids=[a.get('instance_id') for a in data.get('agents',[]) if a.get('agent_name')=='$name' and a.get('status')=='active']; print(ids[0] if ids else '')" 2>/dev/null
}

echo
echo "[1/4] Resolving Triune-Main + WiringSentinel instance_ids"
ENG_ID=$(resolve_id "Engineer-Main")
REV_ID=$(resolve_id "Reviewer-Main")
ARC_ID=$(resolve_id "Architect-Main")
SEN_ID=$(resolve_id "WiringSentinel")
for var in ENG_ID REV_ID ARC_ID SEN_ID; do
  val="${!var}"
  if [ -z "$val" ]; then
    echo "ERROR: $var not resolved. Run dev-tools/birth-triune-main.command (and birth-wiring-sentinel.command for WiringSentinel)."
    exit 2
  fi
  echo "      $var = $val"
done

echo
echo "[2/4] Fetching most-recent wiring_audit_outcome from registry"
# No /agents/{id}/memory GET endpoint — memory is accessed via the
# agent's own tools (memory_recall.v1). For the scheduled wrapper
# we read the registry DB directly. Read-only; fine.
OUTCOME=$(python3 <<PYEOF
import sqlite3, sys
con = sqlite3.connect("$REPO_ROOT/data/registry.sqlite")
cur = con.cursor()
# WiringSentinel writes scope=lineage tagged wiring_audit. Pick most recent.
row = cur.execute("""
  select content from memory_entries
  where instance_id = ? and tags_json like '%wiring_audit%'
  order by created_at desc limit 1
""", ("$SEN_ID",)).fetchone()
if row:
    sys.stdout.write(row[0])
PYEOF
)
if [ -z "$OUTCOME" ]; then
  echo "ERROR: no wiring_audit_outcome entries found in registry for $SEN_ID."
  echo "       Run dev-tools/run-wiring-audit.command first to produce an outcome."
  exit 3
fi
echo "      outcome length: ${#OUTCOME} chars"

echo
echo "[3/4] Dispatching wiring_audit_triage.v1 on Engineer-Main"
PAYLOAD=$(python3 <<PYEOF
import json, uuid, os
payload = {
    "skill_name": "wiring_audit_triage",
    "skill_version": "1",
    "session_id": f"triune-triage-{uuid.uuid4()}",
    "inputs": {
        "recent_outcome": """$OUTCOME""",
        "reviewer_instance_id": "$REV_ID",
        "architect_instance_id": "$ARC_ID",
        "triggered_by": os.environ.get("FSF_TRIAGE_TRIGGER", "scheduled"),
    },
}
print(json.dumps(payload))
PYEOF
)

RESP=$(curl -s --max-time 180 "${DAEMON}/agents/${ENG_ID}/skills/run" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" 2>&1)
echo "      Skill response (truncated):"
echo "$RESP" | python3 -m json.tool 2>/dev/null | head -60 || echo "$RESP" | head -60

STATUS=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "unknown")
if [ "$STATUS" != "succeeded" ]; then
  echo "ERROR: wiring_audit_triage.v1 returned status=$STATUS"
  exit 4
fi

echo
echo "[4/4] Triage complete — outcome logged to Engineer-Main lineage memory"
echo
echo "=========================================================="
echo "Triune-Main triage done"
echo "=========================================================="
exit 0
