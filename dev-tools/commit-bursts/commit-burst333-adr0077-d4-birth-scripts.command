#!/bin/bash
# Burst 333 - ADR-0077 D4 T2b: birth scripts for the three new D4 agents.
#
# Operator-driven, idempotent. Mirrors birth-smith.command's 5-phase
# shape (we drop the workspace-clone phase since these agents don't
# operate branch-isolated like Smith does).
#
# What ships:
#
# 1. dev-tools/birth-test-author.command (NEW):
#    Births TestAuthor-D4 with role=test_author, owner_id=alex.
#    Patches code_edit (allowed=["tests/"], forbidden=["src/"]),
#    shell_exec (pytest+python3 only), web_fetch (docs hosts
#    only). Posture YELLOW.
#
# 2. dev-tools/birth-migration-pilot.command (NEW):
#    Births MigrationPilot-D4. Patches code_edit (registry/ +
#    tests/migrations/), shell_exec (sqlite3+pytest+python3).
#    Posture YELLOW. The apply-step approval gate is
#    constitutional (require_human_approval_for_apply) — YELLOW
#    adds redundant gate during bedding-in.
#
# 3. dev-tools/birth-release-gatekeeper.command (NEW):
#    Births ReleaseGatekeeper-D4. Patches shell_exec (pytest+fsf
#    only — no git, no pip, no curl, no twine). Posture GREEN
#    rationale: gate emits decisions freely; operator tag-time
#    is the actual gate. forbid_release_action constitutional
#    policy blocks dangerous tools at kit layer regardless.
#
# Each script is self-contained and operator-driven from Finder.
# Re-run is a clean no-op (existence check + kickstart-idempotent
# launchctl + posture set is naturally idempotent + constraint
# patch is line-anchored YAML-safe).
#
# These scripts DO NOT auto-run. Alex fires each one when ready,
# in whatever order makes sense. Recommended order:
#   1. test_author  (cheapest; no apply gate, easiest to observe)
#   2. release_gatekeeper (advisory-only; safe to birth before
#      migration_pilot lands)
#   3. migration_pilot (most cautious; birth last so the operator
#      sees the approval queue light up before any dry-run fires)
#
# === ADR-0077 progress: T2 + T2b shipped; T3 wiring next ===

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/birth-test-author.command \
        dev-tools/birth-migration-pilot.command \
        dev-tools/birth-release-gatekeeper.command \
        dev-tools/commit-bursts/commit-burst333-adr0077-d4-birth-scripts.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(d4): ADR-0077 T2b - three operator-driven birth scripts (B333)

Burst 333. Three idempotent birth scripts that mirror birth-
smith.command's 5-phase shape (daemon restart → existence check
→ /birth POST → constitution patch → posture set), one per D4
advanced rollout agent.

What ships:

  - dev-tools/birth-test-author.command (NEW):
    Births TestAuthor-D4. Patches code_edit to allowed_paths=
    [tests/] + forbidden_paths=[src/], shell_exec to allowed=
    [pytest, python3], web_fetch to docs hosts only. Posture
    YELLOW. forbid_production_code_edit policy is the load-
    bearing invariant; constraints enforce it at the tool layer
    too.

  - dev-tools/birth-migration-pilot.command (NEW):
    Births MigrationPilot-D4. Patches code_edit to allowed_
    paths=[src/forest_soul_forge/registry/, tests/migrations/],
    shell_exec to allowed=[sqlite3, pytest, python3]. Posture
    YELLOW. Apply-step approval gate is constitutional
    (require_human_approval_for_apply) — YELLOW adds redundant
    bedding-in gate.

  - dev-tools/birth-release-gatekeeper.command (NEW):
    Births ReleaseGatekeeper-D4. Patches shell_exec to allowed=
    [pytest, fsf] only — explicitly forbid git/pip/curl/twine
    to enforce the forbid_release_action constitutional policy
    at the kit layer. Posture GREEN — gate emits decisions
    freely; operator tag-time is the actual gate.

Scripts do NOT auto-run. Alex fires each when ready. Recommended
order: test_author (cheapest, no apply gate) → release_gatekeeper
(advisory-only) → migration_pilot (most cautious; birth last).

ADR-0077 progress: T1 doc (B331) + T2 substrate (B332) + T2b
birth scripts (B333). Next: T3 handoffs.yaml wiring + cascade
rules + integration test."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "Burst 333 complete - ADR-0077 T2b birth scripts shipped"
echo "=========================================================="
echo ""
echo "The three birth scripts live at:"
echo "  dev-tools/birth-test-author.command"
echo "  dev-tools/birth-migration-pilot.command"
echo "  dev-tools/birth-release-gatekeeper.command"
echo ""
echo "Run each from Finder when you're ready. Recommended order:"
echo "  1. test_author       (cheapest, no apply gate)"
echo "  2. release_gatekeeper (advisory-only, safe early birth)"
echo "  3. migration_pilot   (most cautious, birth last)"
echo ""
echo "Press any key to close this window."
read -n 1
