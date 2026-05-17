#!/bin/bash
# Burst 360 - section-13: full 15-tab coverage + correct two
# wrong-URL probes. Combines what was planned as B360 + B373 into
# a single coherent commit (same scope: rewriting section-13's
# TAB_ENDPOINTS list).
#
# Two bug shapes (surfaced by diagnostic-all on 2026-05-17):
#
#   Bug 1 - section 13 only covered 9 tabs; frontend has 15:
#     The MVP probe shipped with B356 covered Agents, Skills, Tools,
#     Marketplace, Pending, Orchestrator, Provenance, Scheduler,
#     Conversations - 9 of 15. The 6 missing tabs (Audit, Memory,
#     Reality Anchor, Security, Operator Wizard, Voice, plus Forge
#     which is the agent-birth form) had ZERO API-level coverage.
#     The harness could report all green while those tabs were
#     completely broken.
#
#   Bug 2 - two probed URLs didn't exist:
#     /skills/staged/forged - the probe assumed symmetry with
#       /tools/staged/forged (which DOES exist via tools_forge.py).
#       Skills don't have the /forged suffix - the frontend's
#       marketplace.js fetches /skills/staged + /marketplace/index,
#       never /skills/staged/forged.
#     /pending_calls - the probe assumed a collection endpoint; the
#       router only exposes per-agent /agents/{id}/pending_calls.
#       The Pending tab fetches per-agent, not collection.
#     Both bugs trivially fail every probe run even on a clean
#     substrate.
#
# Fix shape:
#
#   - Extend TAB_ENDPOINTS from 9 to 15 entries matching the
#     real index.html tab inventory.
#   - For Pending + Memory which need an agent context, the probe
#     fetches /agents?limit=1 first, samples the first active
#     agent's instance_id, and substitutes it into the per-agent
#     template. If no active agent exists, those tabs degrade to
#     INFO rather than FAIL (no agent = no per-agent route shape
#     to verify, but not a substrate bug).
#   - Forge depends on /traits + /genres + /tools/catalog per the
#     app.js boot contract (the trait-tree-failure branch boots
#     a degraded mode); all three are required.
#   - Reality Anchor + Security + Voice + Operator Wizard + Chat
#     marked optional (their substrates were added later; some
#     deployments may not have them; the harness shouldn't FAIL
#     for them missing - INFO is correct).
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm:
#     - 6 tabs invisible to harness; section 13 can report green
#       while those tabs are dead at the API.
#     - 2 probed routes always 404 - blocks the daily report from
#       ever showing green on this section.
#   Prove non-load-bearing: pure probe extension/correction; no
#     substrate change. Required vs. optional classification is
#     additive.
#   Prove alternative is strictly better: leaving in place means
#     the section 13 column is permanently red AND covers less
#     than half the tabs. Replacing the probe surfaces real bugs
#     in the 6 newly-covered tabs.
#
# Verification after this commit lands:
#   1. Re-run section-13-frontend-integration.command - report
#      shows 15 tabs (was 9). The two wrong-URL FAILs are gone.
#      Real bugs in the newly-covered tabs may surface.
#   2. diagnostic-all.command - section 13's tally jumps from
#      9 to 15.
#
# This commit creates room for B366 (browser-driven smoke) and
# ADR-0080 (per-agent capability tree UI) to build on a known-
# good tab-vs-endpoint map.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/diagnostic/section-13-frontend-integration.command \
        dev-tools/commit-bursts/commit-burst360-section-13-fifteen-tab-coverage.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(harness): section-13 full 15-tab coverage + URL fixes (B360)

Burst 360. Combines B360 + B373. Section 13 previously covered
9 of 15 frontend tabs and probed two URLs that didn't exist.

Six missing tabs added: Audit, Memory, Reality Anchor, Security,
Operator Wizard, Voice. Forge added (it depends on /traits +
/genres + /tools/catalog per the app.js trait-tree-failure boot
branch).

Two corrected URLs:
  /skills/staged/forged removed - that route never existed;
    Marketplace's frontend fetches /skills/staged +
    /marketplace/index.
  /pending_calls removed - it's per-agent only; Pending tab
    fetches /agents/{id}/pending_calls.

For per-agent tabs (Pending, Memory), the probe fetches
/agents?limit=1, samples the first active agent's instance_id,
and substitutes into the path template. No active agent ->
degrades to INFO (per-agent shape unverifiable but not a
substrate bug).

Optional vs. required classification:
  Required: Agents, Forge, Skills, Tool Registry, Audit,
    Marketplace, Pending, Memory, Orchestrator.
  Optional: Provenance, Reality Anchor, Security, Operator
    Wizard, Voice, Chat. Their substrates landed later and
    some deployments may not have them; INFO not FAIL on 404.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: 6 tabs invisible; 2 probes always 404 on clean
    substrate.
  Prove non-load-bearing: pure probe correction.
  Prove alternative is better: leaving in place means section
    13 permanently red on less than half the tabs.

After this lands:
  - section 13 reports 15 tabs (was 9).
  - the two wrong-URL FAILs gone.
  - real bugs in the newly-covered tabs may surface for triage.

Creates room for B366 (browser smoke) and ADR-0080 (per-agent
capability tree) to build on a known-good tab-endpoint map."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 360 complete - section 13 full coverage ==="
echo "=========================================================="
echo "Re-test: dev-tools/diagnostic/section-13-frontend-integration.command"
echo "Expected: 15 tabs probed; two known-wrong-URL FAILs gone;"
echo "any remaining FAILs are real bugs in newly-covered tabs."
echo ""
echo "Press any key to close."
read -n 1 || true
