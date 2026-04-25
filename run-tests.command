#!/usr/bin/env bash
# Run the new TestGenerate class through the dockerized test harness.
# Double-click from Finder. If you want the full suite, edit the filter.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

bar() { printf '\n=== %s ===\n' "$1"; }

bar "running TestGenerate (Phase 4 first slice — POST /runtime/provider/generate)"
bash scripts/docker_test.sh -v tests/unit/test_daemon_readonly.py::TestGenerate

bar "running existing TestRuntime to confirm no regressions"
bash scripts/docker_test.sh -v tests/unit/test_daemon_readonly.py::TestRuntime

echo
echo "Press return to close."
read -r _
