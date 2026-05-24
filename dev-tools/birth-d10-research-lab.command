#!/bin/bash
# ADR-0090 — D10 Multi-Agent Research Lab umbrella birth script.
#
# Births all five D10 agents in order, idempotent. Run this after
# pulling D10-A through D10-D and restarting the daemon — each
# child script restarts the daemon itself, so explicit restart
# beforehand is optional.
#
# Order matters loosely (each script is independent):
#   1. gatherer            — source bundles (researcher, GREEN)
#   2. analyst             — per-claim decomposition (researcher, GREEN)
#   3. critic              — counter-arguments (guardian, GREEN)
#   4. lab_synthesizer     — citation graph + confidence + synthesis (researcher, GREEN)
#   5. debate_moderator    — deterministic turn-ordering (researcher, GREEN)
#
# Per ADR-0090 Decision 4, D10 does NOT birth its own experimenter
# — the hypothesis_testing.v1 skill delegates to the existing
# Experimenter-Smith (ADR-0056).

set -euo pipefail
cd "$(dirname "$0")/.."

echo "=========================================================="
echo "ADR-0090 — Birth D10 Multi-Agent Research Lab (5 agents)"
echo "=========================================================="
echo

echo "[1/5] Gatherer-D10 (researcher, GREEN)"
./dev-tools/birth-gatherer.command < /dev/null
echo

echo "[2/5] Analyst-D10 (researcher, GREEN)"
./dev-tools/birth-analyst.command < /dev/null
echo

echo "[3/5] Critic-D10 (guardian, GREEN)"
./dev-tools/birth-critic.command < /dev/null
echo

echo "[4/5] Lab_Synthesizer-D10 (researcher, GREEN)"
./dev-tools/birth-lab-synthesizer.command < /dev/null
echo

echo "[5/5] Debate_Moderator-D10 (researcher, GREEN)"
./dev-tools/birth-debate-moderator.command < /dev/null
echo

echo "=========================================================="
echo "D10 Multi-Agent Research Lab — 5 agents alive."
echo "=========================================================="
echo
echo "Pipeline (topic → audited synthesis):"
echo "  1. Gatherer-D10 pulls sources via allowlisted web_fetch +"
echo "     D1 catalog reads + lineage memory; composes a source"
echo "     bundle attestation tagged source_bundle:<topic_slug>."
echo "  2. Analyst-D10 consumes the bundle + composes a per-claim"
echo "     decomposition: each claim → cited support spans →"
echo "     verify_claim.v1 verdict. Writes decomposition attestation"
echo "     tagged decomposition:<topic_slug>."
echo "  3. Critic-D10 consumes the decomposition + composes counter-"
echo "     arguments (counter-evidence, alternative interpretations,"
echo "     missing considerations). Writes counter attestation"
echo "     tagged counter_argument:<topic_slug>. NEVER overwrites"
echo "     the analyst's verdict — both stand in the audit chain."
echo "  4. Lab_Synthesizer-D10 aggregates decomposition + counters"
echo "     into a synthesis report with citation_graph_build.v1 +"
echo "     confidence_score.v1 alongside. Writes synthesis attestation"
echo "     tagged synthesis:<topic_slug>."
echo "  5. Debate_Moderator-D10 orchestrates structured multi-agent"
echo "     debates via debate_orchestrate.v1 (deterministic turn-"
echo "     ordering) + claim_provenance.v1 (citation-graph walk)."
echo "     NEVER takes a substantive turn — ORDERS + FRAMES only."
echo "     The hypothesis_testing.v1 skill delegates short-horizon"
echo "     tests to Experimenter-Smith (ADR-0056)."
echo
echo "Cascade rules wired in config/handoffs.yaml:"
echo "  d1_knowledge_forge.knowledge_summarize -> d10.source_gathering"
echo "    (ACTIVE — D1 librarian summarize-request seeds D10 deep research)"
echo "  d10.research_synthesis -> d1_knowledge_forge.knowledge_curation"
echo "    (ACTIVE — D10 syntheses cascade back into D1's catalog)"
echo "  d10.research_synthesis -> d9_learning_coach.curriculum_module"
echo "    (ACTIVE — D10 syntheses seed D9 curriculum modules)"
echo "  d10.research_synthesis -> d7_content_studio.content_drafting"
echo "    (ACTIVE — D10 syntheses seed D7 long-form drafts)"
echo
echo "Downstream cascades declared INERT in handoffs.yaml comments:"
echo "  d9.deep_research_request -> d10.research (D9 side needs new capability)"
echo "  d10.adr_proposal -> d4.review_signoff (both sides need adr_proposal capability)"
echo "  verifier_loop -> d10 (structural — substrate role, not domain capability)"
echo
echo "Press any key to close this window."
read -n 1 || true
