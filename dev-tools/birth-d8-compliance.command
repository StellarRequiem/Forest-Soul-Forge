#!/bin/bash
# ADR-0085 — D8 Compliance Auditor umbrella birth script.
#
# Births all five D8 agents in order, idempotent. Run this after
# pulling D8-A through D8-D and restarting the daemon — each
# child script restarts the daemon itself, so explicit restart
# beforehand is optional.
#
# Order matters loosely (each script is independent), but
# birthing audit_archivist + evidence_collector first means
# scan / lint / report sweeps started afterward will have a
# corpus to attest against.

set -euo pipefail
cd "$(dirname "$0")/.."

echo "=========================================================="
echo "ADR-0085 — Birth D8 Compliance Auditor (5 agents)"
echo "=========================================================="
echo

echo "[1/5] AuditArchivist-D8"
./dev-tools/birth-audit-archivist.command < /dev/null
echo

echo "[2/5] EvidenceCollector-D8"
./dev-tools/birth-evidence-collector.command < /dev/null
echo

echo "[3/5] ComplianceScanner-D8"
./dev-tools/birth-compliance-scanner.command < /dev/null
echo

echo "[4/5] PolicyEnforcer-D8"
./dev-tools/birth-policy-enforcer.command < /dev/null
echo

echo "[5/5] ReportGenerator-D8"
./dev-tools/birth-report-generator.command < /dev/null
echo

echo "=========================================================="
echo "D8 Compliance Auditor — 5 agents alive."
echo "=========================================================="
echo
echo "Next steps:"
echo "  1. Run an initial compliance_scan against the SOC2 seed"
echo "     framework to baseline (ComplianceScanner-D8)."
echo "  2. Capture initial evidence per CC6.1 / CC7.2 / CC8.1 /"
echo "     A1.2 / C1.1 (EvidenceCollector-D8)."
echo "  3. Generate the first audit packet (ReportGenerator-D8)."
echo "  4. Review any policy_enforcement proposals via the"
echo "     approval queue (PolicyEnforcer-D8 starts YELLOW)."
echo "  5. Attest long-term archival of the first packet"
echo "     (AuditArchivist-D8)."
echo
echo "Cascade rules already wired in config/handoffs.yaml:"
echo "  d4_code_review.review_signoff -> d8.compliance_scan"
echo "  d3_local_soc.incident_response -> d8.compliance_scan"
echo
echo "Press any key to close this window."
read -n 1 || true
