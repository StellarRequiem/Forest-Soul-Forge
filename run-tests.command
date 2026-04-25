#!/usr/bin/env bash
# Run the new TestGenerate class through the dockerized test harness.
# Double-click from Finder. If you want the full suite, edit the filter.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

bar() { printf '\n=== %s ===\n' "$1"; }

bar "running ADR-0017 tests (LLM-enriched soul.md narrative)"
bash scripts/docker_test.sh -v \
    tests/unit/test_daemon_writes.py::TestEnrichNarrative \
    tests/unit/test_daemon_writes.py::TestVoiceRendererUnit

bar "regression: existing daemon write tests should stay green"
bash scripts/docker_test.sh -v tests/unit/test_daemon_writes.py

bar "regression: Phase 4 first slice (POST /runtime/provider/generate)"
bash scripts/docker_test.sh -v tests/unit/test_daemon_readonly.py::TestGenerate

bar "regression: existing TestRuntime"
bash scripts/docker_test.sh -v tests/unit/test_daemon_readonly.py::TestRuntime

echo
echo "Press return to close."
read -r _
