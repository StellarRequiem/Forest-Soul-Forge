#!/bin/bash
# Burst 117 — ADR-0044 Kernel Positioning + SoulUX Flagship Branding.
#
# Strategy + naming ADR opening the v0.6 arc. Files the direction;
# does NOT file the bursts that execute it. Light-touch rebrand by
# design — premature branding investment without external integrator
# validation is marketing theater.
#
# Locks four strategic decisions:
#
#   1. Forest is the kernel. The project's charter is governance +
#      identity + audit substrate that other agent OSes /
#      distributions can build on, not a polished end-user product
#      competing with agnt et al.
#
#   2. SoulUX is the flagship distribution. Tauri shell + frontend
#      bundled around Forest get promoted from "the Forest desktop
#      app" to "SoulUX — the flagship distribution that ships
#      Forest." Same relationship as Linux ↔ Ubuntu, Postgres ↔
#      Supabase. Repo + Python package + CLI names preserved at
#      v0.6 — heavier rebrand artifacts wait until external
#      integrator validates the kernel posture.
#
#   3. Lock the kernel ABI surfaces. v1.0 will commit to backward
#      compatibility on:
#        - Tool dispatch protocol (ToolDispatcher.dispatch +
#          mcp_call.v1)
#        - Audit chain JSONL schema + hash-linking + 70+ event
#          types' payload schemas
#        - plugin.yaml schema v1
#        - constitution.yaml schema
#        - HTTP API contract under /agents, /plugins, /tools, etc.
#        - fsf CLI subcommands + exit codes
#        - Strictly-additive registry SQLite migrations
#
#   4. External integrator is the load-bearing milestone. A kernel
#      becomes the kernel only when somebody else builds on it.
#      Recruit candidates: agnt-gg/agnt (most direct overlap), AIOS
#      (academic), or build a second internal distribution
#      (headless server) that exercises the kernel API without the
#      SoulUX shell. Until validation arrives the kernel claim is
#      aspirational.
#
# 7-phase roadmap toward defensible kernel posture:
#   Phase 1: Kernel/userspace boundary lock          3-5 bursts
#   Phase 2: Publish formal kernel API spec          2-3 bursts
#   Phase 3: Headless mode + SoulUX frontend split   3-5 bursts
#   Phase 4: Conformance test suite                  3-4 bursts
#   Phase 5: License posture + governance ADR        1-2 bursts
#   Phase 6: First external integrator               months
#   Phase 7: v1.0 with API stability commitment      1 burst
#
# Phases 1, 2, 5 are doable in the next ~10 bursts. Phase 6 is the
# load-bearing milestone.
#
# What this ADR does NOT do:
#   - Rename the repo.
#   - Rename the Python package or CLI.
#   - Publish the kernel ABI spec yet.
#   - Add a SoulUX build target.
#   - Pick a license posture.
#
# Each of those is a downstream burst; this ADR is the index.

set -euo pipefail

cd "$(dirname "$0")"

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0044-kernel-positioning-soulux.md

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(adr): ADR-0044 Kernel Positioning + SoulUX Flagship Branding

Strategy + naming ADR opening the v0.6 arc. Files the direction;
does NOT file the bursts that execute it. Light-touch rebrand by
design — premature branding investment without external integrator
validation is marketing theater.

Four strategic decisions locked:

1. Forest is the kernel. The project's charter is governance +
   identity + audit substrate that other agent OSes / distributions
   can build on, not a polished end-user product competing with
   agnt et al. Explicit list of what kernel will + will NOT do.

2. SoulUX is the flagship distribution. Tauri shell + frontend
   bundled around Forest get promoted from 'the Forest desktop
   app' to 'SoulUX — the flagship distribution that ships Forest.'
   Same relationship as Linux↔Ubuntu, Postgres↔Supabase. Repo +
   Python package + CLI names preserved at v0.6.

3. Lock the kernel ABI surfaces. v1.0 commits to backward
   compatibility on: ToolDispatcher.dispatch + mcp_call.v1,
   audit chain JSONL schema + 70+ event payload schemas,
   plugin.yaml schema v1, constitution.yaml schema, HTTP API
   contract under /agents, /plugins, /tools, etc., fsf CLI
   subcommands + exit codes, strictly-additive registry SQLite
   migrations.

4. External integrator is the load-bearing milestone. A kernel
   becomes the kernel only when somebody else builds on it.
   Recruit candidates: agnt-gg/agnt (most direct overlap), AIOS
   (academic), or a second internal distribution. Until external
   validation arrives the kernel claim is aspirational.

7-phase roadmap to defensible kernel posture:
  P1 kernel/userspace boundary  3-5 bursts
  P2 kernel API spec            2-3 bursts
  P3 headless + SoulUX split    3-5 bursts
  P4 conformance test suite     3-4 bursts
  P5 license + governance ADR   1-2 bursts (parallel)
  P6 first external integrator  months, not bursts
  P7 v1.0 stability commitment  1 burst

What this ADR does NOT do:
- Rename the repo / Python package / CLI.
- Publish the kernel ABI spec (P2).
- Add a SoulUX build target (P3).
- Pick a license posture (P5).
Each is a downstream burst; this ADR is the index.

Credit: kernel-positioning framing surfaced in a chat with Alex
2026-05-05 about how Forest's actual differentiation
(governance/identity/audit/coherence) maps to substrate-shape
positioning, while UI/UX competition with agnt et al. is a losing
race. SoulUX naming intuition came from the same conversation."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 117 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
