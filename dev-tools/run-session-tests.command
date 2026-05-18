#!/bin/bash
# Run the unit test suite, capturing pass/fail counts + the first
# N failures. This is the session-end gate — every burst from
# B248-B256 needs to land green before we keep building.

set +e
cd "$(dirname "$0")"

echo "=== run-session-tests ==="
echo "python:  $(.venv/bin/python --version 2>&1)"
echo ""

# Run only the unit suite (integration tests are slower + have
# different fixture requirements; cover them in a follow-up).
# -x stops at first failure; -q is quiet for clean output.
echo "--- running tests/unit ---"
.venv/bin/python -m pytest tests/unit/ -q --tb=short --no-header 2>&1 | tail -80
rc=${PIPESTATUS[0]}

echo ""
echo "=== summary ==="
echo "exit code: $rc"
if [ "$rc" -eq 0 ]; then
  echo "✓ all unit tests passed"
else
  echo "✗ failures present — see above"
fi
echo ""
echo "Press return to close."
read -r
