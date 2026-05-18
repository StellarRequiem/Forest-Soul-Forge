#!/bin/bash
# B376 - rebirth Kraine / Victor / chaz from the quarantine list.
#
# Three agents born 2026-05-07 with manually-appended free-text
# 'override' blocks at EOF of their constitution YAML. The text
# isn't a YAML key:value pair so the file fails to parse:
#   "while scanning a simple key... could not find expected ':'"
#
# Per CLAUDE.md architectural invariant ("Constitution hash is
# immutable per agent. A born agent's constitution hash is bound
# to its identity"), we CANNOT rewrite the YAML to fix parse.
# Doing so changes constitution_hash and breaks audit chain
# integrity for every entry referencing the old hash.
#
# Rebirth path (operator choice, 2026-05-17 evening):
#   For each broken agent:
#     1. Archive the old instance_id via POST /archive with reason
#        recording the lineage decision.
#     2. POST /birth with the same role + agent_name. The daemon
#        mints a NEW instance_id with a clean role-derived
#        constitution.
#     3. Operator can later re-attach per-agent posture / notes
#        via the proper substrate (ADR-0036 posture pipeline) -
#        NOT by appending free text to the constitution YAML.
#
# Lineage record:
#   The archive event records old instance_id + reason ("rebirth
#   via 2026-05-17 quarantine resolution"). The new birth event
#   records the new instance_id. Cross-reference between them
#   lives in docs/audits/2026-05-17-quarantine-rebirth.md (this
#   commit lands the audit doc alongside the operation log).
#
# Agents being rebirthed:
#   1. Kraine    - role=system_architect      (old instance: system_architect_054edc592917)
#   2. Victor    - role=knowledge_consolidator (old instance: knowledge_consolidator_9dd33078e7bd)
#   3. chaz      - role=software_engineer     (old instance: software_engineer_871a237714a1)
#
# This script:
#   - Archives each old instance with a recorded reason.
#   - Births the new instance with the same role + agent_name.
#   - Captures the (old, new) pair for each into a JSON log at
#     data/test-runs/rebirth-2026-05-17.json.
#   - Does NOT remove agent_quarantine.yaml entries (separate
#     commit clears them once the rebirths are confirmed).

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="${FSF_DAEMON_URL:-http://127.0.0.1:7423}"

OUT_DIR="$(pwd)/data/test-runs"
mkdir -p "$OUT_DIR"
LOG_JSON="$OUT_DIR/rebirth-2026-05-17.json"
ARCHIVE_REASON="rebirth via 2026-05-17 quarantine resolution; old constitution unparseable (manual free-text append at EOF); operator opted for new instance_id with clean role-derived constitution; lineage in docs/audits/2026-05-17-quarantine-rebirth.md"

echo "=========================================================="
echo "Rebirth quarantined agents — 2026-05-17"
echo "=========================================================="

# Confirm daemon reachable.
if ! curl -s --max-time 5 "${DAEMON}/healthz" -H "X-FSF-Token: $TOKEN" >/dev/null 2>&1; then
  echo "ERROR: daemon unreachable at ${DAEMON}"
  echo "Run dev-tools/force-restart-daemon.command first."
  exit 2
fi

# Track each (old, new) pair via line-per-pair text file. Bash
# arrays make this awkward when results contain JSON quoting; a
# tab-separated text file is simpler and the consumer (python3
# at the end) reads it cleanly.
PAIRS_TSV="$OUT_DIR/rebirth-2026-05-17.pairs.tsv"
: > "$PAIRS_TSV"

# Helper: archive an old instance + birth a new one with the
# same role + agent_name. Writes (agent_name, role, old, new) to
# the pairs TSV on success.
rebirth_one() {
  local old_id="$1"
  local role="$2"
  local agent_name="$3"

  echo
  echo "--- $agent_name (role=$role) ---"
  echo "old instance_id: $old_id"

  # Archive (idempotent: 404 if already archived, treat as already-done).
  echo "  archiving..."
  local arch_payload
  arch_payload=$(python3 -c "import json; print(json.dumps({'instance_id':'$old_id','reason':'$ARCHIVE_REASON','archived_by':'alex'}))")
  local arch_resp
  arch_resp=$(curl -s --max-time 15 -w '\n%{http_code}' "${DAEMON}/archive" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: rebirth-arch-${old_id}-2026-05-17" \
    -d "$arch_payload" 2>&1)
  local arch_code
  arch_code=$(echo "$arch_resp" | tail -1)
  local arch_body
  arch_body=$(echo "$arch_resp" | sed '$d')
  case "$arch_code" in
    200|201) echo "  archived ok (HTTP $arch_code)" ;;
    404)     echo "  archive returned 404 - agent already gone or wrong id" ;;
    *)       echo "  WARN: archive HTTP $arch_code - body: $arch_body" ;;
  esac

  # Birth new instance.
  echo "  birthing new $agent_name..."
  local birth_payload
  birth_payload=$(python3 -c "import json; print(json.dumps({'profile':{'role':'$role','trait_values':{},'domain_weight_overrides':{}},'agent_name':'$agent_name','agent_version':'v1','owner_id':'alex'}))")
  local birth_resp
  birth_resp=$(curl -s --max-time 30 -w '\n%{http_code}' "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: rebirth-birth-${agent_name}-2026-05-17" \
    -d "$birth_payload" 2>&1)
  local birth_code
  birth_code=$(echo "$birth_resp" | tail -1)
  local birth_body
  birth_body=$(echo "$birth_resp" | sed '$d')
  if [ "$birth_code" != "200" ] && [ "$birth_code" != "201" ]; then
    echo "  ERROR: birth HTTP $birth_code - body: $birth_body"
    return 1
  fi
  local new_id
  new_id=$(echo "$birth_body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$new_id" ]; then
    echo "  ERROR: birth response had no instance_id"
    echo "  body: $birth_body"
    return 1
  fi
  echo "  new instance_id: $new_id"

  printf "%s\t%s\t%s\t%s\n" "$agent_name" "$role" "$old_id" "$new_id" >> "$PAIRS_TSV"
}

rebirth_one "system_architect_054edc592917"      "system_architect"       "Kraine"
rebirth_one "knowledge_consolidator_9dd33078e7bd" "knowledge_consolidator" "Victor"
rebirth_one "software_engineer_871a237714a1"     "software_engineer"      "chaz"

# Write the JSON log from the TSV.
python3 - "$PAIRS_TSV" "$LOG_JSON" <<'PY'
import json, sys
pairs_tsv, log_json = sys.argv[1:3]
pairs = []
with open(pairs_tsv) as f:
    for line in f:
        line = line.rstrip("\n")
        if not line:
            continue
        agent_name, role, old_id, new_id = line.split("\t")
        pairs.append({
            "agent_name": agent_name, "role": role,
            "old_instance": old_id, "new_instance": new_id,
        })
out = {
    "date": "2026-05-17",
    "driver": "B376 rebirth-quarantined-agents.command",
    "reason": "rebirth from 2026-05-17 quarantine list",
    "pairs": pairs,
}
with open(log_json, "w") as f:
    json.dump(out, f, indent=2)
print(f"Lineage log written: {log_json}")
print(json.dumps(out, indent=2))
PY

echo
echo "=========================================================="
echo "Rebirth complete — 3 agents"
echo "  Lineage log: $LOG_JSON"
echo "  Next: commit B376 lands docs/audits/2026-05-17-quarantine-rebirth.md"
echo "  Then: commit clears agent_quarantine.yaml entries"
echo "=========================================================="
echo
echo "Press any key to close."
read -n 1 || true
