#!/bin/bash
# Burst 125 — STATE.md refresh, Bursts 116-124.
#
# Critical prerequisite for ADR-0044 P2 (formal kernel API spec).
# The kernel API spec will reference STATE.md numbers, ADR list,
# and role inventory; all three were 9 bursts stale post-Burst-124.
#
# What ships:
#
#   STATE.md — section-by-section refresh:
#
#     Header — last-updated bumped from "post-Burst 115" to
#       "post-Burst 124". Dense paragraph rewritten to capture
#       v0.5.0 final tag (B116), ADR-0044 kernel positioning
#       (B117), Phase 1 boundary doc + KERNEL.md + sentinel
#       (B118-120), ADR-0046 license/governance (B121),
#       CONTRIBUTING + CoC (B122), B124 role expansion 18 -> 42.
#
#     Numbers table — five rows updated:
#       Source LoC: unchanged (50,289 — v0.6 arc to date is pure
#         docs + config)
#       Tests passing: unchanged (2,386 — test_expected_role_count
#         assertion bumped 18 -> 42 in B124, no test count delta)
#       ADRs filed: 41 files / 39 unique -> 43 files / 41 unique
#         (added ADR-0044 + ADR-0046)
#       .command operator scripts: 130 -> 140 (B116-124 scripts,
#         most untracked pending housekeeping burst)
#       Trait roles: 18 -> 42 (5 original + 9 swarm + 3 SW-track
#         + 1 ADR-0036 verifier_loop + 24 v0.6 expansion across
#         8 tranches with per-tranche ADR bindings noted)
#       Total commits on main: 273 -> 281
#       Live audit chain: 1118 -> 1121 entries
#
#     Genres table — every "(aspirational)" entry now resolves to
#       real roles per Burst 124. Added rows for the 3 web genres
#       (web_observer / web_researcher / web_actuator) with their
#       single role each (web_watcher / web_researcher /
#       web_actuator), bringing the visible table to 13 genres
#       matching genres.yaml's count.
#
#     ADR map — 5 new rows: ADR-0042 (v0.5 product direction
#       Accepted, T5 gated), ADR-0043 (MCP plugin protocol Accepted
#       + 4 follow-ups), ADR-0044 (kernel positioning Accepted, P1
#       + P5 shipped, P2 next), ADR-0045 (posture Accepted), ADR-0046
#       (license + governance Accepted).
#
#     Items in queue — reranked post-Burst-124. ADR-0044 P2/P3/P4
#       now leads the queue. ADR-003X completed work removed from
#       blockers. ADR-0042 T5 + ADR-0043 #4 listed as gated/deferred
#       with their specific gating questions. Housekeeping bundle
#       (Burst 126) listed.
#
#     Where to start contributing — item 1 changed from "file
#       ADR-003X" (already shipped) to "ADR-0044 P2 kernel API
#       spec." Read-list reordered to lead with KERNEL.md +
#       boundary doc + ADR-0044 (the v0.6 strategic posture).
#
# Verification:
#   - 544 lines (was 524; +20 net for genre rows + 5 ADR rows +
#     queue rerank).
#   - 11 KERNEL.md cross-references (was 0).
#   - 13 ADR-0044 references.
#   - Zero references to "18 (5 original" (the stale role count).
#   - All 24 Burst 124 role names appear in the genre table.
#
# This closes the must-do precondition for Burst 127 (ADR-0044 P2
# kernel API spec). Burst 126 (housekeeping bundle) lands next as
# an opportunistic cleanup before P2 work begins.

set -euo pipefail

cd "$(dirname "$0")"

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add STATE.md

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs: STATE.md refresh, Bursts 116-124 (B125)

Burst 125. Critical prerequisite for ADR-0044 P2 (formal kernel
API spec): STATE.md was 9 bursts stale post-Burst-124. The kernel
API spec needs accurate numbers + ADR list + role inventory.

Section-by-section refresh:

- Header: 'post-Burst 115' -> 'post-Burst 124'. Dense paragraph
  rewritten to capture v0.5.0 final tag (B116), ADR-0044 kernel
  positioning (B117), Phase 1 boundary doc + KERNEL.md + dev-tools
  sentinel (B118-120), ADR-0046 license/governance (B121),
  CONTRIBUTING + CoC (B122), B124 role expansion.

- Numbers table:
  * Source LoC: unchanged (50,289)
  * Tests passing: unchanged (2,386)
  * ADRs filed: 41/39 -> 43/41 (added ADR-0044, ADR-0046)
  * .command scripts: 130 -> 140
  * Trait roles: 18 -> 42 (per-tranche breakdown with ADR
    bindings: T7 companion ext bound to ADR-0038, T8 web genres
    bound to ADR-003X)
  * Total commits on main: 273 -> 281
  * Live audit chain: 1118 -> 1121 entries

- Genres table: every '(aspirational)' entry now resolves to
  real roles per Burst 124. Added 3 web genre rows
  (web_observer/web_researcher/web_actuator) bringing visible
  table to 13 genres matching genres.yaml.

- ADR map: 5 new rows for ADR-0042 through ADR-0046, each
  capturing what shipped vs. what's gated/deferred.

- Items in queue: reranked post-Burst-124. ADR-0044
  P2/P3/P4 leads. ADR-003X completed work removed from
  blockers. ADR-0042 T5 + ADR-0043 #4 listed as gated/
  deferred with specific gating questions. Housekeeping
  bundle (Burst 126) listed.

- Where to start contributing: item 1 changed from 'file
  ADR-003X' (already shipped) to 'ADR-0044 P2 kernel API
  spec'. Read-list reordered to lead with KERNEL.md +
  boundary doc + ADR-0044.

Verification:
- 544 lines (was 524; +20 net for genre rows + 5 ADR rows +
  queue rerank)
- 11 KERNEL.md cross-references wired in
- 13 ADR-0044 references
- 0 references to '18 (5 original' (stale role count purged)
- All 24 Burst 124 role names appear in the genre table

Closes the must-do precondition for Burst 127 (ADR-0044 P2
kernel API spec). Burst 126 (housekeeping bundle) lands next
as opportunistic cleanup before P2 work begins."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 125 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
