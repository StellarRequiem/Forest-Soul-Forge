#!/bin/bash
# ADR-0081 T5 (B398) — scheduled wiring audit driver.
#
# Two-step pipeline:
#   1. Run section-15-wiring-cross-check (daemon-independent) to
#      regenerate coverage.json from current substrate state.
#   2. Read coverage.json from disk and dispatch wiring_audit.v1
#      against the WiringSentinel agent. The skill verifies the
#      chain, recalls prior audits, summarizes severity, and
#      writes the outcome to lineage memory + audit chain.
#
# Designed to be invoked by launchd on a 4-hour cadence
# (ADR-0081 D7) via ~/Library/LaunchAgents/dev.forest.wiring-audit.plist.
#
# Idempotent. Safe to re-run by hand any time. Exits non-zero if
# section-15 fails OR if no WiringSentinel exists OR if the skill
# dispatch returns non-200. The launchd plist treats non-zero as
# a soft fail (logs to /tmp/forest-wiring-audit.err.log) — the
# next 4-hour tick retries.

set -uo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
ENV_FILE="$REPO_ROOT/.env"
TOKEN=""
if [ -f "$ENV_FILE" ]; then
  TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2 || true)
fi
DAEMON="http://127.0.0.1:7423"
COVERAGE_JSON="$REPO_ROOT/data/test-runs/diagnostic-15-wiring-cross-check/coverage.json"

echo "=========================================================="
echo "ADR-0081 T5 — scheduled wiring audit"
echo "  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "=========================================================="

# Ensure the skill manifest is installed where /skills/run looks for
# it (data/forge/skills/installed/). examples/skills/ is the source-
# of-truth manifest (git-tracked); installed/ is gitignored per-host
# state. Self-heal: if the manifest is missing from installed/, copy
# it from examples/ — happens once per fresh checkout.
SKILL_SRC="$REPO_ROOT/examples/skills/wiring_audit.v1.yaml"
SKILL_DST="$REPO_ROOT/data/forge/skills/installed/wiring_audit.v1.yaml"
if [ ! -f "$SKILL_DST" ] && [ -f "$SKILL_SRC" ]; then
  echo "[0/3] Installing wiring_audit.v1 to data/forge/skills/installed/"
  mkdir -p "$(dirname "$SKILL_DST")"
  cp "$SKILL_SRC" "$SKILL_DST"
fi

echo
echo "[1/3] Regenerating coverage.json via section-15-wiring-cross-check"
bash "$REPO_ROOT/dev-tools/diagnostic/section-15-wiring-cross-check.command" \
  > /tmp/forest-wiring-audit-section15.log 2>&1
SEC15_RC=$?
if [ $SEC15_RC -ne 0 ] && [ $SEC15_RC -ne 1 ]; then
  # rc=0 PASS, rc=1 FAIL (with findings) — both valid; rc>=2 is a
  # script-level error (yaml parse blew up, etc.) — abort.
  echo "ERROR: section-15 crashed (rc=$SEC15_RC). See /tmp/forest-wiring-audit-section15.log"
  exit 2
fi
if [ ! -f "$COVERAGE_JSON" ]; then
  echo "ERROR: section-15 ran but produced no coverage.json at $COVERAGE_JSON"
  exit 2
fi
echo "      coverage.json regenerated"

echo
echo "[2/3] Resolving WiringSentinel instance_id"
SENTINEL_ID=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); ids=[a.get('instance_id') for a in agents if a.get('agent_name')=='WiringSentinel' and a.get('status')=='active']; print(ids[0] if ids else '')" 2>/dev/null \
  || echo "")
if [ -z "$SENTINEL_ID" ]; then
  echo "ERROR: no active WiringSentinel found. Run dev-tools/birth-wiring-sentinel.command first."
  exit 3
fi
echo "      WiringSentinel: $SENTINEL_ID"

echo
echo "[3/3] Dispatching wiring_audit.v1 with coverage as inputs"
# Compose the dispatch payload: read coverage.json, embed it.
PAYLOAD=$(python3 <<PYEOF
import json, sys, uuid, os
cov = json.load(open("$COVERAGE_JSON"))
payload = {
    "skill_name": "wiring_audit",
    "skill_version": "1",
    "session_id": f"wiring-audit-{uuid.uuid4()}",
    "inputs": {
        "coverage": cov,
        "triggered_by": os.environ.get("FSF_WIRING_AUDIT_TRIGGER", "scheduled"),
    },
}
print(json.dumps(payload))
PYEOF
)

# Endpoint is /agents/<id>/skills/run (ADR-0031 T2b), NOT /skills/call.
# /skills/call is for individual tool dispatch (ToolCallRequest); skills
# use the run pathway which loads the manifest from
# data/forge/skills/installed/<name>.v<version>.yaml.
DISPATCH_RESP=$(curl -s --max-time 60 "${DAEMON}/agents/${SENTINEL_ID}/skills/run" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" 2>&1)
echo "      Skill response (truncated):"
echo "$DISPATCH_RESP" | python3 -m json.tool 2>/dev/null | head -40 || echo "$DISPATCH_RESP" | head -40

# SkillRunResponse uses `status` (succeeded|failed|...) not `ok` (bool).
STATUS=$(echo "$DISPATCH_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status', 'unknown'))" 2>/dev/null || echo "unknown")
if [ "$STATUS" != "succeeded" ]; then
  echo "ERROR: wiring_audit.v1 dispatch returned status=$STATUS (expected 'succeeded')."
  exit 4
fi

echo
echo "=========================================================="
echo "Wiring audit complete"
echo "=========================================================="
echo
echo "  Coverage JSON: $COVERAGE_JSON"
echo "  Sentinel:      $SENTINEL_ID"
echo "  Outcome logged to lineage memory + audit chain."
echo
echo "Next scheduled run: +4 hours (per launchd plist)."
echo
exit 0
