#!/bin/bash
# Burst 215 — STATE.md follow-up post-B214 swarm re-acceptance.
#
# B213 refreshed STATE.md but B214 (which fired in the same session)
# changed two of B213's claims:
#   1. "Zero blue-team agents currently alive" — now all 9 are alive.
#   2. "26 installed skills" implicit only in head of section — was 2.
#
# B215 is a tiny correctness fix:
#   - Alive agents row: 14 -> 23, no-blue-team claim removed and
#     replaced with the full 9-agent list + the seq range of the
#     re-acceptance evidence.
#   - Installed skills row: 2 -> 26 (the canonical ADR-0033 set
#     was reloaded during the smoke).
#   - Split "Installed forged artifacts" into "Installed skills"
#     and "Installed forged tools" since the skill count is now
#     dominated by reloaded swarm skills rather than forged.
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: pure documentation refresh.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add STATE.md \
        dev-tools/commit-bursts/commit-burst215-state-md-post-swarm.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(state): STATE.md post-B214 swarm re-acceptance follow-up (B215)

B213 refreshed STATE.md but B214 (same session) invalidated two of
B213's claims:
  - 'Zero blue-team agents currently alive' is now false; all 9
    swarm agents born + 3 agent_delegated hops at seqs 7612/7621/7630.
  - Installed skills 2 -> 26 (ADR-0033 canonical set reloaded
    during the re-acceptance smoke).

Pure docs fix.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: pure documentation refresh."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 215 complete ==="
echo "Press any key to close."
read -n 1
