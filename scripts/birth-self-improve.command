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
#   ./scripts/birth-self-improve.command                       # default run
#   ./scripts/birth-self-improve.command --audit-only          # no fixes
#   ./scripts/birth-self-improve.command --no-branch --no-pytest
#   ./scripts/birth-self-improve.command --no-ollama           # mechanical fixes only
#   ./scripts/birth-self-improve.command --ollama-url http://other:11434
#   ./scripts/birth-self-improve.command --rollback            # delete latest branch
#
# The harness creates a `self-improve/YYYY-MM-DD-HHMMSS` branch and
# never merges or pushes itself — review the generated report under
# docs/self-improvement/ before merging. If anything looks wrong,
# `--rollback` deletes the most-recent self-improve/* branch.
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

# Quick Ollama reachability probe so the operator sees whether the
# intelligent-fix path will run BEFORE the audit phase. We use a
# 2-second timeout against /api/tags — same endpoint the harness
# itself uses for discovery. Operator can override via OLLAMA_URL.
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
if curl -sf --max-time 2 "${OLLAMA_URL}/api/tags" >/dev/null 2>&1; then
    OLLAMA_STATUS="reachable at ${OLLAMA_URL}"
else
    OLLAMA_STATUS="NOT reachable — mechanical fixes only"
fi

echo "==========================================="
echo "  FSF Self-Improvement Harness"
echo "  $(date '+%Y-%m-%d %I:%M:%S %p %Z')"
echo "  python: ${PY}"
echo "  ollama: ${OLLAMA_STATUS}"
echo "==========================================="
echo ""

exec "${PY}" "${REPO_ROOT}/scripts/self_improve.py" "$@"
