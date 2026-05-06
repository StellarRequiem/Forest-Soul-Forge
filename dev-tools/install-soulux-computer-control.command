#!/usr/bin/env bash
# Install the soulux-computer-control plugin into the operator's
# active plugin root + reload the daemon so it picks up the
# new server. Captures stdout + stderr to /tmp/fsf-plugin-install.log
# so any failure surfaces for diagnosis.
#
# Created during the 2026-05-06 e2e test follow-up after the
# bare `fsf plugin install` step from the runbook didn't work
# in the operator's terminal.

set -uo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

LOG=/tmp/fsf-plugin-install.log
: > "$LOG"

bar() { printf '\n========== %s ==========\n' "$1" | tee -a "$LOG"; }

bar "0. environment check"
{
  echo "  cwd: $(pwd)"
  echo "  shell: $SHELL"
  echo "  PATH: $PATH"
  echo ""
  echo "  fsf on PATH? $(command -v fsf || echo 'NOT FOUND')"
  echo "  python3: $(command -v python3)"
  echo "  python3 version: $(python3 --version 2>&1)"
  echo ""
  echo "  daemon venv python: $HERE/.venv/bin/python"
  if [[ -x "$HERE/.venv/bin/python" ]]; then
    echo "  venv python version: $($HERE/.venv/bin/python --version 2>&1)"
    echo "  venv python pydantic: $($HERE/.venv/bin/python -c 'import pydantic; print(pydantic.VERSION)' 2>&1)"
  else
    echo "  ✗ venv python missing — abort"
  fi
  echo ""
  echo "  candidate plugin dir: ./examples/plugins/soulux-computer-control"
  ls -la ./examples/plugins/soulux-computer-control/ 2>&1 || echo "  ✗ plugin dir missing"
} 2>&1 | tee -a "$LOG"

bar "1. determine plugin install path"
PLUGIN_ROOT="$HOME/.forest/plugins"
{
  echo "  target plugin_root: $PLUGIN_ROOT"
  mkdir -p "$PLUGIN_ROOT"
  echo "  ✓ ensured exists"
} 2>&1 | tee -a "$LOG"

# Resolve which Python to use. Resolution order:
#   1. .venv/bin/python (the daemon's venv — has all deps)
#   2. command -v fsf (if the package was installed as a console script)
#   3. system python3 (last-resort; usually fails on missing deps —
#      this is what surfaced the bug on 2026-05-06: the operator's
#      shell python3 didn't have pydantic)
VENV_PY="$HERE/.venv/bin/python"
if [[ -x "$VENV_PY" ]]; then
  PY_INVOKE=("$VENV_PY" "-m" "forest_soul_forge.cli.main")
  PY_PYTHONPATH="$HERE/src"
elif command -v fsf >/dev/null 2>&1; then
  PY_INVOKE=("fsf")
  PY_PYTHONPATH=""
else
  PY_INVOKE=("python3" "-m" "forest_soul_forge.cli.main")
  PY_PYTHONPATH="$HERE/src"
fi

bar "2. invoke fsf plugin install (via ${PY_INVOKE[0]})"
{
  echo "  command: ${PY_INVOKE[*]} plugin install ./examples/plugins/soulux-computer-control --plugin-root $PLUGIN_ROOT"
  if [[ -n "$PY_PYTHONPATH" ]]; then
    PYTHONPATH="$PY_PYTHONPATH" "${PY_INVOKE[@]}" \
      plugin install ./examples/plugins/soulux-computer-control \
      --plugin-root "$PLUGIN_ROOT"
  else
    "${PY_INVOKE[@]}" plugin install ./examples/plugins/soulux-computer-control \
      --plugin-root "$PLUGIN_ROOT"
  fi
} 2>&1 | tee -a "$LOG"
RC=$?
echo "  rc=$RC" | tee -a "$LOG"

bar "3. verify install on disk"
{
  ls -la "$PLUGIN_ROOT/installed/soulux-computer-control/" 2>&1 || \
    echo "  ✗ install dir not found at $PLUGIN_ROOT/installed/soulux-computer-control/"
} 2>&1 | tee -a "$LOG"

bar "4. reload the daemon's plugin runtime"
TOKEN_FILE="$HERE/.env"
TOKEN=""
if [[ -f "$TOKEN_FILE" ]]; then
  TOKEN=$(grep -E '^FSF_API_TOKEN=' "$TOKEN_FILE" | head -1 | cut -d= -f2)
fi
{
  if [[ -z "$TOKEN" ]]; then
    echo "  ✗ no FSF_API_TOKEN found in .env"
  else
    echo "  POST /plugins/reload with token..."
    curl -s -w "\n  HTTP: %{http_code}\n" -X POST \
      "http://127.0.0.1:7423/plugins/reload" \
      -H "X-FSF-Token: $TOKEN" \
      -H "Content-Type: application/json" \
      -H "X-Idempotency-Key: install-$(date +%s)"
  fi
} 2>&1 | tee -a "$LOG"

bar "5. check daemon now sees the plugin"
{
  curl -s -H "X-FSF-Token: $TOKEN" http://127.0.0.1:7423/plugins | \
    python3 -c "import json,sys; d=json.load(sys.stdin); print('  active plugins:', [p.get('name') for p in d.get('active', d.get('plugins', []))])"
} 2>&1 | tee -a "$LOG"

bar "6. summary"
echo "  full log at: $LOG"
echo ""
echo "Done. Press return to close."
read -r _
