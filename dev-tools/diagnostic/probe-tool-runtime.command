#!/usr/bin/env bash
# One-shot probe: fetch /healthz on the host, extract the
# tool_runtime diagnostic block, save to a known path the
# sandbox agent can read.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
OUT="$REPO_ROOT/data/test-runs/probe-tool-runtime/healthz.json"
mkdir -p "$(dirname "$OUT")"

ENV_FILE="$REPO_ROOT/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" 2>/dev/null | cut -d= -f2)

echo "Fetching /healthz..."
curl -s --max-time 5 \
  -H "X-FSF-Token: $TOKEN" \
  "http://127.0.0.1:7423/healthz" > "$OUT" 2>&1

echo "Saved to: $OUT"
echo
echo "tool_runtime block:"
python3 -c "
import json, sys
data = json.load(open('$OUT'))
sd = data.get('startup_diagnostics') or {}
# sd may be list or dict; handle both.
items = sd.items() if isinstance(sd, dict) else enumerate(sd)
for i, (k, v) in enumerate(items if isinstance(sd, dict) else enumerate(sd)):
    pass
# Simpler:
if isinstance(sd, dict):
    block = sd.get('tool_runtime', sd.get(5))
elif isinstance(sd, list):
    block = sd[5] if len(sd) > 5 else None
else:
    block = None
print(json.dumps(block, indent=2) if block else 'No tool_runtime entry found')
print()
print('--- full startup_diagnostics ---')
print(json.dumps(sd, indent=2))
"

echo
echo "Press any key to close."
read -n 1 || true
