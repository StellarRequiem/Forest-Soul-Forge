#!/bin/bash
# Burst 342 - ADR-0078 D3 Local SOC advanced rollout doc.
#
# Second domain rollout post-Phase α. D3 is materially larger
# than D4: 6 new agents + 3 queued infrastructure ADRs (0064
# telemetry pipeline, 0065 detection-as-code, 0066 SOAR
# playbooks). Estimated ~30-37 bursts total across the full
# arc.
#
# Doc-only burst — no code, no tests. Subsequent bursts work
# through Phase A (forensic_archivist, no new infra) then the
# infrastructure ADRs in dependency order before Phases B/C/D.
#
# What ships:
#
# 1. docs/decisions/ADR-0078-d3-local-soc-advanced-rollout.md (NEW):
#    Status: Proposed. Six decisions:
#      D1 — Six new roles across observer/researcher/actuator/
#           guardian genres with appropriate side-effects ceilings
#      D2 — Three queued infrastructure ADRs (0064/0065/0066)
#           must land before respective phases
#      D3 — Phased delivery: A (no infra) → B (after 0064) →
#           C (after 0065) → D (after 0066)
#      D4 — Cascade rules (one already wired d3→d8; two new
#           pending Phase D)
#      D5 — Per-role posture defaults with rationale
#      D6 — Use D4's 10-burst template, expecting ~7-9 bursts
#           per phase thanks to hardened substrate from D4's
#           B335/B336/B341 fixes
#
# Tranches: T1 (this doc), Phase A (5-6 bursts), ADR-0064 +
# Phase B (10-12 bursts), ADR-0065 + Phase C (7-9 bursts),
# ADR-0066 + Phase D (7-9 bursts). Total ~30-37 bursts, spans
# multiple sessions.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0078-d3-local-soc-advanced-rollout.md \
        dev-tools/birth-d4-advanced.command \
        dev-tools/commit-bursts/commit-burst342-adr0078-d3-rollout-doc.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(d3): ADR-0078 - D3 Local SOC advanced rollout (B342)

Burst 342. Second domain rollout post-Phase α. Materially
larger than D4: 6 new agents + 3 queued infrastructure ADRs
(0064/0065/0066). Estimated ~30-37 bursts across the full arc.

Status: Proposed.

Decisions:
  D1 — Six new roles: telemetry_steward (observer),
       detection_engineer (researcher), playbook_pilot
       (actuator), threat_intel_curator (researcher),
       forensic_archivist (guardian), purple_pete (actuator).
  D2 — Three infrastructure ADRs (0064 telemetry pipeline,
       0065 detection-as-code, 0066 SOAR playbooks) must
       land before their respective phases.
  D3 — Phased delivery: A (no new infra) → B (after 0064) →
       C (after 0065) → D (after 0066).
  D4 — Cascade rules: d3→d8 pre-wired from ADR-0067 T4;
       d3→d2 and d3→d4 pending Phase D.
  D5 — Per-role posture defaults with rationale.
  D6 — Use D4's 10-burst template; ~7-9 bursts per phase
       thanks to B335/B336/B341 substrate hardening.

Also includes housekeeping: birth-d4-advanced.command's
trailing 'read -n 1' now uses '|| true' for EOF tolerance,
matching the pattern landed for the individual birth scripts
in B341. Closes the umbrella's own false-alarm rc=1 when
invoked from finish-d4-rollout."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 342 complete - ADR-0078 D3 rollout doc shipped ==="
echo "Next session: Phase A (forensic_archivist, no new infra)."
echo ""
echo "Press any key to close."
read -n 1 || true
