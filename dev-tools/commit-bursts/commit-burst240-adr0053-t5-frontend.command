#!/bin/bash
# Burst 240 — ADR-0053 T5: Chat-tab per-tool toggle grid.
#
# Replaces the ADR-0048 T4 read-only per-tool reference table
# with an interactive checkbox grid wired directly to the new
# ADR-0053 T3 per-tool endpoints (B238). Plus a rewrite of the
# Restricted / Specific / Full preset semantics per ADR-0053 D5.
#
# Substrate is now end-to-end operator-usable:
#   T1 schema (B235) → T2 registry surface (B237) → T3 API
#   (B238) → T4 dispatcher resolver (B239) → T5 UI (this).
#
# Preset semantics per ADR-0053 D5:
#   Restricted — Revoke plugin-level + revoke ALL per-tool
#                grants. Clean state.
#   Specific   — Revoke plugin-level + issue per-tool grants
#                for the two read_only tools (screenshot +
#                clipboard read) at yellow tier. Operator
#                extends via Advanced toggles from there.
#   Full       — Revoke ALL per-tool grants + issue plugin-
#                level grant at green tier.
#
# Per-tool toggle semantics:
#   - Check a row that has no grant   → POST per-tool grant
#                                       at yellow tier.
#   - Uncheck a row with per-tool row → DELETE the per-tool
#                                       grant. Plugin-level
#                                       coverage (if any) is
#                                       UNTOUCHED.
#   - Uncheck a row covered by the    → POST per-tool grant
#     plugin-level grant (no per-      at RED tier — carve
#     tool row exists yet)             out a denial inside the
#                                       broader grant via
#                                       specificity-wins.
#
# The coverage column in the table distinguishes:
#   "(per-tool yellow)" — per-tool override active
#   "(via plugin-level)" — covered only by the plugin-level grant
#   "" (blank)         — uncovered, no grant fires
#
# Files touched:
#
# 1. frontend/index.html
#    - Advanced disclosure summary text updated to
#      "per-tool toggles" (was "per-tool reference").
#    - Static <tbody> rows replaced with a JS-rendered
#      container (id="chat-assistant-allow-tools").
#    - Table header gains "Per-tool grant" column.
#    - Disclosure intro text rewritten to describe the
#      per-tool semantic + the specificity-wins resolution.
#
# 2. frontend/js/chat.js
#    - ALLOW_TOOLS constant: the 6 tools with side_effects +
#      approval metadata.
#    - SPECIFIC_PRESET_TOOLS constant: the seeded set
#      (screenshot + clipboard read).
#    - renderAssistantAllowances rewritten:
#        * Distinguishes plugin-level vs per-tool grants
#          via the new tool_name field on the GET response.
#        * Resolves preset based on which rows are active.
#        * Status line summarizes the effective state
#          (handles plugin-level-only, per-tool-only, mixed,
#          and unrestricted-with-per-tool-overrides).
#    - New renderPerToolGrid: tbody innerHTML rendered per
#      tool with checked state reflecting effective coverage.
#    - New wirePerToolCheckboxes: per-row change handler
#      issues POST/DELETE per the semantics above. Reverts
#      the checkbox on failure + surfaces the error in the
#      feedback line.
#    - applyAssistantAllowancePreset rewritten per D5: each
#      preset is now a coherent state transition (clear stale
#      rows + apply the new shape) rather than a single
#      grant POST.
#
# 3. docs/decisions/ADR-0053-per-tool-plugin-grants.md
#    - Status bumped to T1+T2+T3+T4+T5 shipped. "Operator-
#      usable end-to-end via the Chat-tab Advanced disclosure."
#    - Tranche table marks T5 DONE.
#
# Test verification (sandbox):
#   daemon_plugin_grants + plugin_grants + posture_per_grant:
#     88 passed
#   node --check on chat.js: clean
#   Frontend JS reload picks up automatically — daemon
#   restart NOT required (static asset). Browser-side
#   verification by operator after push lands.
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: zero kernel impact — UI-only change.
# Per CLAUDE.md Hippocratic gate: no removals; the read-only
#   reference table is replaced with a functional grid that
#   serves the same audience but does more.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add frontend/index.html \
        frontend/js/chat.js \
        docs/decisions/ADR-0053-per-tool-plugin-grants.md \
        dev-tools/commit-bursts/commit-burst240-adr0053-t5-frontend.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(frontend): ADR-0053 T5 per-tool toggle grid (B240)

Burst 240. ADR-0053 T5 — Chat-tab Advanced disclosure goes
from read-only reference table to interactive per-tool
checkbox grid. Substrate is now operator-usable end-to-end.

Per-tool toggles wire to the new ADR-0053 T3 endpoints (B238).
Each row's checkbox issues or revokes a per-tool grant; the
coverage column distinguishes per-tool overrides from
plugin-level coverage so the operator can see what's narrowing
what.

Preset semantics rewritten per ADR-0053 D5:
  Restricted - clears plugin-level + all per-tool grants
  Specific   - revokes plugin-level + seeds per-tool yellow
               grants for the two read_only tools (screenshot
               + clipboard read); operator extends via Advanced
  Full       - revokes ALL per-tool overrides + issues a
               plugin-level grant at green tier

Unchecking a tool currently covered by a plugin-level grant
records a per-tool RED grant — the 'carve out a denial inside
a broad grant' use case ADR-0053 D2 calls out. Specificity-
wins resolver (T4, B239) then refuses that one tool while the
others remain ungated.

Tests: daemon_plugin_grants + plugin_grants + posture_per_grant
suite stays green (88 passed). UI changes are JS+HTML; browser
reload picks up automatically.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: UI-only — zero kernel impact.
Per CLAUDE.md Hippocratic gate: no removals."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 240 complete ==="
echo "=== ADR-0053 substrate operator-usable end-to-end. T6 (doc updates) queued. ==="
echo "Press any key to close."
read -n 1
