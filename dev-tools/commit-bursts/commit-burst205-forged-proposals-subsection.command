#!/bin/bash
# Burst 205 — Forged proposals subsection in Approvals tab.
#
# The deferred-twice item from B201 (Skill Forge UI) and B202 (Tool
# Forge UI). Both bursts called this subsection a "UX nice-to-have"
# and skipped it because the modals handle install/discard inline.
# That's true for proposals you forge AND act on in one sitting,
# but breaks down for:
#
#   - Proposals from a previous session, where the modal is gone
#   - Smith / agent-driven cycle proposals where there's no modal
#     in the first place
#   - Operators wanting to batch-review N pending proposals at once
#
# Without this subsection, those proposals had no UI surface and
# the only way to discard them was to know the staged_path and rm
# by hand (sandbox can't even do that on the host filesystem; B204
# had to bake the rm into a commit script as a one-off).
#
# What ships:
#
#   frontend/index.html  MODIFIED.
#     Approvals tab gains a second `<div class="panel">` below the
#     existing per-agent tool-call queue. New section header
#     "Forged proposals" + status badge + refresh button. List
#     container `#forged-proposals-list`.
#
#   frontend/js/forged-proposals.js  NEW.
#     Polls /skills/staged + /tools/staged/forged. Renders a row
#     per pending proposal: kind badge (skill/tool), name+version,
#     hash prefix, description preview, requires/steps/forged_at,
#     Install + Discard buttons. Install routes to /skills/install
#     or /tools/install; Discard to the matching DELETE endpoint.
#     B204-aware: when install rejects with the new
#     unknown_tools_referenced error, the toast message surfaces
#     the offending tool list + the engine's hint pointing at
#     llm_think.v1 as the general-purpose fallback.
#
#   frontend/js/app.js  MODIFIED.
#     Imports + boots forgedProposalsPanel alongside the existing
#     panel boot calls.
#
# Different governance shape from per-call tool-call approvals
# (per-artifact admission vs per-call dispatch) — kept in its own
# section per Alex's directive 2026-05-09 to preserve legibility.
#
# What we deliberately did NOT do:
#   - "Install anyway" button for B204 unknown-tools rejections.
#     Operators can pass force_unknown_tools=true via direct API
#     call; surfacing it in the UI is a separate UX call (it's
#     the kind of operation that should require explicit choice,
#     not a single-click button).
#   - Live-reload polling (auto-refresh every N seconds). Tab
#     activation refresh is sufficient for the typical "I just
#     forged something, let me find it" flow.
#   - Per-row manifest preview expansion. Click rows to expand
#     the full manifest YAML — deferred. Operator who needs the
#     full manifest can read it from staged_path on disk for now.
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: zero ABI changes — pure frontend addition,
#                  reads existing endpoints, no schema changes.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add frontend/index.html \
        frontend/js/forged-proposals.js \
        frontend/js/app.js \
        dev-tools/commit-bursts/commit-burst205-forged-proposals-subsection.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(approvals): Forged proposals subsection in Approvals tab (B205)

Burst 205. Closes the deferred-twice item from B201 (Skill Forge UI)
and B202 (Tool Forge UI). Both bursts skipped this subsection
calling it a 'UX nice-to-have' because the modals handle
install/discard inline. True for proposals you forge + act on in
one sitting; breaks down for proposals from a previous session,
agent-driven cycle proposals where there's no modal, or batch
review.

Approvals tab now has a second panel below the per-agent tool-call
queue. Polls /skills/staged + /tools/staged/forged. Per-row
actions: Install (routes to /skills/install or /tools/install)
and Discard (matching DELETE endpoint). Refresh on tab
activation. Status badge shows count breakdown.

B204-aware: the install path may now return 422
unknown_tools_referenced with structured detail. The toast surfaces
the offending tool list + the engine's hint pointing at
llm_think.v1 as the general-purpose fallback, so operators see
why install failed and what to fix.

Different governance shape from per-call tool-call approvals
(per-artifact admission vs per-call dispatch) — kept in its own
section per Alex 2026-05-09 to preserve legibility.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: zero ABI changes — pure frontend addition that
                 reads existing endpoints."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 205 complete ==="
echo "=== Forged proposals subsection live in Approvals tab. ==="
echo "Press any key to close."
read -n 1
