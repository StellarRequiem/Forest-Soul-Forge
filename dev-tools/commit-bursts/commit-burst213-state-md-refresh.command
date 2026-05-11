#!/bin/bash
# Burst 213 — STATE.md refresh post-Bursts 200-212.
#
# STATE.md was last touched at B199 (2026-05-08). Bursts 200-212
# shipped the natural-language Forge UI arc + uninstall endpoints
# without an accompanying STATE refresh, so the developer-facing
# current-reality view drifted three days and 11 commits behind.
#
# B213 closes that drift. Updates:
#
#   - Header "Last updated" line: 2026-05-08 → 2026-05-11.
#   - Header narrative: replaced the B199-era paragraph with a
#     summary of the Bursts 200-212 arc (ADR-0057 + ADR-0058 +
#     ADR-0059, three artifacts forged + dispatched end-to-end).
#   - Test count: 2,598 → 2,738 (B201/B202 forge tests + B206
#     fixture migration restoring previously-blocked tests).
#   - ADRs filed: 53 / 51 → 56 / 54 with new entries for ADR-0057,
#     ADR-0058, ADR-0059.
#   - Audit event types: 71 → 73 (B212 forge_skill_uninstalled +
#     forge_tool_uninstalled).
#   - Total commits on main: 358 → 369.
#   - Live audit chain entries: ~3,840 → ~7,581.
#   - NEW row "Alive agents in registry" — 14 with role breakdown
#     and the explicit "zero blue-team agents currently alive" note
#     (the swarm bringup is queued).
#   - NEW row "Installed forged artifacts" — 2 skills + 1 tool from
#     the Bursts 200-212 arc, including the live-dispatched
#     translate_to_french.v1.
#
# What we deliberately did NOT do:
#   - Update README.md. STATE.md is the developer-facing current-
#     reality view; README.md is the product-and-mission view (per
#     STATE.md's own refresh-cadence note). They DO update together
#     at phase boundaries, but Bursts 200-212 didn't change the
#     product mission. README refresh is its own burst when the
#     v0.6 tag lands or the kernel positioning changes.
#   - Update CHANGELOG.md. Operational changes between tags live
#     in CHANGELOG; pre-tag drift lives in commit messages. Same
#     reasoning as README.
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: no code changed — pure documentation refresh.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add STATE.md \
        dev-tools/commit-bursts/commit-burst213-state-md-refresh.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(state): STATE.md refresh post-Bursts 200-212 (B213)

Burst 213. STATE.md was last touched 2026-05-08 at B199 — three
days and 11 commits stale. The Bursts 200-212 arc (natural-language
Forge UI + uninstall endpoints) needed a developer-facing summary.

Refresh updates:
- Header narrative: B199 paragraph replaced with arc summary
  covering ADR-0057 / ADR-0058 / ADR-0059, three forged artifacts
  installed + dispatched live (summarize_audit_chain.v1,
  translate_to_french.v1, text_to_bullet_points.v1).
- Test count 2,598 -> 2,738.
- ADRs filed 53/51 -> 56/54 with ADR-0057/0058/0059 added.
- Audit event types 71 -> 73 (B212 forge_*_uninstalled).
- Commits on main 358 -> 369.
- Audit chain entries ~3,840 -> ~7,581.
- New row 'Alive agents in registry' — 14 with the explicit
  'zero blue-team agents currently alive' note.
- New row 'Installed forged artifacts' — 2 skills + 1 tool.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: pure documentation refresh."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 213 complete ==="
echo "=== STATE.md current as of HEAD. ==="
echo "Press any key to close."
read -n 1
