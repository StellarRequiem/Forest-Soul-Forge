#!/bin/bash
# Burst 183 — ADR-0055 — Agentic Marketplace (DESIGN).
#
# Operator directive (2026-05-06):
#   "i want skills and tools to be loaded like the matrix sort
#    of, prebuilt programs that give plug and play access via
#    a agentic marketplace"
#
# Sibling-repo design (operator chose option 2 of two options
# offered: in-Forest vs sibling). Most delivery lives in a new
# forest-marketplace repo; the kernel exposes only a read +
# install API surface and reuses the existing ADR-0043 plugin
# protocol for the install side.
#
# 7 implementation tranches queued (M1-M7):
#   M1 — kernel GET /marketplace/index endpoint
#   M2 — forest-marketplace sibling repo scaffold + v0.1
#        registry (B184)
#   M3 — kernel POST /marketplace/install + new
#        marketplace_plugin_installed audit event type
#   M4 — frontend Marketplace pane (browse + filter + install)
#   M5 — grant-to-agent flow (auto-derived trust_tier per
#        Decision 7)
#   M6 — ed25519 signing pipeline + untrusted-entry confirmation
#   M7 — operator ratings + reviews (DEFERRED — needs separate
#        design conversation about anonymity, gaming, off-by-
#        default)
#
# This burst ships ONLY the design doc (per the established
# pattern: ADR-0054 was B177 design → B178 first impl). The
# sibling repo scaffold + first kernel endpoint follow in
# subsequent bursts.
#
# What ships:
#
#   docs/decisions/ADR-0055-agentic-marketplace.md (NEW):
#     - Context: what's already built (ADR-0043 plugin
#       protocol, ADR-0030/0031 forge events, ADR-0052
#       secrets store, soulux-computer-control as a working
#       example) and what's missing (browse surface, central
#       index, capability search, recommendation, one-click
#       install + grant, trust chain).
#     - Decision 1: sibling repo (forest-marketplace) — keeps
#       marketplace policy independently versioned.
#     - Decision 2: decentralized registry with curated
#       default. Matches Cargo / npm convergence on
#       'central by default, decentralized by escape hatch.'
#     - Decision 3: marketplace manifest schema with mandatory
#       permissions_summary for operator-readable plain-
#       language descriptions.
#     - Decision 4: kernel API surface — exactly TWO new
#       endpoints (GET /marketplace/index, POST
#       /marketplace/install). All other concerns
#       (UI, registry, signing) live in the sibling repo.
#     - Decision 5: three-layer trust model (manifest
#       signature → payload SHA256 → plugin-internal
#       manifest verification per ADR-0043).
#     - Decision 6: capability search + role-fit
#       recommendation computed client-side (low-hundreds
#       scale; no FTS index needed).
#     - Decision 7: grant-to-agent flow uses existing
#       ADR-0043 follow-up #2 endpoint with auto-derived
#       trust_tier from highest_side_effect_tier.
#     - 7 implementation tranches (M1-M7) with M7 explicitly
#       deferred.
#     - Consequences: ADR-0001 D2 + ADR-0044 D3 invariance
#       verifications recorded so the next implementation
#       burst doesn't re-derive them.
#
# Per ADR-0044 D3: design-only commit. Zero code changes.
# Pre-M1 daemons unaffected.
#
# Per ADR-0001 D2: marketplace installs add NEW
# tools/skills/MCP servers to the dispatcher's runtime
# registry. They do NOT touch any existing agent's
# constitution_hash or DNA. Identity invariance preserved
# by design — verification chain recorded in the ADR's
# Consequences section.
#
# Pre-existing pause: ADR-0054 T5b chat-tab thumbs UI is
# deferred. The dormant LastShortcutOut Pydantic schema (added
# during T5b prep before the marketplace pivot) commits with
# this burst as additive substrate; it's unused until T5b
# resumes.
#
# Verification:
#   No code changes. Manual review of the ADR confirms:
#     - 7 numbered Decisions with explicit rationale
#     - 7 numbered tranches (M1-M7)
#     - Consequences section covers positive + negative
#     - Both invariance checks (ADR-0001 D2, ADR-0044 D3)
#       recorded explicitly
#
# Next burst: B184 — forest-marketplace sibling repo scaffold
# (M2 in ADR-0055's tranche list).

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0055-agentic-marketplace.md \
        src/forest_soul_forge/daemon/schemas/conversations.py \
        src/forest_soul_forge/daemon/schemas/__init__.py \
        dev-tools/commit-bursts/commit-burst183-adr0055-marketplace-design.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(adr): ADR-0055 — Agentic Marketplace (B183)

Burst 183. Design doc for the operator-driven marketplace
direction — sibling-repo build (forest-marketplace) consuming
the kernel's existing ADR-0043 plugin protocol with a thin
read+install API on the kernel side.

Operator directive (2026-05-06): 'i want skills and tools to
be loaded like the matrix sort of, prebuilt programs that
give plug and play access via a agentic marketplace.'

7 Decisions:
1. Sibling repo, not in-Forest — keeps marketplace policy
   independently versioned from the kernel governance core.
2. Decentralized registry with curated default — matches
   Cargo/npm convergence on 'central by default,
   decentralized by escape hatch.'
3. Marketplace manifest schema with mandatory
   permissions_summary (plain-language operator-readable
   description).
4. Kernel API surface limited to TWO endpoints
   (GET /marketplace/index, POST /marketplace/install) +
   one new audit event type. Everything else lives in the
   sibling repo.
5. Three-layer trust model: manifest ed25519 signature →
   payload SHA256 → plugin-internal manifest per ADR-0043.
6. Capability search + role-fit recommendation computed
   client-side (low-hundreds scale; no FTS index).
7. Grant-to-agent reuses existing ADR-0043 follow-up #2
   endpoint with auto-derived trust_tier.

7 implementation tranches:
- M1 GET /marketplace/index endpoint
- M2 forest-marketplace sibling repo scaffold + v0.1 registry
- M3 POST /marketplace/install + marketplace_plugin_installed
  event type
- M4 frontend Marketplace pane
- M5 grant-to-agent flow with auto-derived trust_tier
- M6 ed25519 signing pipeline + untrusted-entry confirmation
- M7 operator ratings + reviews (DEFERRED)

Per ADR-0001 D2: installs add tools/skills/mcp_servers to
the runtime registry only. constitution_hash + DNA stay
immutable per agent. Per-(agent, plugin) grants use
existing per-instance state path. Identity invariance
preserved.

Per ADR-0044 D3: two additive endpoints + one additive
audit event type. Pre-M3 daemons reading post-M3 chains
emit verification warnings on the new event type rather
than failing — same forward-compat posture as ADR-0054 T4.

Also commits the dormant LastShortcutOut Pydantic schema
(prep work done before pivot to marketplace; unused until
T5b chat-tab thumbs UI resumes).

Next: B184 — forest-marketplace sibling repo scaffold (M2)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 183 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
