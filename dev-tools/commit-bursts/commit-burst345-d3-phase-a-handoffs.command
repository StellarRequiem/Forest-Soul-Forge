#!/bin/bash
# Burst 345 - ADR-0078 Phase A T3: handoffs.yaml wiring +
# integration tests.
#
# Wires forensic_archive into the orchestrator's routing table so
# route_to_domain.v1 can resolve (d3_local_soc, forensic_archive)
# the moment B346's archive_evidence.v1 skill lands. Until then,
# dispatches return a clean "skill not found" from the dispatcher
# — the operator-visible signal that the wiring is ahead of the
# artifact (same pattern used for ADR-0077 D4 advanced rollout).
#
# What ships:
#
# 1. config/handoffs.yaml:
#    - (d3_local_soc, forensic_archive) → archive_evidence.v1
#    - long comment block explaining why NO new cascade rule from
#      forensic_archive. The attractive
#      d3.incident_response → d3.forensic_archive cascade is
#      deferred to Phase D (ADR-0066 SOAR playbooks) where a
#      playbook step can decide WHICH incidents need auto-archive
#      based on severity rather than blanket-cascading every
#      response and inflating the audit chain with attestations
#      operators may never consult.
#
# 2. tests/unit/test_d3_handoffs_wiring.py: 9 assertions mirroring
#    the test_d4_handoffs_wiring.py pattern. Includes the
#    deliberate-no-cascade test that documents the Phase A
#    decision in code so future bursts don't quietly add one.
#
# Test results: 9/9 D3 handoffs + 18/18 D4 handoffs + 17/17 D3
# Phase A rollout = 44/44 green, no regressions.
#
# Pre-existing wiring guarded by regression tests:
#   - (d3_local_soc, incident_summary)  → summarize_recent_incidents.v1
#   - (d3_local_soc, incident_response) → respond_to_incident.v1
#   - cascade: d3.incident_response → d8.compliance_scan (ADR-0067 T4)
#   - d3 entry_agents original seven Security Swarm entries

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add config/handoffs.yaml \
        tests/unit/test_d3_handoffs_wiring.py \
        dev-tools/commit-bursts/commit-burst345-d3-phase-a-handoffs.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(d3): Phase A T3 - handoffs.yaml wiring + integration tests (B345)

Burst 345. Wires forensic_archive into the orchestrators routing
table so route_to_domain.v1 can resolve (d3_local_soc,
forensic_archive) the moment B346s archive_evidence.v1 skill
lands. Until then dispatches return a clean skill not found from
the dispatcher - same pattern used for ADR-0077 D4 advanced
rollout.

handoffs.yaml:
  - (d3_local_soc, forensic_archive) maps to archive_evidence.v1
  - long comment block explaining why NO new cascade rule from
    forensic_archive. The attractive d3.incident_response to
    d3.forensic_archive cascade is deferred to Phase D
    (ADR-0066 SOAR playbooks) where a playbook step can decide
    WHICH incidents need auto-archive based on severity rather
    than blanket-cascading every response and inflating the
    audit chain with attestations operators may never consult.

tests/unit/test_d3_handoffs_wiring.py:
  9 assertions mirroring test_d4_handoffs_wiring.py:
    - structural integrity: forensic_archive mapping present
      with correct skill_name + skill_version
    - regression: pre-existing D3 mappings + d3 to d8 cascade
      still present
    - resolve_route happy path: forensic_archive routes to
      ForensicArchivist-D3 + archive_evidence.v1
    - resolve_route pre-birth: returns UNROUTABLE_NO_ALIVE_AGENT
      (operator-visible signal that birth hasnt run)
    - d3 manifest: original 7 Security Swarm entries still
      present + new forensic_archivist entry intact
    - cascade behavior: pre-existing d3.incident_response to
      d8.compliance_scan still fires when d8 live
    - deliberate-no-cascade: no outbound cascade from
      forensic_archive (documents the Phase A decision in code)

Test results: 9/9 D3 handoffs + 18/18 D4 handoffs + 17/17 D3
Phase A rollout = 44/44 green, no regressions.

Next: B346 archive_evidence.v1 skill."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 345 complete - D3 Phase A T3 shipped ==="
echo "Next: B346 archive_evidence.v1 skill."
echo ""
echo "Press any key to close."
read -n 1 || true
