#!/usr/bin/env bash
# T4 verification — runs the new tools-catalog tests and the
# regressions most likely to break from preview-response shape changes.
# Double-click from Finder.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

bar() { printf '\n=== %s ===\n' "$1"; }

bar "T4 — new TestToolsCatalog (catalog/kit endpoints + preview.resolved_tools)"
bash scripts/docker_test.sh -v tests/unit/test_daemon_readonly.py::TestToolsCatalog

bar "Regression — full read-only suite (any pydantic shape break shows here)"
bash scripts/docker_test.sh -v tests/unit/test_daemon_readonly.py

bar "Regression — preview hash parity & writes (tools_add/tools_remove path)"
bash scripts/docker_test.sh -v tests/unit/test_daemon_writes.py::TestPreviewEndpoint
bash scripts/docker_test.sh -v tests/unit/test_daemon_writes.py::TestToolKit

bar "Regression — tool catalog loader + policy (no schema drift)"
bash scripts/docker_test.sh -v tests/unit/test_tool_catalog.py
bash scripts/docker_test.sh -v tests/unit/test_tool_policy.py

echo
echo "Press return to close."
read -r _
