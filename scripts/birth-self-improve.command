#!/bin/bash
# Convenience wrapper for scripts/self_improve.py.
#
# Sets the env vars the harness needs (FSF_SKIP_EMAIL_TESTS=1 so the
# email integration tests don't gate the run; PYTHONPATH so the
# harness's pytest subprocess can import forest_soul_forge), picks
# the repo's .venv python if it exists, and forwards any additional
# CLI flags to self_improve.py.
#
# Usage:
#   ./scripts/birth-self-improve.command
#   ./scripts/birth-self-improve.command --audit-only
#   ./scripts/birth-self-improve.command --no-branch --no-pytest
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/.." && pwd)"
cd "${REPO_ROOT}"

export FSF_SKIP_EMAIL_TESTS=1
export PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    PY="${REPO_ROOT}/.venv/bin/python"
else
    PY="$(command -v python3)"
fi

echo "==========================================="
echo "  FSF Self-Improvement Harness"
echo "  $(date '+%Y-%m-%d %I:%M:%S %p %Z')"
echo "  python: ${PY}"
echo "==========================================="
echo ""

exec "${PY}" "${REPO_ROOT}/scripts/self_improve.py" "$@"
