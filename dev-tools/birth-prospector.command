#!/bin/bash
# ADR-0086 Phase A — birth Prospector-D1 (prospector role).
#
# Researcher-genre sourcing role for D1 Knowledge Forge. Pulls
# source material from operator-allowlisted sources and hands
# off to Librarian-D1 for catalog discipline. NEVER persists to
# operator-visible storage directly — sibling pattern to
# paper_summarizer but for sustained sourcing.
#
# Per-tool constraint patches (ADR-0086 §prospector kit):
#   web_fetch:       allowed_hosts to operator-allowlisted sources;
#                    fallback to a conservative default allowlist
#                    when no custom list is configured. The constitution
#                    policy require_source_allowlist_check fires
#                    regardless.
#   memory_write / memory_recall: no path constraint (private memory).
#   operator_profile_read / audit_chain_verify / delegate / llm_think /
#     text_summarize: no constraints.
#
# Posture: GREEN per ADR-0086 Decision 1 — read-from-network +
# write-to-private-memory is non-acting; catalog discipline is
# librarian's lane regardless of posture.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0086 Phase A — Birth Prospector-D1"
echo "=========================================================="

echo
echo "[1/4] Restarting daemon to load prospector role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 6
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/4] Checking for existing Prospector-D1"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='Prospector-D1']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      Prospector-D1 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing Prospector-D1 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "prospector",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "Prospector-D1",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 120 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-prospector-d1" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      Prospector-D1 born: instance_id=${INSTANCE_ID}"
fi

mkdir -p "$(pwd)/data/knowledge/prospector_inbox"

echo
echo "[3/4] Patching Prospector-D1's constitution with per-tool constraints"
CONST_PATH=$(curl -s --max-time 5 "${DAEMON}/agents/${INSTANCE_ID}" \
  -H "X-FSF-Token: $TOKEN" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('constitution_path',''))" 2>/dev/null)

if [ -z "$CONST_PATH" ] || [ ! -f "$CONST_PATH" ]; then
  echo "      WARN: could not resolve constitution_path for ${INSTANCE_ID}"
  echo "      Skipping constraint patch — Prospector-D1 will run with"
  echo "      researcher-genre defaults until manually patched."
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
    if name == "web_fetch":
        # Default conservative allowlist; operator widens at runtime
        # via the per-(agent, tool) grant surface (ADR-0053).
        constraints.setdefault("allowed_hosts", [
            "arxiv.org",
            "openreview.net",
            "github.com",
            "raw.githubusercontent.com",
            "en.wikipedia.org",
        ])
        constraints["forbidden_hosts"] = [
            # Defense-in-depth — the operator allowlist is the
            # primary gate; this list catches obvious holes if
            # the allowlist is accidentally over-broadened.
            "localhost",
            "127.0.0.1",
            "169.254.169.254",
        ]
        patched.append("web_fetch")
if patched:
    with open(path, "w") as f:
        yaml.safe_dump(doc, f, sort_keys=False, default_flow_style=False, allow_unicode=True)
    print(f"      Constraints patched: {', '.join(patched)}")
else:
    print("      (no matching tools found for constraint patch)")
PY
fi

echo
echo "[4/4] Setting Prospector-D1's posture to GREEN"
POSTURE_RESP=$(curl -s --max-time 10 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-prospector-d1-init" \
  -d '{"posture": "green", "reason": "ADR-0086 Decision 1 — prospector read-from-network + write-to-private-memory is non-acting; catalog discipline is librarian lane regardless of posture."}' 2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "Prospector-D1 ready. Summary:"
echo "  instance_id:   ${INSTANCE_ID}"
echo "  role:          prospector"
echo "  genre:         researcher"
echo "  posture:       green"
echo "  inbox root:    data/knowledge/prospector_inbox/ (created if absent)"
echo "  next steps:    run an initial research_gathering skill"
echo "                 dispatch, then verify Librarian-D1 catalog"
echo "                 pickup via knowledge_curation."
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true
