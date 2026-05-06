#!/bin/bash
# Burst 160 — ADR-0048 T5 — posture clamp coverage for the
# soulux-computer-control plugin's tool surface.
#
# When the T5 tranche was estimated at 1 burst in ADR-0048, the
# ADR author assumed new gate code would be required to enforce
# Decision 4 (red dominates grants for non-read-only tools). On
# inspection: the existing PostureGateStep (ADR-0045 T1, B114 +
# T3, B115) ALREADY implements Decision 4 because the gate
# operates on side_effects, not tool name. read_only bypasses
# any posture; non-read-only is elevated to PENDING by yellow,
# refused outright by red, regardless of which plugin owns
# the tool.
#
# T5 therefore reduces to a doc + test-coverage commit:
#
#   - Update PostureGateStep's docstring to cite ADR-0048
#     explicitly + spell out which computer_* tools fall on
#     which side of the read_only/non-read_only line.
#   - Add 12 new tests under
#     TestPostureGateStep_ADR0048Coverage that exercise the
#     gate with computer_screenshot / computer_read_clipboard /
#     computer_click / computer_type / computer_run_app /
#     computer_launch_url tool names across green / yellow /
#     red postures. Confirms the coverage matrix is exactly
#     ADR-0048 Decision 4.
#   - Update ADR-0048's tranche table to mark T5 as shipped
#     via existing substrate (no new gate code).
#
# When ADR-0048 T2/T3 land actual computer-control tool dispatch,
# they automatically inherit posture clamps with zero additional
# gate work. The substrate is the right shape; the test rows in
# this commit are the proof.
#
# What ships:
#
#   src/forest_soul_forge/tools/governance_pipeline.py:
#     PostureGateStep docstring expanded with an ADR-0048 T5
#     section listing each computer-control tool's side_effects
#     classification + how the gate handles it. Confirms zero
#     new code required when T2/T3 land.
#
#   tests/unit/test_posture_gate_step.py:
#     New TestPostureGateStep_ADR0048Coverage class with 12
#     tests:
#       - 2 read-tool bypass tests (screenshot + clipboard pass
#         even at red posture)
#       - 4 yellow-elevates tests (click/type/run_app/launch_url
#         all PENDING when posture is yellow)
#       - 4 red-refuses tests (same four tools all REFUSE when
#         posture is red, with reason='agent_posture_red')
#       - 2 green-permissive tests (click goes through unmodified;
#         the gate adds no override on green so upstream
#         constitution + grants steps decide)
#
#   docs/decisions/ADR-0048-computer-control-allowance.md:
#     T5 row in the tranche table marked DONE B160 with the
#     "no new gate code needed" note. T1 row marked DONE B159.
#
# Verification:
#   - PYTHONPATH=src pytest tests/unit/test_posture_gate_step.py
#       -> 26 passed (was 14; +12 new ADR-0048 tests)
#   - The substrate now has explicit ADR-0048 coverage in the
#     test suite — when T2 + T3 wire the actual tool dispatch,
#     the regression suite catches any drift in posture handling
#
# Per ADR-0048 Decision 1: zero kernel ABI surface changes.
# Substrate (PostureGateStep) was already right; T5 just
# documents that fact.
#
# Remaining ADR-0048 tranches:
#   T2 — read tools (computer_screenshot.v1, computer_read_clipboard.v1)
#   T3 — action tools (4 tools)
#   T4 — Allowance UI in Chat-tab settings panel (closes ADR-0047
#        T4 full)
#   T6 — Documentation + safety guide

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/tools/governance_pipeline.py \
        tests/unit/test_posture_gate_step.py \
        docs/decisions/ADR-0048-computer-control-allowance.md \
        dev-tools/commit-bursts/commit-burst160-adr0048-t5-posture-coverage.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(security): ADR-0048 T5 — posture clamp coverage (B160)

Burst 160. Closes ADR-0048 T5. The estimated 1-burst T5 tranche
reduces to a doc + test-coverage commit because the existing
PostureGateStep (ADR-0045 T1 B114 + T3 B115) already implements
Decision 4. The gate operates on side_effects, not tool name —
so when ADR-0048 T2/T3 land computer-control tool dispatch, the
substrate covers them automatically without new gate code.

Ships:
- governance_pipeline.PostureGateStep docstring expanded with an
  ADR-0048 T5 section enumerating which computer_* tools fall on
  which side of the read_only/non-read_only line.
- test_posture_gate_step: TestPostureGateStep_ADR0048Coverage
  class with 12 new tests across screenshot/clipboard/click/
  type/run_app/launch_url X green/yellow/red posture matrix.
  Confirms coverage = ADR-0048 Decision 4 exactly.
- ADR-0048 tranche table: T5 marked DONE B160 (shipped via
  existing substrate); T1 marked DONE B159.

Verification: pytest tests/unit/test_posture_gate_step.py
-> 26 passed (was 14; +12 new ADR-0048 tests).

Per ADR-0048 Decision 1: zero kernel ABI surface changes.
Substrate was already correct; T5 just documents + locks down
that fact in the test suite.

Remaining ADR-0048 tranches:
- T2 read tools (computer_screenshot, computer_read_clipboard)
- T3 action tools (click, type, run_app, launch_url)
- T4 Allowance UI in Chat-tab (closes ADR-0047 T4 full)
- T6 Documentation + safety guide"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 160 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
