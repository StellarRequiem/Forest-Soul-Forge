#!/bin/bash
# ADR-0085 Phase A — birth AuditArchivist-D8 (audit_archivist role).
#
# Mirrors birth-forensic-archivist.command's 4-phase shape — they are
# sibling guardians (forensic_archivist for D3 SOC chain-of-custody;
# audit_archivist for D8 compliance long-term archival). Idempotent:
# re-runs skip the birth if AuditArchivist-D8 already exists.
#
# Per-tool constraint patches (ADR-0085 §audit_archivist kit):
#   code_read:       allowed_paths to the evidence corpus + the audit
#                    chain + framework yaml directory.
#   file_integrity:  allowed_paths to the evidence corpus + audit
#                    chain (for archival hash verification).
#   memory_write / memory_recall: no path constraint (private memory).
#   audit_chain_verify / delegate / llm_think / text_summarize: no
#   constraints.
#
# Posture: GREEN per ADR-0085 Decision 5 — long-term archival
# attestation is non-acting; the gate is the operator's later use
# of the audit packet, not the archivist's attestation.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0085 Phase A — Birth AuditArchivist-D8"
echo "=========================================================="

echo
echo "[1/4] Restarting daemon to load audit_archivist role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 6
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/4] Checking for existing AuditArchivist-D8"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='AuditArchivist-D8']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      AuditArchivist-D8 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing AuditArchivist-D8 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "audit_archivist",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "AuditArchivist-D8",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 120 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-audit-archivist-d8" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      AuditArchivist-D8 born: instance_id=${INSTANCE_ID}"
fi

mkdir -p "$(pwd)/data/compliance"
mkdir -p "$(pwd)/data/compliance/evidence"
mkdir -p "$(pwd)/data/compliance/archive"

echo
echo "[3/4] Patching AuditArchivist-D8's constitution with per-tool constraints"
CONST_PATH=$(curl -s --max-time 5 "${DAEMON}/agents/${INSTANCE_ID}" \
  -H "X-FSF-Token: $TOKEN" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('constitution_path',''))" 2>/dev/null)

if [ -z "$CONST_PATH" ] || [ ! -f "$CONST_PATH" ]; then
  echo "      WARN: could not resolve constitution_path for ${INSTANCE_ID}"
  echo "      Skipping constraint patch — AuditArchivist-D8 will run"
  echo "      with the guardian-genre defaults until manually patched."
else
  echo "      Constitution at: $CONST_PATH"
  VENV_PY="$(pwd)/.venv/bin/python3"
  "$VENV_PY" - "$CONST_PATH" <<'PY'
import sys, yaml
path = sys.argv[1]
with open(path) as f:
    doc = yaml.safe_load(f)
patched = []
for entry in doc.get("tools") or []:
    if not isinstance(entry, dict):
        continue
    name = entry.get("name", "")
    constraints = entry.setdefault("constraints", {}) or {}
    if name == "code_read":
        constraints["allowed_paths"] = [
            "data/compliance/",
            "config/compliance_frameworks/",
            "examples/audit_chain.jsonl",
            "examples/segments/",
        ]
        constraints["forbidden_paths"] = [
            "src/",
            "data/registry.sqlite",
            ".env",
            "~/.fsf/secrets",
        ]
        patched.append("code_read")
    elif name == "file_integrity":
        constraints["allowed_paths"] = [
            "data/compliance/",
            "examples/audit_chain.jsonl",
            "examples/segments/",
        ]
        constraints["forbidden_paths"] = [
            "src/",
            "config/",
            "data/registry.sqlite",
            ".env",
            "~/.fsf/secrets",
        ]
        patched.append("file_integrity")
if patched:
    with open(path, "w") as f:
        yaml.safe_dump(doc, f, sort_keys=False, default_flow_style=False, allow_unicode=True)
    print(f"      Constraints patched: {', '.join(patched)}")
else:
    print("      (no matching tools found for constraint patch)")
PY
fi

echo
echo "[4/4] Setting AuditArchivist-D8's posture to GREEN"
POSTURE_RESP=$(curl -s --max-time 10 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-audit-archivist-d8-init" \
  -d '{"posture": "green", "reason": "ADR-0085 Decision 5 — long-term archival attestation is non-acting; the gate is the operators later use of the audit packet, not the archivists attestation"}' 2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "AuditArchivist-D8 ready. Summary:"
echo "  instance_id:    ${INSTANCE_ID}"
echo "  role:           audit_archivist"
echo "  genre:          guardian"
echo "  posture:        green"
echo "  archive root:   data/compliance/archive/ (created if absent)"
echo "  next steps:     Phase B compliance_scanner + framework_check.v1"
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true
