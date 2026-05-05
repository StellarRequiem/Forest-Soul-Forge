#!/usr/bin/env bash
# Burst 97: ADR-0042 — v0.5 Product Direction.
#
# Locks the strategic decisions resolved in Burst 96 + the
# orchestrator's response, in the canonical decision-record
# format. Same shape as ADR-0033 (Security Swarm), ADR-0036
# (Verifier Loop), ADR-0041 (Set-and-Forget Orchestrator).
#
# Five decisions captured:
#   D1 Multi-user direction: Tauri installer (over cloud relay)
#   D2 Customer thesis: SMB / prosumer (over regulated, internal-tooling)
#   D3 Mobile platform: PWA-first → Tauri 2.0 mobile
#   D4 Free-tier policy: local-only is free forever
#   D5 Repo branding: single repo for v0.5; revisit at v1.0
#
# These reinforce one thesis: Forest Soul Forge is a sovereign,
# local-first agent foundry that ships as a desktop app for solo
# developers. v0.4 already has the right shape; v0.5 wraps it
# in a Tauri installer + Stripe checkout.
#
# 6-tranche execution plan:
#   T1 (this) — ADR filed
#   T2 (Burst 98) — Frontend responsive pass (PWA-first)
#   T3 (Bursts 99-100) — Tauri shell scaffolding
#   T4 (Burst 101) — Daemon-as-binary build (PyOxidizer/pyinstaller)
#   T5 (Bursts 102-103) — End-to-end signed .app + auto-updater
#   T6 (post-v0.5) — Pricing/landing page (out of repo scope)
#
# Open questions deferred to later ADRs:
#   - Binary build tool (PyOxidizer vs pyinstaller vs Nuitka)
#   - Code signing infra (Apple Developer + Windows cert costs)
#   - Auto-updater manifest hosting
#   - Pricing model details (paid tier composition)

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 97 — ADR-0042: v0.5 Product Direction ==="
echo
clean_locks
git add docs/decisions/ADR-0042-v0.5-product-direction.md
git add commit-burst97-adr0042.command
clean_locks
git status --short
echo
clean_locks
git commit -m "docs(adr): ADR-0042 v0.5 Product Direction

Locks the five strategic decisions resolved post-v0.4.0:

D1 Multi-user direction: Tauri installer. No cloud relay.
   Local-first stays sovereign; lowest distribution surface;
   reversible if multi-machine demand surfaces.

D2 Customer thesis: SMB / prosumer. Solo devs + small teams,
   \$10-30/mo, self-serve. Lowest sales friction; v0.4 already
   has the right shape. Regulated/internal-tooling deferred to
   v1.x.

D3 Mobile platform: PWA-first → Tauri 2.0 mobile. CSS
   responsive pass on the existing frontend; native shell only
   if real demand surfaces. Avoids React Native's doubled-
   codebase tax.

D4 Free-tier policy: local-only is free forever. No trial
   expiration on the daemon. Removes dark-pattern/billing-leak
   surface. Paid tier (when shipped) is opt-in additions on
   top of a permanently-free base.

D5 Repo branding: single repo for v0.5; revisit at v1.0.
   Multi-repo splits are easy later, hard to undo. The
   harness-app + harness-bridge split presupposed D1=relay
   (now rejected); harness-app reduces to apps/desktop/ in
   this repo.

These five decisions reinforce one coherent thesis: Forest
Soul Forge is a sovereign, local-first agent foundry that
ships as a desktop app for solo developers.

6-tranche plan:
  T1 (this burst) — ADR filed
  T2 (Burst 98) — Frontend responsive pass (PWA-first half of D3)
  T3 (Bursts 99-100) — Tauri shell scaffolding (apps/desktop/)
  T4 (Burst 101) — Daemon-as-binary build
  T5 (Bursts 102-103) — Signed .app + auto-updater
  T6 (post-v0.5) — Pricing/landing page (out of repo)

Open questions deferred to later ADRs:
  - Binary build tool (PyOxidizer vs pyinstaller vs Nuitka)
  - Code signing infrastructure cost + setup
  - Auto-updater manifest hosting (GitHub Releases vs CDN vs custom)
  - Pricing model details (paid tier composition)

References Burst 96 planning doc for the framing the
orchestrator made these decisions against."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 97 landed. ADR-0042 captures the v0.5 product direction."
echo "Next: Burst 98 — frontend responsive pass (T2)."
echo ""
read -rp "Press Enter to close..."
