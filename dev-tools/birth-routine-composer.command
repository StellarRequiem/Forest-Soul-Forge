#!/bin/bash
# ADR-0091 Phase C — birth RoutineComposer-D5 (routine_composer role).
#
# Actuator-genre routine queue writer role for D5 Smart Home
# Brain. The ONLY acting role in D5. Dispatches
# home_state_snapshot.v1 (read_only) + routine_compose.v1
# (filesystem; requires_human_approval=True) to compose
# deterministic routine envelopes (vacation_mode /
# morning_sequence / focus_mode / sleep_mode / custom) into
# data/d5/routine_queue.jsonl. NEVER fires routines directly —
# queue → forest-home-assistant connector OR operator pickup is
# the load-bearing separation per ADR-0091 Decision 2.
#
# Posture: YELLOW per ADR-0091 Decision 2 — every routine queue
# write is operator-gated per-call. require_human_approval_per_routine
# + require_snapshot_before_routine + require_routine_attestation
# policies enforce the discipline at governance layer regardless
# of posture.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0091 Phase C — Birth RoutineComposer-D5"
echo "=========================================================="

echo
echo "[1/3] Restarting daemon to load routine_composer role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 7
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/3] Checking for existing RoutineComposer-D5"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='RoutineComposer-D5']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      RoutineComposer-D5 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing RoutineComposer-D5 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "routine_composer",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "RoutineComposer-D5",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 120 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-routine-composer-d5" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      RoutineComposer-D5 born: instance_id=${INSTANCE_ID}"
fi

echo
echo "[3/3] Setting RoutineComposer-D5's posture to YELLOW"
POSTURE_RESP=$(curl -s --max-time 10 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-routine-composer-d5-init" \
  -d '{"posture": "yellow", "reason": "ADR-0091 Decision 2 — routine_composer defaults YELLOW. Every routine queue write is filesystem-class + per-call operator-gated regardless of posture; YELLOW is the second discipline (auto-queue every non-read-only dispatch). routine_compose.v1 carries requires_human_approval=True at the tool layer too. Operator flips to GREEN only after proposal-quality bedded in."}' 2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "RoutineComposer-D5 ready. Summary:"
echo "  instance_id:   ${INSTANCE_ID}"
echo "  role:          routine_composer"
echo "  genre:         actuator"
echo "  posture:       yellow (per-call approval + auto-queue)"
echo "  skills:        routine_management.v1, vacation_mode.v1"
echo "  queue path:    data/d5/routine_queue.jsonl (created on first call)"
echo "  next steps:    1. Dispatch routine_management OR vacation_mode"
echo "                    with a routine_kind + name + scheduled_for +"
echo "                    actions list."
echo "                 2. Approve the queued routine via the operator"
echo "                    approval queue (YELLOW posture)."
echo "                 3. (Future) forest-home-assistant connector picks"
echo "                    the queue record up at scheduled_for + applies"
echo "                    the routine; writes home_state_snapshot back."
echo "                 4. Until connector ships, operator picks queue"
echo "                    records up manually + applies via HA app."
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true
