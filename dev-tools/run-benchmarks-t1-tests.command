#!/usr/bin/env bash
# One-off: run the ADR-0023 T1 unit tests (fixture loader + scoring
# functions). Writes a marker file so the sandbox can poll for done.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.."

OUT=/tmp/forest-benchmarks-t1-test.out
echo "Starting at $(date)" > "$OUT"

if [ -x .venv/bin/pytest ]; then
  .venv/bin/pytest tests/unit/test_benchmarks_fixture.py tests/unit/test_benchmarks_scoring.py -v 2>&1 | tee -a "$OUT"
  RC=${PIPESTATUS[0]}
else
  echo "ERROR: no .venv/bin/pytest" | tee -a "$OUT"
  RC=1
fi

echo "DONE rc=$RC at $(date)" >> "$OUT"
echo
echo "Output saved to $OUT"
echo "Press any key to close."
read -n 1 || true
