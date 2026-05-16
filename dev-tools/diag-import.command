#!/usr/bin/env bash
# Diagnostic: surfaces the actual ImportError that start.command's
# silent check is hiding. Run from Finder; the window stays open
# until you press a key.
#
# Why: start.command's import probe redirects stderr to /dev/null,
# so when forest_soul_forge.daemon.app fails to import the operator
# sees only "Install reported success but the package still won't
# import" with no traceback. This script runs the same import with
# stderr visible.

set -uo pipefail
cd "$(dirname "$0")/.."

echo "=========================================================="
echo "diag-import — surfacing the hidden ImportError"
echo "=========================================================="
echo

if [ ! -x ".venv/bin/python" ]; then
  echo "ERROR: .venv/bin/python missing. Run start.command from scratch."
  echo
  echo "Press any key to close."
  read -n 1
  exit 1
fi

echo "[1/3] venv python version:"
.venv/bin/python --version
echo

echo "[2/3] installed packages (filtered to daemon-relevant):"
.venv/bin/pip list 2>/dev/null | grep -iE "^(fastapi|uvicorn|pydantic|pyyaml|sqlcipher|cryptography|httpx|python-multipart)" || true
echo

echo "[3/3] running 'import forest_soul_forge.daemon.app' with full traceback:"
echo "----------------------------------------------------------"
.venv/bin/python -c "import forest_soul_forge.daemon.app; print('OK — import succeeded')" 2>&1
echo "----------------------------------------------------------"
echo
echo "If you saw 'OK — import succeeded' the daemon should boot."
echo "If you saw a traceback, the bottom-most line names the missing"
echo "module or the file where the error originates. Share it back."
echo
echo "Press any key to close this window."
read -n 1
