#!/bin/bash
# ADR-0089 — D9 Learning Coach umbrella birth script.
#
# Births all five D9 agents in order, idempotent. Run this
# after pulling D9-A through D9-D and restarting the daemon —
# each child script restarts the daemon itself, so explicit
# restart beforehand is optional.
#
# Order matters loosely (each script is independent):
#   1. mentor                    — coaching narrative (researcher, GREEN)
#   2. curriculum_designer       — DAG composition (researcher, GREEN)
#   3. assessor                  — quiz + scoring + Reality Anchor (guardian, YELLOW)
#   4. socratic_partner          — dialogue sessions (communicator, GREEN)
#   5. spaced_repetition_pilot   — SM-2 review queue (actuator, YELLOW)

set -euo pipefail
cd "$(dirname "$0")/.."

echo "=========================================================="
echo "ADR-0089 — Birth D9 Learning Coach (5 agents)"
echo "=========================================================="
echo

echo "[1/5] Mentor-D9 (researcher, GREEN)"
./dev-tools/birth-mentor.command < /dev/null
echo

echo "[2/5] CurriculumDesigner-D9 (researcher, GREEN)"
./dev-tools/birth-curriculum-designer.command < /dev/null
echo

echo "[3/5] Assessor-D9 (guardian, YELLOW)"
./dev-tools/birth-assessor.command < /dev/null
echo

echo "[4/5] SocraticPartner-D9 (communicator, GREEN)"
./dev-tools/birth-socratic-partner.command < /dev/null
echo

echo "[5/5] SpacedRepetitionPilot-D9 (actuator, YELLOW)"
./dev-tools/birth-spaced-repetition-pilot.command < /dev/null
echo

echo "=========================================================="
echo "D9 Learning Coach — 5 agents alive."
echo "=========================================================="
echo
echo "Pipeline (goal → mastery):"
echo "  1. CurriculumDesigner-D9 composes a deterministic topic-"
echo "     prereq DAG via curriculum_design skill from operator"
echo "     goal + D1 catalog reads + operator expertise."
echo "  2. Mentor-D9 composes coaching briefs via coaching skill,"
echo "     drawing on the curriculum + assessment history. Encourages"
echo "     + frames; NEVER gates progression."
echo "  3. SocraticPartner-D9 runs multi-turn dialogue sessions via"
echo "     socratic_dialogue skill — questions that surface gaps;"
echo "     NEVER grades."
echo "  4. Assessor-D9 (YELLOW) composes quiz items + deterministic"
echo "     scoring + Reality Anchor verify_claim via knowledge_assessment"
echo "     skill. Produces misconception PROPOSALS via memory_write;"
echo "     operator dispatches misconception_log.v1 directly to commit"
echo "     (knowledge_verifier separation pattern)."
echo "  5. SpacedRepetitionPilot-D9 (YELLOW) computes SM-2 next-interval"
echo "     + queues the review via spaced_repetition skill. Composes"
echo "     with D2's schedule_reminder.v1 for fire-time delivery."
echo
echo "Cascade rules wired in config/handoffs.yaml:"
echo "  d1_knowledge_forge.knowledge_contradiction_flag -> d9.curriculum_design"
echo "    (ACTIVE — D1 contradictions surface curriculum gaps for D9)"
echo "  d7_content_studio.editing -> d9.curriculum_module"
echo "    (ACTIVE — D7 polished drafts seed D9 curriculum modules)"
echo "  d9.spaced_repetition -> d2.reminder"
echo "    (ACTIVE — D9 review queue composes with D2 schedule_reminder)"
echo "  d9.curriculum_design -> d2.task_prioritization"
echo "    (ACTIVE — curriculum study blocks land in D2 task ranking)"
echo
echo "Downstream cascades declared INERT in handoffs.yaml comments:"
echo "  d9.* -> d10.deep_research (D10 Research Lab not yet shipped)"
echo "  d9.assessment_feedback -> d1.knowledge_curation (adjacent scope)"
echo "  d9.skill_certification -> d7.public_draft (adjacent scope)"
echo
echo "Press any key to close this window."
read -n 1 || true
