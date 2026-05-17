#!/bin/bash
# Burst 356 - ADR-0079 T5: diagnostic sections 11-13.
#
# Completes substrate coverage of ADR-0079's section catalog. T6
# (next) ships the umbrella runner + operator runbook to close
# the harness arc.
#
# 1. section-11-memory-retention.command (MVP):
#    a. /agents/{id}/memory returns 200 + list shape for a
#       sample alive agent (per-agent memory readable)
#    b. /consolidation/status mounted (ADR-0074 substrate wired)
#    c. /scheduler/status mounted (ADR-0075 substrate wired;
#       retention sweeps run via scheduler)
#    Full per-scope writeability + destructive retention sweep
#    test deferred (needs scratch sqlite for safety).
#
# 2. section-12-encryption-at-rest.command:
#    a. encryption_at_rest startup_diagnostic reports ok OR off
#       (off = explicit operator opt-out, not a failure)
#    b. secrets_backend startup_diagnostic resolves
#    c. If encryption on, sample master key resolves
#    Full round-trip (write/read/re-encrypt) deferred — destructive.
#
# 3. section-13-frontend-integration.command (MVP):
#    Per-tab API endpoint reachability checklist:
#      Agents/Skills/Tools/Marketplace/Pending/Orchestrator =
#        REQUIRED (404 = FAIL)
#      Provenance/Scheduler/Conversations =
#        OPTIONAL (404 = INFO, tab not yet shipped)
#    Catches the Marketplace boot-race (B276/B298) class without
#    needing a browser driver. Real browser-driven check (open
#    tab, screenshot, check stuck-loading) deferred — needs
#    Chrome MCP or Playwright.
#
# Expected first-run findings (substrate from prior bursts):
#   - 11: PASS memory readable; likely PASS or INFO on
#     consolidation/scheduler depending on which router mounts
#   - 12: PASS or INFO encryption status (default off per CLAUDE.md)
#   - 13: PASS for the 6 required tabs if daemon is responsive

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/diagnostic/section-11-memory-retention.command \
        dev-tools/diagnostic/section-12-encryption-at-rest.command \
        dev-tools/diagnostic/section-13-frontend-integration.command \
        dev-tools/commit-bursts/commit-burst356-adr0079-t5-sections-11-13.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(diagnostic): ADR-0079 T5 - sections 11-13 (B356)

Burst 356. Completes substrate coverage. T6 ships umbrella +
runbook to close ADR-0079.

  11 memory-retention      MVP: /agents/{id}/memory returns 200
                           + list shape; /consolidation/status
                           and /scheduler/status mounted.
                           Destructive retention sweep deferred
                           (needs scratch sqlite for safety).

  12 encryption-at-rest    encryption_at_rest startup_diagnostic
                           reports ok OR off (opt-out); secrets_
                           backend resolves; master key resolvable
                           if encryption is on. Round-trip deferred
                           (destructive against live registry).

  13 frontend-integration  MVP: per-tab API endpoint reachability
                           checklist. Required tabs (Agents,
                           Skills, Tools, Marketplace, Pending,
                           Orchestrator) FAIL on 404; optional
                           tabs (Provenance, Scheduler,
                           Conversations) report INFO on 404.
                           Catches Marketplace boot-race class
                           without browser driver. Real browser-
                           driven check deferred (needs Chrome
                           MCP or Playwright).

Substrate coverage complete:
  T2: 01 static-config / 02 skill-manifests / 03 boot-health / 04 tool-registration
  T3: 05 agent-inventory / 06 ctx-wiring (B350-class) / 07 skill-smoke
  T4: 08 audit-chain-forensics / 09 handoff-routing / 10 cross-domain-orchestration
  T5: 11 memory-retention / 12 encryption-at-rest / 13 frontend-integration

Next: B357 - T6 umbrella runner (diagnostic-all.command) +
operator runbook. CLOSES ADR-0079."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 356 complete - sections 11-13 shipped ==="
echo "Next: B357 - T6 umbrella + runbook (CLOSES ADR-0079)."
echo ""
echo "Press any key to close."
read -n 1 || true
