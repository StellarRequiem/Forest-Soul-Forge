#!/bin/bash
# ADR-0088 Phase A — birth ContentResearcher-D7 (content_researcher role).
#
# Researcher-genre sourcing role for D7 Content Studio. Pulls
# source material from operator-allowlisted sources + lineage
# memory (D1 catalog via memory_recall), produces structured
# research briefs the writer + editor + style_steward consume.
# Renamed from manifest's bare "researcher" to avoid collision
# with the researcher genre name (same disambiguation as D1's
# knowledge_verifier vs. verifier_loop). Per ADR-0088 Decision 2.
#
# Per-tool constraint patches (ADR-0088 §content_researcher kit):
#   web_fetch:       allowed_hosts to operator-allowlisted sources;
#                    fallback to a conservative default allowlist
#                    when no custom list is configured. The constitution
#                    policy require_source_allowlist_check fires
#                    regardless.
#
# Posture: GREEN per ADR-0088 Decision 1 — read-from-network +
# write-to-private-memory is non-acting; brief discipline is
# writer-pickup-driven regardless of posture.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0088 Phase A — Birth ContentResearcher-D7"
echo "=========================================================="

echo
echo "[1/4] Restarting daemon to load content_researcher role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 7
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/4] Checking for existing ContentResearcher-D7"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='ContentResearcher-D7']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      ContentResearcher-D7 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing ContentResearcher-D7 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "content_researcher",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "ContentResearcher-D7",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 120 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-content-researcher-d7" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      ContentResearcher-D7 born: instance_id=${INSTANCE_ID}"
fi

echo
echo "[3/4] Patching ContentResearcher-D7's constitution with per-tool constraints"
CONST_PATH=$(curl -s --max-time 5 "${DAEMON}/agents/${INSTANCE_ID}" \
  -H "X-FSF-Token: $TOKEN" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('constitution_path',''))" 2>/dev/null)

if [ -z "$CONST_PATH" ] || [ ! -f "$CONST_PATH" ]; then
  echo "      WARN: could not resolve constitution_path for ${INSTANCE_ID}"
  echo "      Skipping constraint patch — ContentResearcher-D7 will run with"
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
            "www.rfc-editor.org",
            "datatracker.ietf.org",
        ])
        constraints["forbidden_hosts"] = [
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
echo "[4/4] Setting ContentResearcher-D7's posture to GREEN"
POSTURE_RESP=$(curl -s --max-time 10 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-content-researcher-d7-init" \
  -d '{"posture": "green", "reason": "ADR-0088 Decision 1 — content_researcher read-from-network + write-to-private-memory is non-acting; brief discipline is writer-pickup-driven regardless of posture."}' 2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "ContentResearcher-D7 ready. Summary:"
echo "  instance_id:   ${INSTANCE_ID}"
echo "  role:          content_researcher"
echo "  genre:         researcher"
echo "  posture:       green"
echo "  skill:         content_research.v1"
echo "  next steps:    dispatch content_research with a topic_slug"
echo "                 + optional source_url to compose a research"
echo "                 brief. Writer-D7's draft_writing skill picks"
echo "                 the brief up via memory_recall."
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true
