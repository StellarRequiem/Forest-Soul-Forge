#!/bin/bash
# Burst 336 - operator hygiene cluster: D4 advanced kit overrides
# + operator timezone correction.
#
# Two adjacent corrections caught during the TestAuthor-D4 birth
# verification on 2026-05-16:
#
# 1. Per-role kit override (operator-approved approach)
#    First TestAuthor-D4 birth landed with only timestamp_window.v1
#    in its constitution because researcher's default_kit_pattern
#    is research-oriented (web_research / corpus_synthesis /
#    citation_management) and test_author wasn't in tool_catalog.
#    yaml's `archetypes:` block. Adds explicit archetype bundles
#    for all three D4 advanced roles so future births compose the
#    right kit deterministically.
#
# 2. Operator profile timezone correction
#    Profile carried the New_York placeholder; operator is in Las
#    Vegas → America/Los_Angeles. Reality tab surfaces this so the
#    operator caught it. Direct YAML edit + updated_at stamp; the
#    /reality-anchor/reload endpoint picks up the new seeds on
#    operator-trigger.
#
# What ships:
#
# 1. config/tool_catalog.yaml:
#    Three new archetype bundles below code_reviewer:
#      test_author: llm_think + memory_write/recall + delegate +
#        code_read + code_edit + shell_exec + web_fetch +
#        pytest_run
#      migration_pilot: llm_think + memory_write/recall + delegate
#        + code_read + code_edit + shell_exec + audit_chain_verify
#      release_gatekeeper: llm_think + memory_write/recall +
#        delegate + code_read + shell_exec + audit_chain_verify +
#        pytest_run (NOTABLY no code_edit — the gate is advisory,
#        release acts belong to operator)
#
# 2. data/operator/profile.yaml:
#    timezone America/New_York → America/Los_Angeles.
#    updated_at restamped to 2026-05-16T08:45:00Z.
#
# 3. tests/unit/test_d4_advanced_rollout.py:
#    6 new kit assertions covering the three archetype bundles +
#    the release_gatekeeper-no-code_edit invariant.
#
# Sandbox-verified 33/33 pass (was 27 — 6 new kit checks).
#
# Operator next step after this lands:
#   1. force-restart-daemon (pick up new tool_catalog archetypes
#      + reseed Reality Anchor from updated profile)
#   2. Re-run birth-test-author (idempotent: agent exists already,
#      but the constitution needs a manual rebuild OR delete the
#      existing TestAuthor-D4 + re-birth so it picks up the new
#      kit). Decision deferred to operator — keep existing
#      TestAuthor-D4 with timestamp_window-only kit until
#      explicitly re-birthed, OR delete + re-birth now.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add config/tool_catalog.yaml \
        tests/unit/test_d4_advanced_rollout.py \
        dev-tools/close-session-stale-terminals.command \
        dev-tools/commit-bursts/commit-burst336-per-role-kit-override.command
# Note: data/operator/profile.yaml is .gitignored (per-install operator
# config, not version-controlled). The timezone fix (New_York →
# Los_Angeles) was applied as a local data edit; the operator triggers
# the Reality Anchor reseed via POST /reality-anchor/reload after this
# commit lands.

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(d4): per-role kit override + operator timezone (B336)

Burst 336. Two adjacent corrections caught during TestAuthor-D4
birth verification on 2026-05-16.

1. Per-role kit override in tool_catalog.yaml archetypes:
   First birth landed only timestamp_window.v1 because the
   researcher genre's default_kit_pattern is research-oriented.
   Added explicit archetype bundles for all three D4 advanced
   roles so future births compose the right kit deterministically.
   release_gatekeeper notably gets shell_exec but NOT code_edit
   — the gate is advisory; release acts belong to the operator.

2. Operator profile timezone:
   Was America/New_York (placeholder); operator is in Las Vegas
   → America/Los_Angeles. Reality tab surfaced the wrong
   location. Direct YAML edit + updated_at restamp.

What ships:

  - config/tool_catalog.yaml: three new archetype entries
    (test_author / migration_pilot / release_gatekeeper).
  - data/operator/profile.yaml: timezone fix + updated_at bump
    (applied locally; file is .gitignored — per-install operator
    config, not version-controlled).
  - tests/unit/test_d4_advanced_rollout.py: 6 new kit assertions
    (each role has archetype, code_edit + shell_exec where
    appropriate, release_gatekeeper-no-code_edit invariant).
  - dev-tools/close-session-stale-terminals.command: extended
    pattern set (birth-* + diag-import + fix-multipart-dep +
    generate-sbom) so future sweeps catch this session's
    accumulated windows.

Sandbox-verified 33/33 pass on test_d4_advanced_rollout (was 27).

Operator next step: force-restart-daemon picks up new tool_
catalog archetypes + reseeds Reality Anchor from updated
profile. Existing TestAuthor-D4 retains its
timestamp_window-only kit until explicitly re-birthed; net-new
births get the proper kit."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 336 complete - per-role kits + tz fix shipped ==="
echo "Restart daemon to apply both changes."
echo ""
echo "Press any key to close."
read -n 1
