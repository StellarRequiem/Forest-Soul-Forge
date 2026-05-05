#!/usr/bin/env bash
# Burst 48: v0.2 close planning doc.
#
# docs/roadmap/2026-05-01-v0.2-close-plan.md decomposes remaining
# v0.2 work into 16 concrete Bursts (49-64). Lays out three path
# options (A/B/C) with sequencing recommendations and sign-off
# questions. Pure planning doc; no code, no tests changed.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 48 — v0.2 close planning doc ==="
echo
clean_locks
git add docs/roadmap/2026-05-01-v0.2-close-plan.md \
        commit-burst48.command
clean_locks
git status --short
echo
clean_locks
git commit -m "Burst 48: v0.2 close plan — decompose remaining work into Bursts

docs/roadmap/2026-05-01-v0.2-close-plan.md.

Reviews v0.1.2-shipped surface (SarahR1 absorption complete; per-
tool annotations from Burst 46; v0.3 ADR drafts from Burst 47).
Identifies remaining v0.2 work and decomposes into concrete Bursts
49-64 sized to fit individual sessions.

Three path options for v0.2 sequencing:

A) v0.2 with all v0.3 ADR prep groundwork. Bursts 49 (annotations
   continuation) -> 50/51/52 (Verifier role + flag tool +
   telemetry table) -> 53-62 (Phase G.1.A programming primitives,
   one tool per Burst) -> 63 (docs refresh) -> 64 (release).

B) v0.2 minimal (skip v0.3 prep). Bursts 49 -> 53-62 -> 63 -> 64.
   ADR-0035/0036/0037 stay entirely Proposed; v0.3 absorption is
   its own arc later.

C) Defer Phase G to v0.3 entirely. Bursts 49 -> 63 -> 64. v0.2
   captures SarahR1 absorption + per-tool annotations only.

Recommendation: Path B. Phase G.1.A is meaningful operator-visible
feature work (SW-track agents can lint, run tests, read git
history); v0.3 ADR implementation is properly its own absorption
arc, not v0.2 scope.

Estimated total v0.2 close cost via Path B: 13-15 sessions over
3-4 weeks of sustained pace.

Document explicitly captures what v0.2 does NOT include (G.1.B
web reach, G.1.C blue team, G.1.D red team, ADR-0035/0036/0037
T2+ implementation tranches, etc.) so future contributors don't
expand scope beyond the close-path.

No code changes. No test changes. Test count unchanged (1577).
Pure planning artifact, sized at ~280 lines."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 48 landed. v0.2 close plan filed; awaiting Path A/B/C decision."
echo ""
read -rp "Press Enter to close..."
