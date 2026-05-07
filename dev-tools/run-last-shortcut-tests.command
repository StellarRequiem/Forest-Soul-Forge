#!/bin/bash
# Run the test_last_shortcut_route.py file end-to-end (cycle 1 close).
# Output stays in this Terminal window so the assistant can read it.

set -uo pipefail
cd "$(dirname "$0")/.."

echo "=========================================================="
echo "Running tests/unit/test_last_shortcut_route.py"
echo "=========================================================="
echo

"$(pwd)/.venv/bin/pytest" tests/unit/test_last_shortcut_route.py -v --tb=short
RC=$?

echo
echo "=========================================================="
if [ $RC -eq 0 ]; then
  echo "GREEN — cycle 1 closes."
else
  echo "FAILED — pytest rc=$RC. Tail above for stack."
fi
echo "=========================================================="

# Persist output for the assistant.
"$(pwd)/.venv/bin/pytest" tests/unit/test_last_shortcut_route.py -v --tb=short \
  > dev-tools/cycle-1-pytest-output.txt 2>&1

echo
echo "Output also saved to dev-tools/cycle-1-pytest-output.txt"
echo
echo "Press any key to close."
read -n 1
