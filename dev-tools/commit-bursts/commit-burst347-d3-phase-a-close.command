#!/bin/bash
# Burst 347 - ADR-0078 Phase A T5+T6: umbrella birth + runbook.
# CLOSES D3 PHASE A.
#
# Phase A is light (one new agent — forensic_archivist — no new
# infrastructure ADR), so the umbrella is thinner than D4's
# (which birthed three) but follows the same pattern so Phases
# B/C/D can extend it without rewriting the shape.
#
# What ships:
#
# 1. dev-tools/birth-d3-phase-a.command (NEW, +x):
#    Umbrella that calls birth-forensic-archivist.command with
#    stdin redirected to /dev/null + EOF-tolerant trailing read.
#    Idempotent. Includes operator-readable preamble showing the
#    full four-phase plan so the operator knows where they are
#    in the rollout (Phase A done; B/C/D gated on ADR-0064/0065/
#    0066 respectively).
#
# 2. docs/runbooks/d3-phase-a-rollout.md (NEW):
#    Operator runbook mirroring docs/runbooks/d4-advanced-rollout.md.
#    Sections:
#      - At a glance (role/genre/posture/skill table + why
#        forensic_archivist distinct from vault_warden)
#      - One-time setup (daemon restart, birth, skill install)
#      - Dispatch flow with three concrete examples (acquire,
#        handoff, retire) plus the operator-readable five-step
#        pipeline walkthrough
#      - Observation (agent identity, audit chain, custody log
#        access, approval-queue note about why GREEN posture is
#        the right choice)
#      - Recovery — common failure modes (unknown role, keychain
#        colon, skill-not-found, all five HALT codes with
#        recovery procedures, constitution patch skipped)
#      - Out of scope (auto-archive cascade deferred to Phase D,
#        operator-driven move tooling, retire-then-cleanup
#        enforcement, multi-host chain)
#      - Reference (cross-links to all relevant ADRs, skills,
#        tests, and the D4 runbook this one mirrors)
#
# Phase A summary at close:
#   B342 — ADR-0078 decision doc
#   B343 — trait_tree + genres + constitutions + tool_catalog
#          + d3 manifest + tests (17/17)
#   B344 — birth-forensic-archivist.command
#   B345 — handoffs.yaml wiring + tests (9/9)
#   B346 — archive_evidence.v1 skill + tests (11/11)
#   B347 — THIS BURST: umbrella + runbook (CLOSE Phase A)
#
# Total D3 Phase A = 6 bursts. Hardened substrate from B335/B336/
# B341 made Phase A noticeably cheaper than D4's 10-burst arc
# (ADR-0078 §Decision 6 predicted 5-6 bursts per phase; came in
# at 6 — within the band).
#
# Next session: ADR-0064 (telemetry pipeline) — ~5-6 bursts —
# then Phase B (telemetry_steward + threat_intel_curator).

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/birth-d3-phase-a.command \
        docs/runbooks/d3-phase-a-rollout.md \
        dev-tools/commit-bursts/commit-burst347-d3-phase-a-close.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(d3): Phase A T5+T6 - umbrella + runbook (CLOSE Phase A, B347)

Burst 347. Closes D3 Local SOC Phase A under ADR-0078. Phase A
ran in 6 bursts (B342-B347), within the 5-6 burst-per-phase
band ADR-0078 Decision 6 predicted thanks to the hardened
substrate from B335/B336/B341.

dev-tools/birth-d3-phase-a.command (NEW, +x):
  Umbrella that calls birth-forensic-archivist.command with
  stdin redirected to /dev/null + EOF-tolerant trailing read.
  Phase A is light (one new agent) so the umbrella is thinner
  than birth-d4-advanced.command but follows the same pattern
  so Phases B/C/D can extend it. Includes operator-readable
  preamble showing the full four-phase plan so the operator
  knows where they are in the rollout.

docs/runbooks/d3-phase-a-rollout.md (NEW):
  Operator runbook mirroring d4-advanced-rollout.md. Covers:
    - At a glance (role + posture + skill + why forensic_archivist
      is distinct from vault_warden)
    - One-time setup (daemon restart, birth, skill install)
    - Dispatch flow with concrete examples for all three
      transition_type values (acquire / handoff / retire)
    - Observation (agent identity, audit chain, custody log
      access)
    - Recovery for all five HALT codes plus the common birth
      failure modes
    - Out of scope (auto-archive cascade deferred to Phase D,
      operator-driven move tooling, retire-then-cleanup
      enforcement, multi-host chain)
    - Reference cross-links

Phase A summary:
  B342 - ADR-0078 decision doc
  B343 - trait_tree + genres + constitutions + tool_catalog
         + d3 manifest + tests (17/17)
  B344 - birth-forensic-archivist.command
  B345 - handoffs.yaml wiring + tests (9/9)
  B346 - archive_evidence.v1 skill + tests (11/11)
  B347 - umbrella + runbook (CLOSE)

Next session: ADR-0064 (telemetry pipeline) - ~5-6 bursts -
then Phase B (telemetry_steward + threat_intel_curator)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 347 complete - D3 Phase A CLOSED ==="
echo "=========================================================="
echo "Phase A = 6 bursts (B342-B347)."
echo "Next session: ADR-0064 (telemetry pipeline) -> Phase B."
echo ""
echo "Press any key to close."
read -n 1 || true
