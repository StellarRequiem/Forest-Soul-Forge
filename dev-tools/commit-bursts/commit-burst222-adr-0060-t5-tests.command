#!/bin/bash
# Burst 222 — ADR-0060 T5 comprehensive unit tests.
#
# Locks in B219-B221 with proper pytest coverage. Until this burst
# the runtime-grant arc was covered only by in-process smoke
# scripts that exercised end-to-end flows but didn't ship as
# repeatable regression checks. T5 ships two new test files:
#
# 1. tests/unit/test_daemon_catalog_grants.py (15 tests)
#    Endpoint contracts mirroring the test_daemon_plugin_grants
#    pattern:
#      TestGrantCatalogTool: happy path, default trust_tier,
#        404 on unknown agent, 400 on unknown tool (ADR-0060 D5),
#        invalid trust_tier, re-issue overwrites, 403 when writes
#        disabled.
#      TestRevokeCatalogTool: happy path, idempotent when already
#        revoked, idempotent when no grant existed (per D3),
#        404 on unknown agent.
#      TestListCatalogGrants: empty list, active-filters-revoked
#        default, ?history=true includes revoked, 404 on unknown
#        agent.
#
# 2. tests/unit/test_posture_catalog_grant_matrix.py (13 tests)
#    Exhaustive coverage of the ADR-0060 D4 9-cell matrix plus
#    edge cases:
#      TestGreenAgent: all 3 grant tiers → GO
#      TestYellowAgent: green/yellow → GO, red → PENDING with
#        gate_source=posture_yellow_grant_red
#      TestRedAgent: green/yellow → PENDING with
#        gate_source=posture_red_grant_lower; red → REFUSE with
#        reason=agent_posture_red_grant_red
#      TestNonGrantedDispatchUnaffected: ensures legacy posture
#        branching still fires when granted_trust_tier is None
#      TestReadOnlyAlwaysBypassesPosture: confirms read_only
#        short-circuits even in the doubly-defended red+red case
#
# 28 new tests, all PASS. 168 tests in the broader regression sweep
# (catalog_grants + plugin_grants + posture_per_grant + posture_gate
# + tool_dispatch + audit_chain + registry) — no regressions.
#
# What we deliberately did NOT do:
#   - Integration tests against a real daemon (run via .command
#     live-test scripts). The in-process TestClient covers the
#     same contract surface and runs in CI.
#   - Frontend grant-pane tests. T6 frontend is queued; its tests
#     ship with it.
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: pure test additions; no source code modified.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add tests/unit/test_daemon_catalog_grants.py \
        tests/unit/test_posture_catalog_grant_matrix.py \
        dev-tools/commit-bursts/commit-burst222-adr-0060-t5-tests.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "test(adr-0060): T5 comprehensive coverage for runtime grants (B222)

Burst 222. Locks in B219-B221 with pytest coverage:

test_daemon_catalog_grants.py (15 tests)
  POST/DELETE/GET endpoint contracts mirroring the plugin_grants
  pattern. Covers happy paths, default trust_tier, 404 on unknown
  agent, 400 on unknown tool (D5), invalid tier rejection,
  re-issue overwrites, idempotent revoke (D3), and 403 when
  writes disabled.

test_posture_catalog_grant_matrix.py (13 tests)
  All 9 cells of the ADR-0060 D4 matrix verified, plus:
    - Non-granted dispatch unaffected (legacy logic still fires)
    - Read-only side_effects bypass posture even in the
      doubly-defended red+red case

28 new tests, all pass. 168 in the broader sweep — no regressions.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: pure test additions."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 222 complete ==="
echo "=== ADR-0060 T5 coverage locked in. T1-T5 ready; T6 frontend queued. ==="
echo "Press any key to close."
read -n 1
