#!/bin/bash
# Burst 109 — STATE.md + CHANGELOG refresh covering Bursts 95-108
# (the v0.5 arc), plus push of all unpushed commits.
#
# Bookkeeping pass: STATE was stale (last touched post-Burst 94 /
# v0.4.0 final). CHANGELOG [Unreleased] was empty. Bursts 95-108
# shipped two new ADRs (ADR-0042 v0.5 Product Direction, ADR-0043
# MCP-First Plugin Protocol) plus 13 implementation commits, none
# of which were reflected in either doc.
#
# Numbers refreshed against disk reality (per CLAUDE.md "if a
# number changes, measure it from disk"):
#   - Test count: 2177 → 2289 (+112)
#   - Source LoC: 44,648 → 48,760 (+~4,100)
#   - ADRs: 38 → 40 files (+ADR-0042 +ADR-0043)
#   - Commits on main: 250 → 264 (+14)
#   - .command scripts: 107 → 120 (+13)
#   - Audit chain entries: 1083 → 1118 (+35)
#   - Audit event types: 62 → 67 (+5 plugin lifecycle events)
#
# This is bookkeeping only — no functional changes. The next
# burst (Burst 110) tags v0.5.0-rc as a checkpoint marking the
# implementation-complete portion of the v0.5 arc. v0.5.0 final
# gates on:
#   - Apple Developer signing decision (ADR-0042 T5)
#   - ADR-0043 deferred follow-ups (per-tool approval mirroring,
#     allowed_mcp_servers auto-grant, frontend Tools-tab plugin
#     awareness, plugin_secret_set audit event)

set -euo pipefail

cd "$(dirname "$0")"

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

# Stage docs only — keep this commit clean.
git add STATE.md CHANGELOG.md

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs: STATE + CHANGELOG refresh for v0.5 arc (Bursts 95-108)

Bookkeeping pass closing the documentation gap that opened
between Burst 94 (v0.4.0 final docs refresh) and Burst 108
(ADR-0043 T5 example plugins). Bursts 95-108 shipped two new
Accepted ADRs (ADR-0042 v0.5 Product Direction, ADR-0043
MCP-First Plugin Protocol) plus implementation tranches; none
were reflected in STATE.md or CHANGELOG until now.

STATE.md updates:
- Last-updated banner rewritten to cover v0.5 arc + ADR-0042 +
  ADR-0043 status (Accepted, T1-T5 implementation-complete for
  ADR-0043; T2/T3.1/T4 shipped + T5 gated for ADR-0042).
- Numbers table refreshed against disk:
  - Source LoC 44,648 → 48,760
  - Tests 2177 → 2289 (+112 across Bursts 95-108)
  - ADRs 38 → 40 files / 36 → 38 unique numbers
  - Commits on main 250 → 264
  - .command scripts 107 → 120
  - Audit chain entries 1083 → 1118
  - Audit event types 62 → 67 (+5 plugin lifecycle events)
- New 'Plugin examples' row enumerating the 3 canonical examples
  in examples/plugins/ (forest-echo / brave-search /
  filesystem-reference) covering the read_only / network /
  filesystem governance posture spectrum.
- 'Distribution' row notes ADR-0042 T4 PyInstaller binary +
  apps/desktop/ Tauri shell.
- 'Data dirs' row notes the new ~/.forest/plugins/ operator-
  managed plugin root (separate from repo per ADR-0043).
- TL;DR fixed: '40 builtin tools' → 53 (matches numbers table;
  pre-existing drift since v0.3.0 audit corrected the count).

CHANGELOG [Unreleased] populated with the full v0.5 arc:
- ADR-0042 (Burst 97) + T2 (Burst 98) + T3.1 (Burst 99) +
  T4 (Burst 101) + T5 deferred + viewport audit (Burst 100)
- ADR-0043 (Burst 103) + T2 (Burst 104) + T3 (Burst 105) +
  T4 (Burst 106) + T4.5 (Burst 107) + T5 (Burst 108)
- Burst 95 v0.4.0 final docs/tag, Burst 96 v0.5 planning,
  Burst 102 README audience + integrations roadmap

Test suite remains 2289 passing / 3 skipped / 1 xfailed.
Schema unchanged at v13. No functional changes in this commit."

echo "--- commit landed ---"
git log --oneline -1

# Push every unpushed commit on main.
echo ""
echo "--- pushing to origin/main ---"
git push origin main

echo ""
echo "=== Burst 109 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
