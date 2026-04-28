#!/usr/bin/env bash
# Forest Soul Forge — install all 21 swarm skill manifests.
#
# Copies (or fsf-installs) every YAML in examples/skills/ into the
# daemon's skill_install_dir, then triggers /skills/reload so the
# catalog picks them up without a daemon restart.
#
# Usage:
#   ./scripts/security-swarm-install-skills.sh [--dest <dir>]
#
# Default dest: data/forge/skills/installed (the daemon's
# skill_install_dir default per DaemonSettings).
#
# Env:
#   FSF_DAEMON_URL   daemon URL for the reload trigger
#   FSF_API_TOKEN    auth token if reload is gated

set -uo pipefail

DEST="data/forge/skills/installed"
DAEMON="${FSF_DAEMON_URL:-http://127.0.0.1:7423}"
TOKEN="${FSF_API_TOKEN:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dest) DEST="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

mkdir -p "$DEST"
copied=0
for f in examples/skills/*.yaml; do
  [[ -f "$f" ]] || continue
  cp "$f" "$DEST/"
  copied=$((copied + 1))
done
echo "copied $copied manifests to $DEST"

# Trigger /skills/reload so the catalog picks up new manifests.
auth_header=""
[[ -n "$TOKEN" ]] && auth_header="-H X-FSF-Token: $TOKEN"

resp=$(curl -sf -X POST "$DAEMON/skills/reload" $auth_header || true)
if [[ -n "$resp" ]]; then
  echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  reload: {d.get(\"status\",\"?\")}, loaded={d.get(\"loaded\",0)}')" 2>/dev/null || echo "  reload response: $resp"
else
  echo "  reload skipped (daemon unreachable or endpoint missing)"
fi
