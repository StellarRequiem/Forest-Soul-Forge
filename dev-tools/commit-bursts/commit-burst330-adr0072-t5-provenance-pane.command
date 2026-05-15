#!/bin/bash
# Burst 330 - ADR-0072 T5: frontend Provenance pane.
#
# CLOSES ADR-0072 5/5 and Phase α 10/10. The operator can now
# see, at a glance, every layer of the precedence ladder:
# hardcoded handoffs (tier 1000) > constitutional (800) >
# preference (400) > learned (100). Active vs pending vs refused
# learned rules are bucketed with status pills + RA verdicts.
#
# What ships:
#
# 1. src/forest_soul_forge/daemon/routers/provenance.py (NEW):
#    Two read-only endpoints.
#      GET /provenance/active   — preferences + learned-rule
#        buckets (active / pending_activation / refused) + the
#        canonical precedence ladder.
#      GET /provenance/handoffs — hardcoded skill mappings +
#        cascade rules from config/handoffs.yaml.
#    Errors land in the 'errors' field; structural failures
#    return empty payloads + error strings so the frontend
#    degrades gracefully.
#
# 2. src/forest_soul_forge/daemon/app.py:
#    Imports + registers the router.
#
# 3. frontend/index.html:
#    New Provenance tab with four panels (precedence ladder,
#    preferences table, learned-rule buckets with status pills,
#    hardcoded handoffs).
#
# 4. frontend/js/provenance.js (NEW):
#    initProvenancePane() controller. Fetches both endpoints,
#    renders precedence table + preferences + learned rules
#    (bucketed with active/pending/refused status pills + RA
#    verification reason column) + hardcoded handoffs (defaults
#    + cascade rules).
#
# 5. frontend/js/app.js:
#    Imports + initializes the pane in both boot branches.
#
# Tests (test_provenance_router.py - 6 cases):
#   /provenance/active (4):
#     precedence ladder shape + numbering, missing files return
#     empty payload + soft errors, status buckets correctly
#     filtered (active vs pending vs refused), pending list with
#     status='active' rule does NOT bleed into pending bucket
#   /provenance/handoffs (2):
#     missing file returns empty lists, populated file returns
#     flat default_skill + cascade arrays
#
# Sandbox-verified 6/6 pass.
#
# === ADR-0072 CLOSED 5/5 ===
# Behavior provenance arc complete.
#
# === PHASE α COMPLETE — 10/10 SCALE ADRS CLOSED ===
# ADR-0050 encryption-at-rest, ADR-0067 cross-domain orchestrator,
# ADR-0068 operator profile, ADR-0070 voice I/O, ADR-0071 plugin
# author + adapter kit, ADR-0072 behavior provenance, ADR-0073
# audit chain segmentation, ADR-0074 memory consolidation,
# ADR-0075 scheduler scale, ADR-0076 vector index for personal
# context.
#
# Ten-domain platform substrate is in place. Next direction:
# domain rollouts in dependency order D4 → D3 → D8 → D1 → D2 →
# D7 → D9 → D10 → D5 → D6.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/routers/provenance.py \
        src/forest_soul_forge/daemon/app.py \
        frontend/index.html \
        frontend/js/provenance.js \
        frontend/js/app.js \
        tests/unit/test_provenance_router.py \
        dev-tools/commit-bursts/commit-burst330-adr0072-t5-provenance-pane.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(provenance): ADR-0072 T5 - Provenance pane (B330) — PHASE α 10/10

Burst 330. Closes ADR-0072 5/5 and Phase α 10/10. The operator
can now see, at a glance, every layer of the precedence ladder:
hardcoded handoffs (tier 1000) > constitutional (800) >
preference (400) > learned (100). Active vs pending vs refused
learned rules are bucketed with status pills + RA verdicts.

What ships:

  - daemon/routers/provenance.py (NEW): GET /provenance/active
    + GET /provenance/handoffs. Read-only surfaces that power
    the frontend pane. Missing files return empty payloads +
    soft errors so the frontend degrades gracefully.

  - daemon/app.py: registers the router.

  - frontend/index.html: new Provenance tab + panel with four
    sections (precedence ladder, preferences table, learned-
    rule buckets, hardcoded handoffs).

  - frontend/js/provenance.js (NEW): initProvenancePane()
    controller. Fetches both endpoints, renders the
    precedence-ladder table, preferences (id/statement/weight/
    domain), learned rules bucketed by status with colored
    pills + RA verification reason column, and hardcoded
    handoffs (default_skill_per_capability + cascade_rules).

  - frontend/js/app.js: initializes the pane in both boot
    branches.

Tests: test_provenance_router.py — 6 cases covering 4 active
endpoint scenarios (precedence ladder shape, missing files
soft-error, status bucket filtering, mismatched-status rules
don't bleed into wrong buckets) and 2 handoffs cases (missing
file empty, populated file returns flat arrays). Sandbox-
verified 6/6 pass.

=== ADR-0072 CLOSED 5/5 ===
Behavior provenance arc complete.

=== PHASE α COMPLETE — 10/10 SCALE ADRS CLOSED ===
ADR-0050 encryption-at-rest, ADR-0067 cross-domain orchestrator,
ADR-0068 operator profile, ADR-0070 voice I/O, ADR-0071 plugin
author + adapter kit, ADR-0072 behavior provenance, ADR-0073
audit chain segmentation, ADR-0074 memory consolidation,
ADR-0075 scheduler scale, ADR-0076 vector index for personal
context.

Ten-domain platform substrate is in place. Next direction:
domain rollouts in dependency order D4 → D3 → D8 → D1 → D2 →
D7 → D9 → D10 → D5 → D6."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 330 complete - PHASE α 10/10 COMPLETE ==="
echo "All ten Phase α scale ADRs closed."
echo ""
echo "Press any key to close."
read -n 1
