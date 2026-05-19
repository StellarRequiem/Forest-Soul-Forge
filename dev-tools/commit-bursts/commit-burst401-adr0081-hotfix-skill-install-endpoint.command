#!/bin/bash
# Burst 401 - ADR-0081 HOTFIX-2: skill install dir + endpoint URL.
#
# Two bugs surfaced when run-wiring-audit.command actually dispatched
# wiring_audit.v1 against the WiringSentinel (after B400 closed the
# genre miss):
#
#   Bug 1: wrong endpoint. run-wiring-audit.command POSTed to
#     /agents/<id>/skills/call — but the actual endpoint is
#     /agents/<id>/skills/run (ADR-0031 T2b skills_run.py). /skills/call
#     is for individual tool dispatch (ToolCallRequest); skills use
#     /skills/run which loads the manifest from disk.
#
#   Bug 2: wrong skill install location. /skills/run loads manifests
#     from data/forge/skills/installed/<name>.v<version>.yaml. T4
#     (B397) dropped wiring_audit.v1.yaml into examples/skills/ —
#     correct for the source-of-truth tree but the runtime install
#     dir is per-host (gitignored). The daemon never saw the manifest.
#
#   Bug 3 (incidental): SkillRunResponse exposes `status`
#     (succeeded|failed|...), not `ok` (bool). The wrapper's success
#     check looked at the wrong field.
#
# What this commit adds:
#
# 1. dev-tools/run-wiring-audit.command
#    - New step [0/3]: self-heal install. If
#      data/forge/skills/installed/wiring_audit.v1.yaml is missing,
#      copy it from examples/skills/. Happens once per fresh
#      checkout; subsequent runs no-op.
#    - URL fix: /skills/call -> /skills/run.
#    - Status check fix: look at `status == 'succeeded'` not
#      `ok == True`.
#
# Operator-visible behavior:
#   First run-wiring-audit.command on a fresh checkout: prints
#   '[0/3] Installing wiring_audit.v1 to data/forge/skills/installed/'
#   and then proceeds normally. Subsequent runs skip step 0.
#
# Note on data/forge/skills/installed/: per .gitignore this directory
# is per-host state, NOT git-tracked. Skills land here via either
# (a) explicit operator copy, (b) /skills/forge endpoint, or (c) the
# self-heal step added by this commit. Source of truth stays in
# examples/skills/ (git-tracked).
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: wiring_audit.v1 dispatch returns 404 'Not Found'.
#     T6 live verify cannot complete. The whole arc surfaces as
#     'sentinel born but never actually audits.'
#   Prove non-load-bearing: only run-wiring-audit.command changes.
#     Self-heal step is no-op when install already present. URL +
#     status field are the literally-correct values per the daemon's
#     own endpoint definitions.
#   Prove alternative is strictly better:
#     (a) Document the operator-copy step in the runbook and leave
#         the script broken - shifts burden to operator, easy to
#         forget, doesn't scale.
#     (b) Build a generic 'install all examples/skills/ to installed/'
#         endpoint - overreach for this fix; can land later if other
#         skills need it.
#     (c) Self-heal one skill in the one wrapper that consumes it -
#         minimal blast radius, correct behavior. This commit.
#
# Verification after this commit lands:
#   1. cp -f to ensure data/forge/skills/installed/wiring_audit.v1.yaml
#      is fresh (this commit already did the manual copy as part of
#      surfacing the bug).
#   2. bash dev-tools/run-wiring-audit.command
#      Expected: [0/3] no-op (already installed), [1/3] coverage
#      regenerated, [2/3] sentinel id resolved, [3/3] dispatch
#      status=succeeded.
#   3. Sentinel's lineage memory gains a wiring_audit_outcome entry.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/run-wiring-audit.command \
        dev-tools/commit-bursts/commit-burst401-adr0081-hotfix-skill-install-endpoint.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(audit): run-wiring-audit endpoint + skill install (ADR-0081 HOTFIX-2, B401)

Burst 401. HOTFIX-2 for B398 (run-wiring-audit.command).

Three bugs surfaced when the wrapper actually dispatched the skill
against WiringSentinel (after B400 closed the genre miss):

  Bug 1 — URL: posted to /agents/<id>/skills/call (tool dispatch
    endpoint). Skill dispatch is /agents/<id>/skills/run per
    ADR-0031 T2b.
  Bug 2 — install: skills_run.py loads manifests from
    data/forge/skills/installed/. T4 (B397) dropped wiring_audit.v1
    in examples/skills/ only. installed/ is per-host gitignored.
  Bug 3 — status check: SkillRunResponse uses status field, not ok.

Fix: run-wiring-audit.command
  - New [0/3] self-heal step: copies examples/skills/wiring_audit.v1
    to data/forge/skills/installed/ if missing. No-op when present.
  - URL: /skills/call -> /skills/run.
  - Success check: status == 'succeeded' (not ok == True).

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: dispatch returns 404, T6 live verify cannot complete.
  Prove non-load-bearing: only the wrapper changes. Self-heal is
    no-op when install already present.
  Prove alternative: documenting operator-copy in runbook shifts
    burden + doesn't scale; building a generic install endpoint is
    overreach for one skill.

After landing: bash dev-tools/run-wiring-audit.command should
reach status=succeeded + sentinel lineage gets wiring_audit_outcome."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 401 complete - ADR-0081 HOTFIX-2 shipped ==="
echo "=========================================================="
echo "Next: bash dev-tools/run-wiring-audit.command"
echo ""
echo "Press any key to close."
read -n 1 || true
