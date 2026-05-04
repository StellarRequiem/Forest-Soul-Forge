#!/usr/bin/env bash
# Burst 96: v0.5 planning doc.
#
# Post-v0.4.0 milestone checkpoint. ADR-0041 is feature-complete;
# the natural pause point before committing to v0.5 direction.
#
# Filed: docs/roadmap/2026-05-04-v0.5-planning.md
#
# Frames the 5 strategic decisions awaiting orchestrator input
# (multi-user direction, customer/vertical thesis, mobile platform,
# free-tier policy, repo branding). For each: the question, the
# options I see, my recommendation with reasoning, and what's
# blocked until the decision is made.
#
# Plus the technical backlog ranked by leverage:
#   1. Live verify v0.4.0 against the running daemon
#   2. Frontend polish queue (4 items)
#   3. Diagnostics dashboard
#   4. Frontend test suite (zero today)
#   5. Observability layer (structured logging)
#   6. README + onboarding
#
# Plus two recommended sequences: "keep moving without resolving
# strategic decisions" (Bursts 97-100) and "resolve strategic
# decisions first" (start with Decisions 1 + 2).
#
# Same shape as the Burst 48 v0.2-close planning doc that proved
# useful then. The Burst 87 roadmap is the v0.4 baseline; this doc
# is the v0.5 baseline.
#
# Decision needed from orchestrator: pick one of the two sequences,
# or propose a third.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 96 — v0.5 planning doc ==="
echo
clean_locks
git add docs/roadmap/2026-05-04-v0.5-planning.md
git add commit-burst96-v0.5-planning.command
clean_locks
git status --short
echo
clean_locks
git commit -m "docs(roadmap): v0.5 planning — strategic decisions + technical backlog

Post-v0.4.0 milestone checkpoint. ADR-0041 Set-and-Forget
Orchestrator is feature-complete; this doc frames what's next.

5 strategic decisions awaiting orchestrator input — each as a
question + options + recommendation + what's blocked:

1. Multi-user direction: cloud relay vs Tauri installer.
   Recommendation: Tauri installer. Aligns with local-first
   thesis; lowest distribution surface; reversible.
2. Customer/vertical thesis: regulated vs SMB/prosumer vs
   internal-tooling consultancies. Recommendation: SMB first.
   Lowest sales friction; v0.4 already has the right shape.
3. Mobile platform: Tauri 2.0 mobile vs React Native vs
   PWA-first. Recommendation: PWA-first then Tauri 2.0 if real
   demand surfaces.
4. Free-tier policy: local-only-free vs trial vs open-core.
   Recommendation: local-only is free forever. Aligns with
   local-first; removes trial-expiration surface.
5. Repo branding: single repo vs forge+harness split vs rename.
   Recommendation: single repo for v0.5; revisit at v1.0.

Technical backlog (orchestrator-independent), ranked by leverage:
- Live verify v0.4.0 against running daemon (15min, ship-ready)
- Frontend polish queue (4 open items from Burst 87)
- Diagnostics dashboard (latency p50/p95/p99)
- Frontend test suite (zero coverage today)
- Observability layer (structured logging)
- README + onboarding refresh

Two recommended sequences:
A. Keep moving without resolving strategic decisions:
   Burst 97 live-verify, 98 chat live-stream, 99 chat
   pagination, 100 diagnostics groundwork.
B. Resolve strategic decisions first: Decisions 1 + 2 unblock
   everything else.

Decision needed from orchestrator: pick A, B, or propose a third."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 96 landed. v0.5 planning doc filed."
echo "Strategic decisions + technical backlog framed; awaiting orchestrator input."
echo ""
read -rp "Press Enter to close..."
