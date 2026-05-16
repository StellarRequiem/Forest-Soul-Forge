#!/usr/bin/env bash
# Quick fix: install python-multipart into the daemon's venv.
#
# The /voice/transcribe endpoint (ADR-0070 T2) uses FastAPI's
# Form(...) decorator, which requires python-multipart at module
# load time. The host venv was missing this package as of
# 2026-05-16, blocking daemon boot with:
#
#   RuntimeError: Form data requires "python-multipart" to be installed.
#
# pyproject.toml's daemon extras now include python-multipart
# (commit B335-fix) so future pip install -e .[daemon] will pick
# it up. This script is the one-shot fix for the existing venv.

set -uo pipefail
cd "$(dirname "$0")/.."

echo "=========================================================="
echo "fix-multipart-dep — install python-multipart into venv"
echo "=========================================================="
echo

if [ ! -x ".venv/bin/pip" ]; then
  echo "ERROR: .venv/bin/pip missing. Run start.command first."
  echo
  echo "Press any key to close."
  read -n 1
  exit 1
fi

echo "[1/2] installing python-multipart..."
.venv/bin/pip install --quiet python-multipart
echo "      ok"
echo

echo "[2/2] verifying import succeeds..."
if .venv/bin/python -c "import forest_soul_forge.daemon.app; print('OK')" 2>&1; then
  echo
  echo "=========================================================="
  echo "Fix applied. Next:"
  echo "  1. Run force-restart-daemon.command to bring the daemon up"
  echo "  2. Run birth-test-author.command to retry the birth"
  echo "=========================================================="
else
  echo
  echo "=========================================================="
  echo "Import STILL fails. The traceback above names the next"
  echo "missing piece. Share the bottom-most error line back."
  echo "=========================================================="
fi
echo
echo "Press any key to close this window."
read -n 1
