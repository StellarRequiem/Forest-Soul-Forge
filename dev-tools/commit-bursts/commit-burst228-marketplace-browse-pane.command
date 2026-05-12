#!/bin/bash
# Burst 228 — ADR-0055 M4: frontend Marketplace tab + Browse pane.
#
# Closes Phase A4. With M3 (the install endpoint) shipped at
# B227, M4 is the operator-facing browse surface. An operator
# can now configure FSF_MARKETPLACE_REGISTRIES, switch to the
# Marketplace tab, browse entries, filter by side_effects tier
# or text search, and click Install to land any plugin without
# leaving the SoulUX.
#
# What ships:
#
# 1. New top-level tab Marketplace, placed between Tools and
#    Memory per the roadmap recommendation. SVG icon styled to
#    match the existing tab vocabulary.
#
# 2. New panel under data-panel="marketplace" with:
#      - Status indicator (entry count, fetched_at, stale flag)
#      - Refresh button
#      - Filter row: text search + max side_effects tier select
#        (read_only / network / filesystem / external)
#      - Meta line: configured registries count, failed
#        registries count, last fetch time
#      - Entry list (rendered by JS)
#
# 3. New JS module frontend/js/marketplace.js:
#      - start() fetches /marketplace/index on boot + tab
#        activation
#      - _renderEntry() — entry card with:
#          * Title row: name, version, tier pill (color-coded by
#            risk), "untrusted" badge (M6 signing not yet
#            enforced)
#          * Description text
#          * Permissions summary in a highlighted block — the
#            load-bearing piece for operator informed consent
#          * Meta line: id, author, capability counts (tools +
#            skills + mcps), source URL link
#          * Install button → POST /marketplace/install
#      - _applyFilters() — client-side search + tier-ceiling
#        predicate. Sort: safer-first (lower tier rank), then
#        alphabetical
#      - Toast on success showing trust state explicitly
#      - Toast on failure carrying daemon's detail message
#        (sha mismatch, unknown entry, etc.)
#
# 4. app.js wires marketplacePanel.start() alongside the other
#    panels.
#
# Trust posture: every entry renders an "untrusted" badge. The
# badge tooltip explains M6 signing is queued; operators are
# trusting the source registry's reputation rather than a kernel-
# verified signature. When M6 lands, the badge flips off for
# verified entries.
#
# Verification:
#   - node --check clean on marketplace.js + app.js
#   - All 6 required HTML element ids present
#   - 1 tab button with data-tab="marketplace"
#   - 52 existing tests still pass (marketplace_index +
#     audit_chain regression sweep)
#
# Visual verification (operator-side after this lands):
#   1. Configure FSF_MARKETPLACE_REGISTRIES (or test with
#      file:// pointing at a local marketplace.yaml fixture)
#   2. Restart daemon
#   3. Open SoulUX, click Marketplace tab
#   4. See entries (or "no entries" if registry empty)
#   5. Type in search box → list filters
#   6. Pick "read_only only" in tier filter → only the safest
#      entries remain
#   7. Click Install on an entry → daemon downloads, SHA-verifies,
#      installs, reloads. Toast confirms. Switch to Tools tab to
#      see the new tools registered.
#
# What's queued next in Phase A:
#   A5 — M5 post-install grant flow ("Use with [agent]" picker
#        after Install — pipes into the existing ADR-0060 grant
#        endpoints from B220).
#   A4 — M6 signing pipeline (blocked on maintainer keypair).
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: pure frontend addition wired to existing
#                  endpoints (M1 from B184, M3 from B227).

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add frontend/index.html \
        frontend/js/marketplace.js \
        frontend/js/app.js \
        dev-tools/commit-bursts/commit-burst228-marketplace-browse-pane.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(frontend): Marketplace tab + Browse pane (B228)

Burst 228 / ADR-0055 M4. Operator-facing browse surface for the
marketplace. With B227 (install endpoint) and this burst, an
operator can configure FSF_MARKETPLACE_REGISTRIES, open the
Marketplace tab, browse and filter entries, and one-click install.

New top-level Marketplace tab between Tools and Memory.

Panel features:
  - Text search + side_effects tier filter
  - Entry cards with name + version + risk-tier pill + untrusted
    badge + description + permissions summary + author + capability
    counts + source URL link + Install button
  - Stale/failed-registry status indicator
  - Auto-refresh on tab activation

Trust posture: every entry shows 'untrusted' until M6 signing
enforcement lands. Tooltip explains the deferred state.

Verification: node --check clean, 6 required HTML hooks present,
52 backend regression tests pass.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: pure frontend addition over existing endpoints
                 (M1 from B184, M3 from B227)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 228 complete ==="
echo "=== Marketplace M4 live. Phase A4 done; next M5 (grant-on-install) or M6 (signing). ==="
echo "Press any key to close."
read -n 1
