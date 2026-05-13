#!/bin/bash
# Targeted diagnostic for B264 (ADR-0051 T4+T5+T6+T7 dispatcher
# integration). Runs the dispatcher + sandbox test files together
# so a regression in the dispatcher hot path surfaces immediately
# alongside the new sandbox-integration coverage.

set +e
cd "$(dirname "$0")"

echo "=== diag-b264-tests (ADR-0051 T4+T5+T6+T7 dispatcher verification) ==="
echo ""

.venv/bin/python -m pytest \
    tests/unit/test_tool_dispatcher.py \
    tests/unit/test_tool_sandbox.py \
    tests/unit/test_tool_catalog.py \
    -v --tb=short --no-header 2>&1 | tail -200
rc=${PIPESTATUS[0]}

echo ""
echo "=== summary ==="
echo "exit code: $rc"
echo ""
echo "Press return to close."
read -r
