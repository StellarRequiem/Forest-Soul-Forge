#!/usr/bin/env bash
# Run pytest directly via the project venv — no Docker required.
#
# The dockerized run-tests.command needs Docker Desktop running. This
# is a faster alternative: same test suite, run against the same Python
# version (3.14 from Homebrew) the daemon itself uses, no container
# spin-up. Trade-off: dep mismatch is theoretically possible if the
# venv drifts from Dockerfile.test, but in practice both are pinned by
# pyproject.toml so they stay aligned.
#
# Use: double-click from Finder.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

PYTHON=".venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "error: $PYTHON not found. Run 'uv sync' or recreate the venv first."
  echo ""
  echo "Press return to close."
  read -r _
  exit 1
fi

bar() { printf '\n========== %s ==========\n' "$1"; }

bar "pytest discovery"
"$PYTHON" -m pytest --collect-only -q 2>&1 | tail -20

mkdir -p data/test-runs
LOG="data/test-runs/pytest-$(date +%s).log"
bar "running full test suite → $LOG"
"$PYTHON" -m pytest tests/ -v --tb=short 2>&1 | tee "$LOG"
RESULT=${PIPESTATUS[0]}
echo ""
echo "Full output saved to: $LOG"

bar "summary"
if [[ $RESULT -eq 0 ]]; then
  echo "✓ ALL TESTS PASSED"
else
  echo "✗ TEST RUN FAILED (exit code: $RESULT)"
  echo ""
  echo "Scroll up for individual failures."
fi

echo ""
echo "Press return to close."
read -r _
exit $RESULT
