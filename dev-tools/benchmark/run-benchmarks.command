#!/usr/bin/env bash
# Wrapper around run_substrate_perf.py — runnable via Finder cmd+O.
# Substrate-perf benchmark; distinct from ADR-0023's per-genre
# quality battery scope (which remains Proposed).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "=========================================================="
echo "FSF substrate-perf benchmark runner"
echo "=========================================================="
echo

if [ -x .venv/bin/python ]; then
  .venv/bin/python dev-tools/benchmark/run_substrate_perf.py
elif command -v python3 >/dev/null 2>&1; then
  python3 dev-tools/benchmark/run_substrate_perf.py
else
  echo "ERROR: no python3 found. Aborting."
  exit 1
fi

echo
echo "Done. Latest results under data/test-runs/benchmark-substrate-perf-*/"
echo
echo "Press any key to close."
read -n 1 || true
