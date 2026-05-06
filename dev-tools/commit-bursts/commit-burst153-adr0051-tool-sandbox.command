#!/bin/bash
# Burst 153 — T29 ADR for per-tool subprocess sandbox.
#
# Phase 4 item #5 (final) of the security-hardening arc. The
# 2026-05-05 outside security review flagged: "tools run in the
# daemon's process. No OS-level sandbox. A compromised LLM tool
# can do real damage if you approve it."
#
# ADR-0051 closes that with opt-in per-tool subprocess sandboxing
# (macOS sandbox-exec / Linux bwrap). Default off (trusted-host
# model preserved); operators opt in via FSF_TOOL_SANDBOX={off,
# strict,permissive}. Largest implementation of the four Phase-4
# ADRs (7-10 bursts).
#
# Pure design ADR. Implementation queued in 8 tranches T1-T8.
#
# Decisions locked:
#   1. Opt-in (default off). Three modes: off / strict / permissive.
#      Matches Forest's pattern of "substrate optional, opt-in via
#      env var" (ADR-0042 T5, ADR-0043 grants, ADR-0045 posture,
#      ADR-0049 KeyStore).
#   2. Subprocess + platform-specific sandbox: sandbox-exec on
#      macOS, bwrap on Linux, Windows not in v1.
#   3. Tool-by-tool eligibility: sandbox_eligible flag in catalog.
#      Most tools (read_only, shell, file, web) are eligible. Memory,
#      delegate, llm_think are NOT (need direct daemon state).
#   4. Profile generation: mechanical, derived from constitution
#      allowlists + tool side_effects. Regenerated per call to
#      respect runtime grants/posture changes.
#   5. Sandbox metadata in audit chain via existing event types
#      (additive event_data fields). New: sandbox_mode, sandbox_used,
#      sandbox_violation, sandbox_setup_failed.
#   6. Schema additive: optional sandbox_eligible field on catalog
#      entries; optional event_data fields; FSF_TOOL_SANDBOX env
#      var. Per ADR-0044 D3 — kernel-compatible.
#
# What ships:
#
#   docs/decisions/ADR-0051-per-tool-subprocess-sandbox.md (~370 lines)
#     - 6 decisions + tradeoffs
#     - 8 implementation tranches
#     - Tool eligibility classification table
#     - Profile-from-constitution mapping
#     - Cross-references to ADR-0019/0021/0025/0033/0042/0043/0044/
#       0045/0049/0050
#
# Implementation queued (7-10 bursts):
#   T1: Sandbox Protocol + macOS sandbox-exec impl
#   T2: Linux bwrap impl
#   T3: Tool catalog sandbox_eligible flag
#   T4: Dispatcher integration
#   T5: Profile generation from constitution
#   T6: Audit chain extension (event_data fields)
#   T7: Permissive-mode fallback
#   T8: Per-platform docs + runbook
#
# Closes T29 design phase. **Phase 4 (security hardening) DESIGN
# COMPLETE.** All four ADRs from the outside security review are
# now locked: T25 (auth, IMPL DONE), T26 (SBOM workflow, IMPL DONE),
# T27 (signatures, ADR ONLY), T28 (encryption at rest, ADR ONLY),
# T29 (sandbox, ADR ONLY).
#
# Implementation work for T27/T28/T29 totals ~22-27 bursts spread
# across multiple sessions. Substantial roadmap; design discipline
# from this session lets each tranche land cleanly without rework.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0051-per-tool-subprocess-sandbox.md \
        dev-tools/commit-bursts/commit-burst153-adr0051-tool-sandbox.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(adr): ADR-0051 — per-tool subprocess sandbox (B153)

Burst 153. Closes T29 design phase. Final Phase-4 ADR from the
2026-05-05 outside security review: 'tools run with daemon
privileges. No OS-level sandbox.'

ADR-0051 closes that with opt-in per-tool subprocess sandboxing
(macOS sandbox-exec / Linux bwrap). Default off — trusted-host
model preserved. Operators opt in via FSF_TOOL_SANDBOX={off,
strict,permissive}.

6 decisions locked:
1. Opt-in default off. Three modes: off / strict / permissive.
   Matches the optional-substrate pattern from ADR-0042/0043/
   0045/0049.
2. Subprocess + platform-specific sandbox: sandbox-exec on macOS,
   bwrap on Linux, Windows not in v1.
3. Tool eligibility flag (sandbox_eligible). Most tools eligible;
   memory/delegate/llm_think structurally can't be sandboxed
   (need daemon state).
4. Profile generated mechanically from constitution allowlists
   + tool side_effects. Regenerated per call (respects runtime
   grants / posture).
5. Sandbox metadata in audit chain via existing event types
   (additive event_data fields).
6. Schema additive: optional flags + event_data fields + env var.
   Kernel-compatible per ADR-0044 D3.

8 implementation tranches queued, 7-10 bursts total. Largest of
the four Phase-4 ADRs.

PHASE 4 DESIGN COMPLETE. All four security-hardening ADRs locked:
- T25 (auth required) — IMPL DONE B148+B149
- T26 (SBOM workflow) — IMPL DONE B150
- T27 (per-event signatures) — ADR done B151
- T28 (encryption at rest) — ADR done B152
- T29 (per-tool sandbox) — ADR done B153

Implementation roadmap for T27/T28/T29: ~22-27 bursts spread
across future sessions. The design discipline locked in tonight's
session lets each tranche land cleanly."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 153 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
