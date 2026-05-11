#!/bin/bash
# Burst 226 — marketplace roadmap doc.
#
# ADR-0055 expanded yesterday (B224) to D1-D11 + M1-M10
# implementation tranches. This roadmap sequences those tranches
# into delivery phases with explicit dependency edges, splits
# work between kernel and sibling repo, and surfaces five open
# decisions that must resolve before specific tranches can
# advance.
#
# Phases:
#   A — Working substrate (kernel + minimal sibling repo)
#       ~5 kernel bursts + 0.5 sibling-repo day. Closes the
#       browse-install-grant loop end-to-end with one entry.
#   B — Community signal (reviews + auditability)
#       ~4 kernel bursts + 1 sibling-repo day. Reviews + commit
#       pinning + change-log surfacing + staleness flagging.
#   C — Telemetric scores
#       ~3 kernel bursts + 2 sibling-repo days + telemetry
#       endpoint stand-up. Skills get objective success-rate
#       scores alongside subjective stars.
#   D — Agent templates
#       ~4 kernel bursts + 1 sibling-repo day. Templates as
#       first-class marketplace items + Clone-this-agent.
#
# Total: ~16 kernel bursts + ~4.5 sibling-repo days + 2
# external infra pieces (maintainer keypair, telemetry endpoint).
#
# Five open decisions enumerated:
#   1. Sibling repo owner identity (blocks M2)
#   2. Maintainer ed25519 keypair (blocks M6)
#   3. Telemetry endpoint host — Cloudflare Worker / Vercel /
#      lightweight VPS / GitHub-PR-based (blocks M8)
#   4. Browse pane placement — recommend new top-level tab
#   5. Reviewer trust model bootstrap — default permissive
#
# Critical path: Phase A is the gate. B/C/D run parallel after.
#
# Risks called out:
#   - Sibling-repo content bottleneck (1 plugin isn't enough)
#   - Telemetry adoption (opt-in default)
#   - Review gaming (CI is the chokepoint)
#   - Template DNA boundary confusion (UX copy matters)
#
# What's deferred to post-MVP:
#   - Multi-registry federation
#   - Rich-media plugin pages
#   - In-kernel review submission UI
#   - Marketplace uninstall (already covered by B212)
#
# Recommended first burst when the marketplace track starts:
# Phase A1 (sibling repo scaffold). Needs Open Decision 1
# resolved first.
#
# Per ADR-0001 D2: roadmap explicitly preserves identity
#                  invariance. Templates produce new DNA;
#                  installs don't mutate constitution_hash.
# Per ADR-0044 D3: pure planning doc; zero code/ABI changes.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/roadmap/2026-05-11-marketplace-roadmap.md \
        dev-tools/commit-bursts/commit-burst226-marketplace-roadmap.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(roadmap): marketplace phased rollout plan (B226)

Burst 226. Sequences ADR-0055 M1-M10 into four phases with
dependency edges, kernel vs sibling-repo splits, and five open
decisions called out.

Phases:
  A — Working substrate (5 kernel + 0.5 sibling)
  B — Community signal: reviews + auditability (4 + 1)
  C — Telemetric scores: opt-in batched telemetry (3 + 2 + ext)
  D — Agent templates + clone-this-agent (4 + 1)

Total: ~16 kernel bursts, ~4.5 sibling-repo days, 2 external
infra pieces (maintainer keypair, telemetry endpoint).

Phase A is the critical-path gate; B/C/D run parallel after.
Recommended first burst when starting: scaffold the
forest-marketplace sibling repo with soulux-computer-control as
the first entry. Blocked on Open Decision 1 (repo owner identity).

Risks documented:
  - Sibling-repo content bottleneck
  - Telemetry adoption (opt-in default)
  - Review gaming via key generation
  - Template DNA boundary confusion in UX

Per ADR-0001 D2: identity invariance preserved.
Per ADR-0044 D3: pure planning doc."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 226 complete ==="
echo "=== Marketplace roadmap committed. Phase A unblocking action: pick sibling-repo owner. ==="
echo "Press any key to close."
read -n 1
