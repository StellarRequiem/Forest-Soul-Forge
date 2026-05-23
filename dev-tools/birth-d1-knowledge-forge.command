#!/bin/bash
# ADR-0086 — D1 Personal Knowledge Forge umbrella birth script.
#
# Births all four D1 agents in order, idempotent. Run this after
# pulling D1-A through D1-D and restarting the daemon — each
# child script restarts the daemon itself, so explicit restart
# beforehand is optional.
#
# Order matters loosely (each script is independent), but
# birthing librarian + prospector first establishes the catalog
# + sourcing lanes that synthesizer and knowledge_verifier
# operate on.

set -euo pipefail
cd "$(dirname "$0")/.."

echo "=========================================================="
echo "ADR-0086 — Birth D1 Personal Knowledge Forge (4 agents)"
echo "=========================================================="
echo

echo "[1/4] Librarian-D1"
./dev-tools/birth-librarian.command < /dev/null
echo

echo "[2/4] Prospector-D1"
./dev-tools/birth-prospector.command < /dev/null
echo

echo "[3/4] Synthesizer-D1"
./dev-tools/birth-synthesizer.command < /dev/null
echo

echo "[4/4] KnowledgeVerifier-D1"
./dev-tools/birth-knowledge-verifier.command < /dev/null
echo

echo "=========================================================="
echo "D1 Personal Knowledge Forge — 4 agents alive."
echo "=========================================================="
echo
echo "Next steps:"
echo "  1. Pull source material via Prospector-D1's"
echo "     research_gathering skill (operator-allowlisted URLs)."
echo "  2. Catalog the material via Librarian-D1's"
echo "     knowledge_curation skill (one claim per call;"
echo "     provenance per fact)."
echo "  3. Build topic graphs via Synthesizer-D1's"
echo "     topic_genealogy skill once you have 3+ catalog"
echo "     entries on a topic."
echo "  4. Run a contradiction sweep via KnowledgeVerifier-D1's"
echo "     knowledge_contradiction_flag skill (YELLOW posture;"
echo "     flags surface to the approval queue)."
echo "  5. Pull a daily delta via Synthesizer-D1's"
echo "     daily_knowledge_delta skill (24h default)."
echo
echo "Cascade rules already wired in config/handoffs.yaml:"
echo "  d8_compliance.compliance_scan -> d1.knowledge_curation"
echo "    (active — framework rule updates flow into catalog)"
echo
echo "Downstream cascades (d1 -> d9/d10/d7/d2) declared INERT"
echo "in handoffs.yaml comments until those domains ship."
echo
echo "Press any key to close this window."
read -n 1 || true
