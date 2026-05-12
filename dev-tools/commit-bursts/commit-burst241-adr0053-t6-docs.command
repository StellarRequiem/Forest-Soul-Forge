#!/bin/bash
# Burst 241 — ADR-0053 T6: cross-doc updates.
#
# Closes the per-tool substrate-gap references in three places
# + adds a per-tool granularity section to the computer-control
# safety runbook. ADR-0053 now fully shipped: all six tranches
# done across Bursts 235-241.
#
# Files touched:
#
# 1. docs/decisions/ADR-0048-computer-control-allowance.md
#    - T4 row flipped from "DONE B165 (partial)" to "DONE B165
#      + B240 (fully)" with the "Per-tool granularity in
#      Advanced awaits substrate extension" wording struck and
#      replaced with a description of the ADR-0053 T5 toggle
#      grid + the new preset semantics.
#
# 2. docs/decisions/ADR-0043-mcp-plugin-protocol.md
#    - References list gains an explicit ADR-0053 pointer so
#      readers following the plugin-protocol chain see the
#      per-tool extension and understand byte-for-byte
#      compatibility with follow-up #2's grant substrate.
#
# 3. docs/runbooks/computer-control-safety.md
#    - Restricted/Specific/Full preset descriptions rewritten
#      per ADR-0053 D5 semantics.
#    - NEW "Per-tool granularity in Advanced (ADR-0053)" section
#      with: coverage column meaning, toggle semantics (including
#      the carve-out-denial RED-on-plugin-level pattern), and a
#      canonical configurations table.
#    - Audit-chain forensics section gets two new jq snippets
#      for filtering per-tool vs plugin-level grant/revoke events.
#    - Threat model corrected: the granted-skip-approval behavior
#      IS shipped via the ADR-0045 T3 + ADR-0053 D3 path, not
#      "hasn't been enabled yet."
#
# 4. docs/decisions/ADR-0053-per-tool-plugin-grants.md
#    - Status block flipped to "All six tranches shipped" with
#      end-to-end summary line.
#    - T6 row in the tranche table marked DONE B241 with full
#      implementation detail.
#
# 5. STATE.md
#    - Two "awaits ADR-0053 substrate" mentions (in the
#      ADR-0047 and ADR-0048 status entries) flipped to
#      "fully shipped via ADR-0053 substrate, B240."
#    - ADR-0053 status flipped from "Proposed, substrate for
#      ADR-0047/0048 T4 per-tool granularity" to "Accepted
#      2026-05-12, all 6 tranches shipped Bursts 235-241."
#
# This is a doc-only burst. Zero code changes, zero tests
# affected.
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: zero ABI impact (doc-only).
# Per CLAUDE.md Hippocratic gate: additive doc work + retiring
#   stale "awaits" wording. The historical "awaits substrate"
#   notes in ADR-0053's own body stay intact (drafting-time
#   context + credit section).

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0048-computer-control-allowance.md \
        docs/decisions/ADR-0043-mcp-plugin-protocol.md \
        docs/decisions/ADR-0053-per-tool-plugin-grants.md \
        docs/runbooks/computer-control-safety.md \
        STATE.md \
        dev-tools/commit-bursts/commit-burst241-adr0053-t6-docs.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs: ADR-0053 T6 cross-doc updates (B241)

Burst 241. ADR-0053 T6 — close the per-tool substrate-gap
references in ADR-0048, ADR-0043, and the computer-control
safety runbook. ADR-0053 now fully shipped end-to-end.

ADR-0048 T4 row: 'DONE B165 (partial)' to 'DONE B165 + B240
(fully)' with the 'awaits substrate' sentence struck.

ADR-0043 References: explicit ADR-0053 pointer with the
byte-for-byte compatibility note (per-tool grants are new rows
with non-null tool_name; plugin-level grants retain the
ADR-0043 semantic via NULL tool_name).

docs/runbooks/computer-control-safety.md: three preset
descriptions rewritten per ADR-0053 D5; new 'Per-tool
granularity in Advanced' section with coverage-column meaning,
toggle semantics (including the carve-out-denial pattern), and
canonical configurations table; new jq snippets for filtering
per-tool vs plugin-level audit events; threat model corrected.

STATE.md: two 'awaits ADR-0053 substrate' mentions flipped to
'fully shipped via ADR-0053 substrate, B240'; ADR-0053 row
flipped to 'Accepted, all 6 tranches shipped Bursts 235-241.'

Per ADR-0001 D2: no identity surface touched (doc-only).
Per ADR-0044 D3: zero ABI impact.
Per CLAUDE.md Hippocratic gate: additive doc work; historical
'awaits substrate' notes in ADR-0053's own body stay intact
(drafting-time context)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 241 complete ==="
echo "=== ADR-0053 fully shipped. Per-tool plugin grants end-to-end. ==="
echo "Press any key to close."
read -n 1
