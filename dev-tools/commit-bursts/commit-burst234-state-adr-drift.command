#!/bin/bash
# Burst 234 — STATE.md + ADR drift refresh post-B233.
#
# Doc-only burst. No code changed. Restores the doc layer as a
# trustable starting point before picking between heavy arcs
# (Phase 4 security hardening vs open-web tool family).
#
# Three files touched:
#
# 1. STATE.md
#    - "Last updated" paragraph rewritten to fold in Bursts
#      213-233 (swarm re-acceptance, 24/7 launchd, ADR-0060 full
#      arc, MCP HTTP transport, marketplace expansion + Phase A,
#      20-item seed catalog).
#    - Numbers table refreshed against disk:
#        - Source LoC: 56,113 -> 59,602
#        - Tests: 2,738 -> 2,800
#        - ADRs filed: 56 -> 57 (added ADR-0060)
#        - Skill manifests: 26 -> 36 (+10 marketplace seed B232/B233)
#        - NEW row: Marketplace seed tools (10 in examples/tools/)
#        - Schema version: v15 -> v17 (added v16 reserved + v17
#          agent_catalog_grants for ADR-0060 T1)
#        - Audit event types: 73 -> 76
#        - .command at root: 59 -> 68
#        - dev-tools/commit-bursts/ archived: 172 -> 204
#        - Commits on main: 369 -> 389
#        - Audit chain entries: 7,581 -> 8,870
#    - ADR-003Y wording corrected (was "draft", is Y1-Y7 shipped).
#    - ADR-0049/0050/0051 reframed as "the Phase 4
#      security-hardening runway" (drafted, no implementation).
#    - ADR-0052/0053 status clarified (Proposed, ADR-0053 is the
#      substrate ADR-0047/0048 T4 per-tool granularity is waiting
#      on).
#    - ADR-0054 wording extended (T6 docs runbook B217 shipped;
#      chat-tab review card UI half still queued).
#    - ADR-0055 status updated (Phase A shipped; B/C/D queued).
#    - ADR-0060 added (Accepted + all six tranches shipped).
#    - 24/7 ops gap closed by B216 noted under alive-agents row.
#
# 2. docs/decisions/ADR-0060-runtime-tool-grants.md
#    - Status block was "T1 lands B219. T2-T6 queued." All six
#      tranches actually shipped B219-B223. Status block rewritten
#      with per-tranche burst + commit refs so future readers can
#      trace the implementation surface from the ADR alone.
#
# 3. docs/decisions/ADR-0055-agentic-marketplace.md
#    - Status block extended with "Phase A shipped 2026-05-11
#      (Bursts 227-229) + 20-item seed catalog 2026-05-11 to
#      2026-05-12 (Bursts 230-233)" and a pointer to the roadmap
#      doc at docs/roadmap/2026-05-11-marketplace-roadmap.md.
#    - Each kernel-side endpoint annotated with its burst-of-
#      origin (M1=B184, M3=B227, M7=Phase B, etc.) so the ADR
#      shows the actual implementation status, not just the plan.
#    - Marketplace org status noted (not yet scaffolded).
#
# Per ADR-0001 D2: no identity surface touched (doc-only).
# Per ADR-0044 D3: zero ABI impact (doc-only).
# Per CLAUDE.md Hippocratic gate: this is purely additive doc
#   work. No removals, no code changes, no migration.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add STATE.md \
        docs/decisions/ADR-0060-runtime-tool-grants.md \
        docs/decisions/ADR-0055-agentic-marketplace.md \
        dev-tools/commit-bursts/commit-burst234-state-adr-drift.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs: STATE.md + ADR drift refresh post-B233 (B234)

Burst 234. Doc-only refresh of three files to fold Bursts 213-233
into the canonical doc layer before picking the next heavy arc.

STATE.md:
  - Rewrote 'Last updated' paragraph covering swarm re-acceptance,
    24/7 launchd, ADR-0060 6-tranche arc, MCP HTTP transport,
    marketplace expansion + Phase A, 20-item seed catalog.
  - Refreshed numbers table against disk (LoC 56,113 to 59,602,
    tests 2,738 to 2,800, schema v15 to v17, audit events 73 to
    76, ADRs 56 to 57, commits 369 to 389, audit chain 7,581 to
    8,870).
  - Added new row: Marketplace seed tools (10 in examples/tools/).
  - Skill manifests count updated 26 to 36 with seed-skill names.
  - ADR-003Y wording corrected (was 'draft', is Y1-Y7 shipped).
  - Phase 4 security ADRs (0049/0050/0051) reframed as 'the
    Phase 4 security-hardening runway'.

ADR-0060:
  - Status block was 'T1 lands B219. T2-T6 queued.' Updated to
    'All six tranches shipped 2026-05-11' with per-tranche burst
    and commit refs (B219 d01ee2e through B223 64c1679).

ADR-0055:
  - Status block extended with Phase A shipped reference and
    pointer to docs/roadmap/2026-05-11-marketplace-roadmap.md.
  - Each kernel-side endpoint annotated with burst-of-origin.

Per ADR-0001 D2: no identity surface touched (doc-only).
Per ADR-0044 D3: zero ABI impact (doc-only).
Per CLAUDE.md Hippocratic gate: additive doc work, no removals."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 234 complete ==="
echo "=== Doc layer refreshed. Ready to pick next heavy arc. ==="
echo "Press any key to close."
read -n 1
