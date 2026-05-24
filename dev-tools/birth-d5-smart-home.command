#!/bin/bash
# ADR-0091 — D5 Smart Home Brain umbrella birth script.
#
# Births all five D5 agents in order, idempotent. Run this after
# pulling D5-A through D5-D and restarting the daemon — each
# child script restarts the daemon itself, so explicit restart
# beforehand is optional.
#
# Order matters loosely (each script is independent):
#   1. home_steward       — state-of-the-home report (researcher, GREEN)
#   2. home_sentinel      — household-security alerts (guardian, GREEN)
#   3. energy_warden      — per-device energy anomalies (researcher, GREEN)
#   4. comfort_optimizer  — comfort recommendations (researcher, GREEN)
#   5. routine_composer   — routine queue writer (actuator, YELLOW)
#
# Per ADR-0091 Decision 2, D5 has NO builtin tool that touches
# Home Assistant entities directly — the forest-home-assistant
# connector (when installed) consumes routine_compose.v1's queue
# + writes home_state_snapshot memory_writes back. D5 ships
# substrate-ready without the connector present; the operator
# can dispatch all five roles using one-shot operator-supplied
# home_state attestations.

set -euo pipefail
cd "$(dirname "$0")/.."

echo "=========================================================="
echo "ADR-0091 — Birth D5 Smart Home Brain (5 agents)"
echo "=========================================================="
echo

echo "[1/5] HomeSteward-D5 (researcher, GREEN)"
./dev-tools/birth-home-steward.command < /dev/null
echo

echo "[2/5] HomeSentinel-D5 (guardian, GREEN)"
./dev-tools/birth-home-sentinel.command < /dev/null
echo

echo "[3/5] EnergyWarden-D5 (researcher, GREEN)"
./dev-tools/birth-energy-warden.command < /dev/null
echo

echo "[4/5] ComfortOptimizer-D5 (researcher, GREEN)"
./dev-tools/birth-comfort-optimizer.command < /dev/null
echo

echo "[5/5] RoutineComposer-D5 (actuator, YELLOW)"
./dev-tools/birth-routine-composer.command < /dev/null
echo

echo "=========================================================="
echo "D5 Smart Home Brain — 5 agents alive."
echo "=========================================================="
echo
echo "Pipeline (operator dispatch → audited household pass):"
echo "  1. HomeSteward-D5 reads home_state attestations + composes"
echo "     a state-of-the-home report tagged home_state_report:"
echo "     <window_slug>. Cross-domain context (D2/D3) narrated."
echo "  2. HomeSentinel-D5 reads the same attestations + composes"
echo "     alerts for unfamiliar-presence / vacation-inconsistency /"
echo "     sensor-drift / surveillance-gap. Tagged home_security_alert:"
echo "     <window_slug>. Stands ALONGSIDE the steward's report"
echo "     per ADR-0091 Decision 3 — both attestations stand."
echo "  3. EnergyWarden-D5 dispatches energy_anomaly_scan.v1 over"
echo "     per-device current draws against operator-supplied"
echo "     baselines; composes anomaly attestations. Read-only;"
echo "     never tunes."
echo "  4. ComfortOptimizer-D5 dispatches comfort_recommend.v1 over"
echo "     current home_state + operator preferences + time-of-day;"
echo "     composes recommendation attestations. Read-only; never"
echo "     actuates."
echo "  5. RoutineComposer-D5 dispatches home_state_snapshot.v1 +"
echo "     routine_compose.v1 to queue routine envelopes into"
echo "     data/d5/routine_queue.jsonl. YELLOW posture — every"
echo "     queue write is operator-approved per call;"
echo "     routine_compose.v1 carries requires_human_approval=True"
echo "     at the tool layer too. NEVER fires routines directly."
echo
echo "Umbrella skill: smart_home.v1 — single dispatch that"
echo "delegates to HomeSteward + HomeSentinel for the observation"
echo "pass. Energy + comfort + routine sub-passes are explicit"
echo "operator dispatches against their respective skills."
echo
echo "Cascade rules wired in config/handoffs.yaml:"
echo "  d2_daily_life_os.morning_briefing -> d5.home_orchestration"
echo "    (ACTIVE — D2 morning briefing seeds the state-of-the-home pass)"
echo "  d2_daily_life_os.task_prioritization -> d5.routine_management"
echo "    (ACTIVE — D2 high-priority tasks compose routine envelopes;"
echo "     YELLOW posture still requires operator approval per queue write)"
echo "  d5.home_security -> d3_local_soc.incident_response"
echo "    (ACTIVE — D5 security alerts route to SOC incident correlator)"
echo "  d5.routine_management -> d2_daily_life_os.reminder"
echo "    (ACTIVE — queued routines fire D2 schedule_reminder at"
echo "     scheduled_for time as the pre-connector fallback)"
echo
echo "Downstream cascades declared INERT in handoffs.yaml comments:"
echo "  d5.energy_optimization -> d6.power_bill_anomaly (D6 not shipped)"
echo "  d5.routine_management -> d1.routines_index (capability TBD)"
echo
echo "Press any key to close this window."
read -n 1 || true
