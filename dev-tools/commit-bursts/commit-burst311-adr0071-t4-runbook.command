#!/bin/bash
# Burst 311 - ADR-0071 T4: plugin author runbook + publishing guide.
#
# Closes ADR-0071 4/4. Pure-docs burst tying together every plugin-
# authoring tranche from this arc:
#
#   T1 (B289) plugin-new scaffold
#   T2 (B305) tier-specific tool exemplars
#   T3 (B310) fsf plugin-adapt MCP wrapper
#   T4 (this burst) end-to-end runbook
#
# What ships:
#
# 1. docs/runbooks/plugin-authoring.md — operator runbook covering:
#    - The two authoring paths (plugin-new vs plugin-adapt) with
#      a when-to-use comparison table
#    - Path 1: scaffold → implement validate/execute → test → install
#    - Path 2: pull upstream → scaffold wrapper → compute sha256
#      → install
#    - ADR-0043 manifest reference (minimal yaml + per-field
#      explanation)
#    - Side-effects tiers table with examples + per-call approval
#      defaults
#    - requires_human_approval semantics + when to override per-tool
#    - required_secrets + ADR-0052 secrets backend integration
#    - Marketplace publishing flow (registry index.yaml shape +
#      submission process)
#    - Out-of-scope clarifier (MCP protocol details, Forest
#      internals, upstream server authoring all live elsewhere)
#    - Common gotchas: sha256 forgetting, tier-too-low at runtime,
#      naming collisions, mutable upstream behavior
#
# No code changes. No new tests. The runbook references the
# existing test files + examples/plugins/ canonical samples that
# already cover the relevant assertions.
#
# === ADR-0071 CLOSED 4/4 ===
# Plugin author + adapter kit complete. Phase alpha scorecard:
# 6/10 closed (ADR-0050, ADR-0067, ADR-0071, ADR-0073, ADR-0074,
# ADR-0075).

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/runbooks/plugin-authoring.md \
        dev-tools/commit-bursts/commit-burst311-adr0071-t4-runbook.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(plugins): ADR-0071 T4 - plugin author runbook (B311) — ARC CLOSED 4/4

Burst 311. Pure-docs burst closing ADR-0071. Operator runbook
tying together every plugin-authoring tranche from this arc
(T1 plugin-new B289, T2 tier exemplars B305, T3 plugin-adapt
B310) into one end-to-end workflow doc.

What ships:

  - docs/runbooks/plugin-authoring.md: covers both authoring
    paths (plugin-new vs plugin-adapt) with a when-to-use
    comparison table, walks each path scaffold-to-install,
    documents the ADR-0043 manifest reference (minimal yaml +
    per-field semantics), side-effects tiers table with per-call
    approval defaults, requires_human_approval override
    semantics, required_secrets + ADR-0052 secrets backend
    integration, marketplace publishing flow (registry index.yaml
    shape), explicit out-of-scope list (MCP protocol details,
    Forest internals, upstream-server authoring), and four
    common gotchas (sha256 forgetting, tier-too-low at runtime,
    capability naming collisions, mutable upstream behavior).

No code changes. Runbook references the existing test files +
examples/plugins/ canonical samples for the assertions.

=== ADR-0071 CLOSED 4/4 ===
Phase alpha scorecard: 6/10 closed (ADR-0050, ADR-0067,
ADR-0071, ADR-0073, ADR-0074, ADR-0075). Substrate-only:
ADR-0068, ADR-0070 (3/8), ADR-0076. Partial: ADR-0072 (2/5)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 311 complete - ADR-0071 CLOSED 4/4 ==="
echo "Phase alpha: 6/10 scale ADRs closed."
echo ""
echo "Press any key to close."
read -n 1
