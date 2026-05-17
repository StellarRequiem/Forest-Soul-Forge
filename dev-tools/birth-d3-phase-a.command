#!/bin/bash
# ADR-0078 Phase A T5 — D3 Local SOC Phase A umbrella birth script.
#
# Phase A is light: one new agent (forensic_archivist), no new
# infrastructure ADRs needed before this phase. The umbrella is
# thinner than birth-d4-advanced.command — a single birth, then
# verification + summary — but it follows the same pattern so
# Phases B/C/D can extend it without rewriting the shape.
#
# Each individual birth script is itself idempotent — re-runs
# skip the birth POST when the agent already exists. So is the
# umbrella.
#
# Operator NOT-required between scripts unless a birth fails.
# This is operator-driven (run from Finder); it does NOT
# auto-restart the daemon (use force-restart-daemon.command for
# that — role/genre/template changes from B343 require a restart
# to take effect on net-new births).

set -uo pipefail
cd "$(dirname "$0")"

echo "=========================================================="
echo "ADR-0078 Phase A — D3 Local SOC Advanced Rollout"
echo "  (umbrella birth — Phase A is forensic_archivist only)"
echo "=========================================================="
echo
echo "This will birth 1 agent:"
echo "  1. ForensicArchivist-D3  (guardian   / green posture)"
echo
echo "Phase A is the no-new-infra tranche. Phases B-D each pull"
echo "in their own ADR before adding more agents:"
echo "  Phase B (after ADR-0064 telemetry pipeline): telemetry_steward"
echo "                                              + threat_intel_curator"
echo "  Phase C (after ADR-0065 detection-as-code): detection_engineer"
echo "  Phase D (after ADR-0066 SOAR playbooks):    playbook_pilot"
echo "                                              + purple_pete"
echo
echo "Press Ctrl-C now to abort, or wait 3s..."
sleep 3

echo
echo "=========================================================="
echo "[1/1] Birthing ForensicArchivist-D3"
echo "=========================================================="
bash ./birth-forensic-archivist.command < /dev/null
RC1=$?
if [ "$RC1" -ne 0 ]; then
  echo
  echo "ERROR: birth-forensic-archivist exited rc=$RC1. Stopping."
  echo "Fix the cause + re-run. The script is idempotent so a"
  echo "successful retry will skip the birth and just patch the"
  echo "constitution + set posture."
  echo
  echo "Press any key to close."
  read -n 1 || true
  exit "$RC1"
fi

echo
echo "=========================================================="
echo "D3 Phase A advanced rollout COMPLETE — one agent alive."
echo "=========================================================="
echo
echo "Next steps:"
echo "  * Verify in /agents endpoint or the frontend Agents tab"
echo "    that ForensicArchivist-D3 is alive + green-posture."
echo "  * Install the skill (examples/skills/archive_evidence.v1.yaml)"
echo "    into data/forge/skills/installed/ via the Skill Forge"
echo "    UI or operator copy + /skills/reload."
echo "  * First real dispatch: route a forensic_archive subintent"
echo "    via the orchestrator — operator says something like"
echo "    'archive evidence for incident INC-2026-001'"
echo "    decompose_intent.v1 -> route_to_domain.v1 ->"
echo "    delegate.v1(ForensicArchivist-D3, archive_evidence.v1)."
echo "  * data/forensics/ root was created at birth time. Populate"
echo "    per-incident subtrees as operator-driven."
echo
echo "When ready to proceed to Phase B:"
echo "  * ADR-0064 (telemetry pipeline) ships across ~5-6 bursts."
echo "  * After ADR-0064 lands, Phase B umbrella births"
echo "    telemetry_steward + threat_intel_curator."
echo
echo "Press any key to close."
read -n 1 || true  # EOF-tolerant for non-interactive callers
