#!/bin/bash
# Burst 357 - ADR-0079 T6: umbrella runner + operator runbook.
# CLOSES ADR-0079.
#
# Two artifacts complete the diagnostic harness arc:
#
# 1. dev-tools/diagnostic/diagnostic-all.command (NEW):
#    Umbrella that runs all 13 sections sequentially. Per
#    ADR-0079 D3 (fail loud per section but the umbrella runs
#    EVERY section regardless) — the operator wants the full
#    punch list, not the first failure. Writes:
#      - data/test-runs/diagnostic-all-<ts>/summary.md
#        (aggregated per-section status + consolidated FAIL list
#         + tally)
#      - data/test-runs/diagnostic-all-<ts>/section-NN-*.stdout.log
#        (per-section stdout)
#      - the per-section report.md files get refreshed by each
#        individual section invocation
#    Exits non-zero if any section FAILs or MISSING.
#
# 2. docs/runbooks/diagnostic-harness.md (NEW):
#    Operator runbook. Sections:
#      - at-a-glance section catalog
#      - when to run (Phase closures, release tags, "something
#        feels off" triage; explicitly NOT in CI)
#      - how to run (umbrella vs individual section)
#      - reading the summary (3-part structure)
#      - what each status means (PASS / FAIL / INFO / SKIP / MISSING)
#      - recovery for the 5 most common failure shapes:
#        daemon unreachable, degraded subsystems, B350-class
#        subsystem-not-wired, audit chain integrity break,
#        unmapped capabilities, tab API endpoint missing
#      - how to extend the harness (new section, new check,
#        new dispatcher subsystem discipline reminder)
#
# Final tranche summary:
#   T1 ADR doc                       (B351)
#   T2 sections 01-04 static         (B352)
#   T3 sections 05-07 runtime wiring (B354)  - B350 catch zone
#   T4 sections 08-10 integration    (B355)
#   T5 sections 11-13 system         (B356)
#   T6 umbrella + runbook            (B357 - THIS BURST)
# Total: ~6 bursts to close the harness, matching the ADR-0079
# estimate exactly.
#
# After this commit:
#   - Daemon at HEAD has the harness available
#   - Operator can run diagnostic-all.command at any time
#   - The 5 known open bugs from earlier in the session will
#     show up in the first real harness run as a punch list
#   - Original ADR-0064 T3 (telemetry chain hookup) becomes
#     resumable with substrate health known

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/diagnostic/diagnostic-all.command \
        docs/runbooks/diagnostic-harness.md \
        dev-tools/commit-bursts/commit-burst357-adr0079-t6-umbrella-runbook-close.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(diagnostic): ADR-0079 T6 - umbrella + runbook (CLOSE, B357)

Burst 357. Closes ADR-0079. Two artifacts:

dev-tools/diagnostic/diagnostic-all.command (NEW):
  Umbrella runs all 13 sections sequentially per ADR-0079 D3
  (fail loud per section, umbrella runs every section
  regardless - operator wants full punch list). Writes:
    data/test-runs/diagnostic-all-<ts>/summary.md
      (aggregated status + consolidated FAIL punch list + tally)
    data/test-runs/diagnostic-all-<ts>/section-NN-*.stdout.log
  Exits non-zero if any section FAILs or MISSING.

docs/runbooks/diagnostic-harness.md (NEW):
  Operator runbook. At-a-glance section catalog; when to run
  (Phase closures, release tags, suspicion triage; NOT in CI);
  how to run; reading the 3-part summary; what each status
  means; recovery for the 5 common failure shapes; how to
  extend the harness with discipline reminder for new
  dispatcher-owned ToolContext subsystems.

ADR-0079 arc closed:
  T1 ADR doc                       (B351)
  T2 sections 01-04 static         (B352)
  T3 sections 05-07 runtime wiring (B354) - B350 catch zone
  T4 sections 08-10 integration    (B355)
  T5 sections 11-13 system         (B356)
  T6 umbrella + runbook            (B357 - THIS)

6 bursts total, matches ADR-0079 estimate exactly. Hardened
substrate carry-over from D3 Phase A + D4 advanced rollout.

After this commit, ADR-0064 T3 (telemetry chain hookup) becomes
resumable with substrate health known via the harness."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 357 complete - ADR-0079 CLOSED ==="
echo "=========================================================="
echo "ADR-0079 = 7 bursts (B351-B357). Diagnostic harness live."
echo ""
echo "Try it: dev-tools/diagnostic/diagnostic-all.command"
echo "Reports land at: data/test-runs/diagnostic-all-<ts>/summary.md"
echo ""
echo "Next session: re-eval queue (ADR-0064 T3 telemetry chain"
echo "hookup, inline fixes for the 5 known bugs the harness will"
echo "surface, or new direction)."
echo ""
echo "Press any key to close."
read -n 1 || true
