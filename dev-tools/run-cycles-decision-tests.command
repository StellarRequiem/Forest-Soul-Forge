#!/bin/bash
# Run tests/unit/test_cycles_decision.py end-to-end (cycle 2 close).

set -uo pipefail
cd "$(dirname "$0")/.."

echo "=========================================================="
echo "Running tests/unit/test_cycles_decision.py"
echo "=========================================================="
echo

"$(pwd)/.venv/bin/pytest" tests/unit/test_cycles_decision.py -v --tb=short \
  > dev-tools/cycle-2-pytest-output.txt 2>&1
RC=$?

# Echo to terminal too.
cat dev-tools/cycle-2-pytest-output.txt

echo
echo "=========================================================="
if [ $RC -eq 0 ]; then
  echo "GREEN — cycle 2 closes."
else
  echo "FAILED — pytest rc=$RC. See output above + cycle-2-pytest-output.txt."
fi
echo "=========================================================="
echo
echo "Output saved to dev-tools/cycle-2-pytest-output.txt"
echo
echo "Press any key to close."
read -n 1
