#!/bin/bash
# Burst 265 — ADR-0051 T8: per-tool sandbox operator runbook.
# Closes ADR-0051 end-to-end.
#
# New: docs/runbooks/tool-sandbox.md
#
# Contents:
#   - What ADR-0051 ships + trusted-host context
#   - The three modes (off/strict/permissive) + when to use each
#   - Per-platform setup (macOS sandbox-exec built-in; Linux apt/
#     dnf/pacman install bubblewrap; Windows not supported v1)
#   - Audit-chain field reference (sandbox_mode, sandbox_used,
#     sandbox_violation, sandbox_error_kind, sandbox_stderr,
#     sandbox_skipped_reason)
#   - Why memory_*/delegate/llm_think opt out + the drift detectors
#   - Monitoring playbook: jq queries to see "is it firing?",
#     "did it block anything?", "is permissive papering over real
#     failures?"
#   - CVE response: patch OS, restart daemon, optionally
#     mid-incident switch to permissive
#   - Troubleshooting: setup_failed sub-classes,
#     result_unpickle_failed, user-namespaces unavailable on Linux
#   - 3-step "is it working?" smoke procedure operators can run
#   - What this ADR does NOT do (operationally important caveats)
#   - References back to the implementation files
#
# Pure documentation. No code changes. Zero risk of regression.
# Tests unchanged. After this push, ADR-0051 is shipped
# end-to-end: T1 substrate, T2 Linux bwrap, T3 catalog opt-outs,
# T4 dispatcher routing, T5 profile from constitution, T6 audit
# fields, T7 permissive fallback, T8 runbook.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/runbooks/tool-sandbox.md \
        dev-tools/commit-bursts/commit-burst265-adr0051-t8-runbook.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs: ADR-0051 T8 — tool-sandbox operator runbook (B265)

Burst 265. Closes ADR-0051 end-to-end with the operator-facing
runbook at docs/runbooks/tool-sandbox.md.

Sections:
- What ADR-0051 ships + the trusted-host context it extends
- Three modes (off/strict/permissive) + when to use each
- Per-platform setup (macOS sandbox-exec built-in; Linux apt/dnf/
  pacman install bubblewrap; Windows not supported in v1)
- Audit-chain field reference covering all 6 additive
  sandbox_* fields the dispatcher emits per ADR Decision 6
- Why memory_*/delegate/llm_think opt out + the drift detectors
  in test_tool_catalog.py
- Monitoring playbook: 3 jq queries operators run regularly:
  - Is the sandbox actually firing (sandbox_used: true)?
  - Did it block anything (sandbox_violation: true on failed)?
  - Is permissive papering over real failures
    (sandbox_skipped_reason: setup_failed_permissive_fallback)?
- CVE response: patch OS, restart daemon, optionally switch
  permissive mid-incident
- Troubleshooting matrix for each setup_failed sub-class +
  result_unpickle_failed + user-namespaces-unavailable on Linux
- 3-step 'is it working?' smoke (sandbox-eligible succeeds with
  sandbox_used=true; sandbox-ineligible runs in-process with
  skipped_reason=ineligible; deliberate violation refuses with
  sandbox_violation=true)
- 'What this ADR does NOT do' for operational expectation-setting
- References back to every implementation file

Pure documentation. Zero code change. ADR-0051 status:
T1 ✓ B261 (substrate)
T2 ✓ B262 (Linux bwrap)
T3 ✓ B263 (catalog opt-outs)
T4-T7 ✓ B264 (dispatcher + profile + audit + permissive fallback)
T8 ✓ B265 (this burst — runbook)"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 265 complete — ADR-0051 SHIPPED END-TO-END ==="
echo "Press any key to close."
read -n 1
