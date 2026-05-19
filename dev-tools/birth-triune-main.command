#!/bin/bash
# Birth the named *-Main triune for scheduled autonomous work.
#
# Three birth POSTs in sequence: Engineer-Main (software_engineer),
# Reviewer-Main (code_reviewer), Architect-Main (system_architect).
# Mirrors the birth-wiring-sentinel pattern. Idempotent — skips
# any agent that already exists.
#
# Why *-Main: chaz + Kraine are operator-named (B369 rebirth identities)
# and shouldn't be repurposed for scheduled work. The Main suffix
# tags these as the canonical scheduled-task triune; future
# scheduled-cadence work uses these specific instance_ids.
#
# Genre/posture:
#   Engineer-Main:  actuator genre, posture YELLOW (has shell_exec
#                   + code_edit — gated until operator allowlists
#                   paths/commands per ADR-0035 SW.A.5)
#   Reviewer-Main:  guardian genre, posture GREEN (read_only kit)
#   Architect-Main: researcher genre, posture GREEN (read_only kit;
#                   memory_write to private/lineage only)
#
# Per ADR-0034 §"SW track" the triune's working flow is:
#   Architect designs → Engineer writes → Reviewer reviews.
# For the scheduled wiring_audit_triage skill: the flow inverts
#   (Engineer reads sentinel memory → Reviewer ranks → Architect
#   synthesizes) because triage is a read-rank-synthesize task, not
#   a build-review-sign-off task. Both flow shapes use the same kits.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "Birth Triune-Main — Engineer + Reviewer + Architect"
echo "=========================================================="

birth_one() {
  local role="$1"
  local name="$2"
  local posture="$3"
  local posture_reason="$4"
  local idem="birth-${name,,}"

  echo
  echo "----- $name ($role) -----"
  EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=300" \
    -H "X-FSF-Token: $TOKEN" 2>/dev/null \
    | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); ids=[a.get('instance_id') for a in agents if a.get('agent_name')=='$name' and a.get('status')=='active']; print(ids[0] if ids else '')" 2>/dev/null \
    || echo "")
  if [ -n "$EXISTING" ]; then
    INSTANCE_ID="$EXISTING"
    echo "  $name already exists: $INSTANCE_ID — skipping birth"
  else
    BIRTH_PAYLOAD=$(cat <<JSON
{
  "profile": {
    "role": "$role",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "$name",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
    BIRTH_RESP=$(curl -s --max-time 30 "${DAEMON}/birth" \
      -H "X-FSF-Token: $TOKEN" \
      -H "Content-Type: application/json" \
      -H "X-Idempotency-Key: $idem" \
      -d "$BIRTH_PAYLOAD" 2>&1)
    INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
    if [ -z "$INSTANCE_ID" ]; then
      echo "  ERROR: birth failed:"
      echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -20 || echo "$BIRTH_RESP" | head -10
      exit 2
    fi
    echo "  born: $INSTANCE_ID"
  fi

  echo "  setting posture: $posture"
  POSTURE_RESP=$(curl -s --max-time 10 "${DAEMON}/agents/${INSTANCE_ID}/posture" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: posture-${name,,}-init" \
    -d "{\"posture\":\"$posture\",\"reason\":\"$posture_reason\"}" \
    2>&1)
  POSTURE_OK=$(echo "$POSTURE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('posture','?'))" 2>/dev/null || echo "?")
  echo "  posture: $POSTURE_OK"
}

echo
echo "[1/3] Engineer-Main (software_engineer, actuator, posture YELLOW)"
birth_one "software_engineer" "Engineer-Main" "yellow" \
  "Triune-Main schedule-cadence writer. Actuator genre — has shell_exec.v1 + code_edit.v1. Posture YELLOW gates code_edit allowed_paths + shell_exec allowed_commands; operator promotes to GREEN after the per-tool constraints are operator-reviewed."

echo
echo "[2/3] Reviewer-Main (code_reviewer, guardian, posture GREEN)"
birth_one "code_reviewer" "Reviewer-Main" "green" \
  "Triune-Main schedule-cadence reviewer. Guardian genre, read_only kit (llm_think + memory + code_read + text_summarize + code_explain + commit_message + email_draft + tone_shift). Output is text-only verdicts; no side effects."

echo
echo "[3/3] Architect-Main (system_architect, researcher, posture GREEN)"
birth_one "system_architect" "Architect-Main" "green" \
  "Triune-Main schedule-cadence synthesizer. Researcher genre, read_only kit (llm_think + memory + code_read). Output is design memos written to private/lineage memory only."

echo
echo "=========================================================="
echo "Triune-Main birth complete"
echo "=========================================================="
echo
echo "Next: schedule wiring_audit_triage.v1 against this triune."
echo
echo "Press any key to close."
read -n 1 || true
