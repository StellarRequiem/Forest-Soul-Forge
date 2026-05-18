#!/bin/bash
# ADR-0064 T6 (B385) — birth ThreatIntelCurator-D3 (threat_intel_curator role).
#
# Mirrors birth-telemetry-steward.command's 4-phase shape. Idempotent.
# Posture: YELLOW per ADR-0064 T6 — the curator pulls external content,
# so its initial reach exceeds telemetry_steward's pure-read posture.
# Operator can promote to GREEN after the allowlist is configured at
# config/threat_intel_sources.yaml (a future ADR will land that file;
# today the operator passes URLs through skill inputs and the YELLOW
# posture gates external-reach until that surface is properly
# allowlisted).
#
# Per-tool constraint patches:
#   web_fetch: allowed_domains set to empty at birth — operator
#              MUST configure via posture / constitution patch
#              before the curator can pull any feed. This is the
#              forbid_silent_feed_substitution policy enforced
#              at the kit level.
#   file_integrity: allowed_paths to data/intel_cache/ (operator
#              creates this directory at first use).
#   Other tools: standard read-only constraints.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0064 T6 — Birth ThreatIntelCurator-D3"
echo "=========================================================="

echo
echo "[1/4] Restarting daemon to load threat_intel_curator role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 6
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/4] Checking for existing ThreatIntelCurator-D3"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='ThreatIntelCurator-D3']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      ThreatIntelCurator-D3 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing ThreatIntelCurator-D3 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "threat_intel_curator",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "ThreatIntelCurator-D3",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 30 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-threat-intel-curator-d3" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      ThreatIntelCurator-D3 born: instance_id=${INSTANCE_ID}"
fi

echo
echo "[3/4] Verifying ThreatIntelCurator-D3 constitution parses cleanly"
DETAIL=$(curl -s --max-time 5 "${DAEMON}/agents/${INSTANCE_ID}" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null)
CONST_PATH=$(echo "$DETAIL" | python3 -c "import sys,json; print(json.load(sys.stdin).get('constitution_path',''))" 2>/dev/null)
if [ -n "$CONST_PATH" ] && [ -f "$CONST_PATH" ]; then
  VENV_PY="$(pwd)/.venv/bin/python3"
  if "$VENV_PY" -c "import yaml; yaml.safe_load(open('$CONST_PATH'))" >/dev/null 2>&1; then
    echo "      Constitution parses cleanly"
  else
    echo "      ERROR: constitution YAML parse failed."
    exit 3
  fi
else
  echo "      WARN: could not resolve constitution_path"
fi

echo
echo "[4/4] Setting posture: YELLOW (until operator configures allowlist)"
POSTURE_RESP=$(curl -s --max-time 10 "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-threat-intel-curator-d3-init" \
  -d '{"posture":"yellow","reason":"ADR-0064 T6: external reach (web_fetch) gated YELLOW until operator configures the source allowlist (future config/threat_intel_sources.yaml). Operator promotes to GREEN after allowlist is in place."}' \
  2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "ThreatIntelCurator-D3 birth complete"
echo "=========================================================="
echo
echo "  instance_id:   $INSTANCE_ID"
echo "  role:          threat_intel_curator"
echo "  genre:         guardian"
echo "  posture:       yellow (operator promotes after allowlist)"
echo "  skill:         threat_intel_refresh.v1 (operator-driven)"
echo
echo "Next operator step:"
echo "  Define allowlist of intel sources. Today: pass URLs via skill"
echo "  inputs. Future: config/threat_intel_sources.yaml (own ADR)."
echo
echo "Press any key to close."
read -n 1 || true
