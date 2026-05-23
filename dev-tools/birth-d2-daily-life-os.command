#!/bin/bash
# ADR-0087 — D2 Daily Life OS umbrella birth script.
#
# Births all five D2 agents in order, idempotent. Run this
# after pulling D2-A through D2-D and restarting the daemon —
# each child script restarts the daemon itself, so explicit
# restart beforehand is optional.
#
# Order matters loosely (each script is independent):
#   1. coordinator       — orchestrator; composes morning briefings
#   2. inbox_triager     — drafts replies from inbox snapshots
#   3. time_steward      — scheduler + calendar (YELLOW posture)
#   4. task_prioritizer  — deterministic ranker
#   5. reflector         — evening decision-journal sweep

set -euo pipefail
cd "$(dirname "$0")/.."

echo "=========================================================="
echo "ADR-0087 — Birth D2 Daily Life OS (5 agents)"
echo "=========================================================="
echo

echo "[1/5] Coordinator-D2 (researcher, GREEN)"
./dev-tools/birth-coordinator.command < /dev/null
echo

echo "[2/5] InboxTriager-D2 (communicator, GREEN)"
./dev-tools/birth-inbox-triager.command < /dev/null
echo

echo "[3/5] TimeSteward-D2 (actuator, YELLOW)"
./dev-tools/birth-time-steward.command < /dev/null
echo

echo "[4/5] TaskPrioritizer-D2 (researcher, GREEN)"
./dev-tools/birth-task-prioritizer.command < /dev/null
echo

echo "[5/5] Reflector-D2 (researcher, GREEN)"
./dev-tools/birth-reflector.command < /dev/null
echo

echo "=========================================================="
echo "D2 Daily Life OS — 5 agents alive."
echo "=========================================================="
echo
echo "Next steps:"
echo "  1. Run a morning briefing via Coordinator-D2's"
echo "     daily_orchestration skill (operator decides the"
echo "     window_hours)."
echo "  2. Paste an inbox snapshot into private memory tagged"
echo "     'inbox_snapshot' + dispatch InboxTriager-D2's"
echo "     inbox_triage skill for ranked + drafted replies."
echo "  3. Schedule a reminder or queue a calendar action via"
echo "     TimeSteward-D2 (YELLOW posture → approval queue)."
echo "  4. Rank an operator-supplied task list via"
echo "     TaskPrioritizer-D2's task_prioritization skill."
echo "  5. At end of day, dispatch Reflector-D2's"
echo "     daily_reflection skill for a decision-journal digest."
echo
echo "Cascade rules wired in config/handoffs.yaml:"
echo "  d1_knowledge_forge.daily_knowledge_delta -> d2.morning_briefing"
echo "    (ACTIVE — Synthesizer-D1's delta feeds tomorrow's brief)"
echo
echo "Downstream cascades (d2 -> d6/d7/d5) declared INERT"
echo "in handoffs.yaml comments until those domains ship."
echo
echo "Press any key to close this window."
read -n 1 || true
