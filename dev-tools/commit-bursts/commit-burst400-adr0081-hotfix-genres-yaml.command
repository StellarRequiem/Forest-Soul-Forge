#!/bin/bash
# Burst 400 - ADR-0081 HOTFIX: genres.yaml missing wiring_sentinel.
#
# Surfaced during live verify (after B399 close). birth-wiring-
# sentinel.command POST /birth returned 500 Internal Server Error.
# Root cause: B396 added the wiring_sentinel archetype + trait
# profile + constitution template, but missed adding the role
# to config/genres.yaml's guardian.roles list. The genre resolver
# (genre_engine) looks up which genre a role belongs to via that
# list — without an entry, role-to-genre resolution fails and
# the birth pipeline raises before kit-tier check completes.
#
# Same pattern as ADR-0078 / ADR-0064: every new role needs FOUR
# substrate entries:
#   1. archetype in config/tool_catalog.yaml (kit)            ✓ B396
#   2. trait profile in config/trait_tree.yaml                ✓ B396
#   3. constitution template in config/constitution_templates ✓ B396
#   4. genre membership in config/genres.yaml's <genre>.roles ✗ MISSED in B396 — fixed here
#
# This is the same class of gap as B363/B392 (missed cross-layer
# wiring) — fittingly, the one the ADR-0081 sentinel is designed
# to surface. Self-referential: we missed a wiring layer for the
# wiring sentinel itself. Operator pointed at the script that
# would have caught this exact class given a few more days of
# coverage maturity.
#
# What this commit adds:
#
# 1. config/genres.yaml — guardian.roles list gains
#    `wiring_sentinel` entry with a one-line comment pointing at
#    ADR-0081 T3 (B396) + the kit summary.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: /birth POST returns 500. WiringSentinel cannot be
#     born. The entire T6 live verify is blocked.
#   Prove non-load-bearing: one-line ADDITION to a roles list.
#     No removals; no behavior changes to existing roles.
#   Prove alternative is strictly better:
#     (a) Roll back the role entirely - destroys the 6-burst arc.
#     (b) Move the role to a different genre - guardian is the
#         correct genre per the role's read_only kit + audit-
#         oriented behavior; moving it would be wrong.
#     (c) Add the missing entry - what this commit does.
#
# Verification after this commit lands:
#   1. force-restart-daemon (daemon reloads genres.yaml).
#   2. bash dev-tools/birth-wiring-sentinel.command
#      Expected: birth succeeds with instance_id printed,
#      constitution parses, posture=green.
#   3. curl -s http://127.0.0.1:7423/agents -H \"X-FSF-Token: \$TOKEN\" \\
#        | jq '.agents[] | select(.role==\"wiring_sentinel\")'
#      Expected: one row, status=active.
#   4. bash dev-tools/run-wiring-audit.command
#      Expected: section-15 runs, sentinel ID resolved, skill
#      dispatch ok=true, lineage memory gains a
#      wiring_audit_outcome entry.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add config/genres.yaml \
        dev-tools/commit-bursts/commit-burst400-adr0081-hotfix-genres-yaml.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(role): add wiring_sentinel to guardian.roles (ADR-0081 HOTFIX, B400)

Burst 400. HOTFIX for B396 miss. /birth POST returned 500 during
T6 live verify because B396 added the wiring_sentinel archetype
+ trait + template but missed adding the role to
config/genres.yaml's guardian.roles list. Genre resolver fails;
birth raises before kit-tier check completes.

Every new role needs FOUR substrate entries:
  1. archetype in config/tool_catalog.yaml (kit)         ✓ B396
  2. trait profile in config/trait_tree.yaml             ✓ B396
  3. constitution template in constitution_templates     ✓ B396
  4. genre membership in genres.yaml <genre>.roles       MISSED — fixed here

Same class of cross-layer-wiring gap as B363/B392. Fittingly,
the one ADR-0081's sentinel is designed to surface. Self-
referential miss: section-15 wasn't yet checking 'every role
in tool_catalog archetypes has a genre.roles entry' as a check.
Should be added next.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: /birth POST 500. WiringSentinel cannot be born.
    Entire T6 live verify blocked.
  Prove non-load-bearing: one-line ADDITION.
  Prove alternative: rollback destroys arc; move-to-different-
    genre is wrong (guardian is correct); add the entry IS the
    right move.

After landing: force-restart-daemon + re-run birth."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 400 complete - ADR-0081 HOTFIX shipped ==="
echo "=========================================================="
echo "Next:"
echo "  bash dev-tools/force-restart-daemon.command"
echo "  bash dev-tools/birth-wiring-sentinel.command"
echo ""
echo "Press any key to close."
read -n 1 || true
