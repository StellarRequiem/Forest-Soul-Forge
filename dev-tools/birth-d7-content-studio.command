#!/bin/bash
# ADR-0088 — D7 Content Studio umbrella birth script.
#
# Births all five D7 agents in order, idempotent. Run this
# after pulling D7-A through D7-D and restarting the daemon —
# each child script restarts the daemon itself, so explicit
# restart beforehand is optional.
#
# Order matters loosely (each script is independent):
#   1. writer              — long-form drafting (researcher, GREEN)
#   2. content_researcher  — source-pull + brief composition (researcher, GREEN)
#   3. style_steward       — voice arbitration (guardian, GREEN)
#   4. editor              — editing + format adaptation (guardian, GREEN)
#   5. distribution_pilot  — publish queue (actuator, YELLOW)

set -euo pipefail
cd "$(dirname "$0")/.."

echo "=========================================================="
echo "ADR-0088 — Birth D7 Content Studio (5 agents)"
echo "=========================================================="
echo

echo "[1/5] Writer-D7 (researcher, GREEN)"
./dev-tools/birth-writer.command < /dev/null
echo

echo "[2/5] ContentResearcher-D7 (researcher, GREEN)"
./dev-tools/birth-content-researcher.command < /dev/null
echo

echo "[3/5] StyleSteward-D7 (guardian, GREEN)"
./dev-tools/birth-style-steward.command < /dev/null
echo

echo "[4/5] Editor-D7 (guardian, GREEN)"
./dev-tools/birth-editor.command < /dev/null
echo

echo "[5/5] DistributionPilot-D7 (actuator, YELLOW)"
./dev-tools/birth-distribution-pilot.command < /dev/null
echo

echo "=========================================================="
echo "D7 Content Studio — 5 agents alive."
echo "=========================================================="
echo
echo "Pipeline (idea → publish-ready):"
echo "  1. ContentResearcher-D7 pulls source material via"
echo "     content_research skill → brief lands in private memory."
echo "  2. Writer-D7 composes the draft via draft_writing skill,"
echo "     drawing on the brief + operator voice samples."
echo "  3. StyleSteward-D7 builds the voice profile (one-time"
echo "     setup via voice_profile_build) + scores drafts via"
echo "     voice_matching skill — flags drift, never rewrites."
echo "  4. Editor-D7 composes verify_claim + voice_match + format_adapt"
echo "     into editing + format_adaptation skills — produces verdict +"
echo "     format-adapted artifacts (twitter / linkedin / newsletter /"
echo "     blog) alongside the immutable source draft."
echo "  5. DistributionPilot-D7 (YELLOW) queues the publish via"
echo "     scheduled_publishing skill — every queue is operator-"
echo "     approved before the future forest-publish connector"
echo "     picks the queue record up at fire_at."
echo
echo "Cascade rules wired in config/handoffs.yaml:"
echo "  d1_knowledge_forge.knowledge_curation -> d7.content_drafting"
echo "    (ACTIVE — Librarian-D1's catalog seeds D7 research briefs)"
echo "  d2_daily_life_os.daily_reflection -> d7.content_seed"
echo "    (ACTIVE — Reflector-D2's evening sweep surfaces blog seeds)"
echo
echo "Downstream cascades declared INERT in handoffs.yaml comments:"
echo "  d4.release_signoff -> d7.release_notes_draft (not yet wired)"
echo "  d7.* -> d9.* (D9 Learning Coach not yet shipped)"
echo
echo "Press any key to close this window."
read -n 1 || true
