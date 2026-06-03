#!/usr/bin/env bash
# One-click runner for the Golden Demo (double-clickable on macOS).
# Runs entirely local against a throwaway temp dir — touches no real state.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
PY="$REPO/.venv/bin/python3"; [ -x "$PY" ] || PY=python3
exec "$PY" "$HERE/golden_demo.py"
