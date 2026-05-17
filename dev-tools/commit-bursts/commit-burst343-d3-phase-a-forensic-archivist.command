#!/bin/bash
# Burst 343 - ADR-0078 Phase A T2: forensic_archivist role +
# manifest + tests.
#
# First role-bringup burst for D3 Local SOC advanced rollout.
# Phase A is the "no new infra" tranche — forensic_archivist's
# kit reuses tools that already exist in tool_catalog.yaml
# (file_integrity, audit_chain_verify, code_read, memory_*).
# No infrastructure ADR needed before this lands.
#
# What ships:
#
# 1. config/trait_tree.yaml: forensic_archivist entry. Six
#    domain_weights. Audit emphasis (2.5) — chain-of-custody is
#    fundamentally an audit-discipline role. Security 2.0 (lower
#    than vault_warden's 2.5 because the archivist PRESERVES;
#    vault_warden DISPOSES). Communication 1.4 (custody chain
#    is operator-readable). Embodiment 0.5 (read-only by design).
#
# 2. config/genres.yaml: forensic_archivist claimed by guardian.
#    Long inline comment explaining WHY guardian (not actuator
#    like B341 moved migration_pilot + release_gatekeeper) — the
#    archivist's kit is genuinely read_only, no shell_exec, no
#    code_edit. The apply path (acquire/archive/retire) is
#    operator-driven; the archivist verifies + attests, the
#    operator moves bytes.
#
# 3. config/constitution_templates.yaml: forensic_archivist
#    role_base entry. Four load-bearing policies:
#      - forbid_artifact_mutation (the bytes are frozen on entry)
#      - require_chain_of_custody_log (every transition attested)
#      - forbid_silent_archive (no untracked moves)
#      - require_integrity_hash_verification (re-verify on read/handoff)
#    min_confidence_to_act = 0.75 — verification-class threshold
#    between researcher (0.55) and release_gatekeeper (0.80).
#
# 4. config/tool_catalog.yaml: forensic_archivist archetype kit.
#    Seven tools, all read_only: llm_think, memory_write,
#    memory_recall, delegate, audit_chain_verify, file_integrity,
#    code_read. Explicit override required because guardian's
#    default_kit_pattern is content_review-oriented (no
#    file_integrity).
#
# 5. config/domains/d3_local_soc.yaml: forensic_archive capability
#    + (role=forensic_archivist, capability=forensic_archive) entry.
#    Distinct from vault_warden's forensic_cleanup capability —
#    cleanup DISPOSES (vault_warden's job), archive PRESERVES
#    (the new agent's job).
#
# 6. tests/unit/test_d3_phase_a_rollout.py: 17 assertions across
#    trait_tree / genres / constitution_templates / tool_catalog /
#    d3 manifest. Mirrors the test_d4_advanced_rollout.py pattern.
#    Includes a kit-tier ceiling check that runs the same logic
#    the genre engine runs at birth — catches drift before a
#    birth attempt fails on the host.
#
# Tranches in D3 Phase A:
#   T1 = ADR-0078 (B342, shipped)
#   T2 = THIS BURST (B343)
#   T2b = birth-forensic-archivist.command (B344)
#   T3 = handoffs.yaml wiring + integration tests (B345)
#   T4 = forensic_archive skill (B346)
#   T5+T6 = umbrella + runbook (B347), closes Phase A

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add config/trait_tree.yaml \
        config/genres.yaml \
        config/constitution_templates.yaml \
        config/tool_catalog.yaml \
        config/domains/d3_local_soc.yaml \
        tests/unit/test_d3_phase_a_rollout.py \
        dev-tools/commit-bursts/commit-burst343-d3-phase-a-forensic-archivist.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(d3): Phase A T2 - forensic_archivist role + manifest + tests (B343)

Burst 343. First role-bringup for D3 Local SOC advanced rollout
under ADR-0078. Phase A is no-new-infra; forensic_archivist's kit
reuses tools that already exist in the catalog.

trait_tree.yaml:
  forensic_archivist with audit 2.5 + security 2.0 + cognitive 1.6
  + communication 1.4 + emotional 0.4 + embodiment 0.5. Audit is
  the primary discipline; security is lower than vault_warden's
  2.5 because vault_warden DISPOSES while the archivist PRESERVES.

genres.yaml:
  Claimed by guardian. Inline comment explains why this STAYS in
  guardian unlike migration_pilot + release_gatekeeper which B341
  had to move to actuator: the archivist's kit is genuinely
  read_only. No shell_exec, no code_edit. The apply path is
  operator-driven.

constitution_templates.yaml:
  Four load-bearing policies:
    - forbid_artifact_mutation
    - require_chain_of_custody_log
    - forbid_silent_archive
    - require_integrity_hash_verification
  min_confidence_to_act = 0.75 (verification-class threshold).

tool_catalog.yaml:
  Archetype kit: llm_think + memory_write + memory_recall +
  delegate + audit_chain_verify + file_integrity + code_read.
  Every tool is side_effects: read_only. Stays within guardian
  ceiling.

d3_local_soc.yaml:
  forensic_archive capability added to capabilities list;
  (role=forensic_archivist, capability=forensic_archive) added
  to entry_agents. Distinct from vault_warden's forensic_cleanup.

tests/unit/test_d3_phase_a_rollout.py:
  17 assertions mirroring the test_d4_advanced_rollout.py pattern.
  Includes a kit-tier ceiling check that runs the same logic the
  genre engine runs at birth.

All 17 D3 Phase A tests pass; D4 advanced rollout tests still pass
(33/33). No regressions."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 343 complete - D3 Phase A T2 shipped ==="
echo "Next: B344 birth-forensic-archivist.command."
echo ""
echo "Press any key to close."
read -n 1 || true
