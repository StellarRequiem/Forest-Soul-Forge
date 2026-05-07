#!/bin/bash
# Start the frontend dev HTTP server (frontend/serve.py).
#
# Foreground; closing the Terminal window kills the server.
# For a persistent server, use the launchd plist
# (dev.forest.frontend.plist — not yet scaffolded; queued).

REPO="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PY="$REPO/.venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  echo "ERROR: no $VENV_PY"
  exit 1
fi
# serve.py serves the CWD — must cd into frontend/ first
# (the previous script ran from repo root and looked for
# index.html there; serve.py errored with 'No index.html in
# .../Forest-Soul-Forge').
cd "$REPO/frontend"

# Confirm port 5173 isn't already in use.
if lsof -nP -iTCP:5173 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port 5173 already in use — frontend is already running."
  echo "Open http://127.0.0.1:5173/?api=http://127.0.0.1:7423"
  echo
  echo "Press any key to close."
  read -n 1
  exit 0
fi

echo "Starting frontend HTTP server on 127.0.0.1:5173 ..."
echo "Dashboard URL: http://127.0.0.1:5173/?api=http://127.0.0.1:7423"
echo "Stop: close this Terminal window or Ctrl+C below."
echo

# Run serve.py directly (cd'd into frontend/ above). Running it
# as `python -m frontend.serve` from inside frontend/ fails with
# ModuleNotFoundError because there's no `frontend/` sub-package
# under the CWD. Direct invocation is cleaner.
exec "$VENV_PY" serve.py
