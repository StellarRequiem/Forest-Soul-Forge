#!/bin/bash
# Burst 341 - ADR-0077 design correction caught during D4 birth.
#
# ReleaseGatekeeper-D4's birth on 2026-05-16 surfaced a genre
# kit-tier violation: "release_gatekeeper is in genre guardian
# (max_side_effects=read_only); the resolved kit contains tools
# that exceed that ceiling: shell_exec (external), pytest_run
# (filesystem)."
#
# ADR-0077 placed migration_pilot + release_gatekeeper in
# guardian because of the "advisory only" framing. The genre
# system is NOT about advisory-vs-acting — it's about the
# kit-tier action ceiling. Both agents need shell_exec to do
# real work (sqlite3 dry-runs, pytest conformance suite),
# which is filesystem/external tier.
#
# The "advisory" stance is correctly enforced at the
# constitutional layer via forbid_release_action +
# require_human_approval_for_apply policies. Genre placement
# should match the kit's action surface.
#
# What ships:
#
# 1. config/genres.yaml:
#    Moved migration_pilot + release_gatekeeper from guardian.
#    roles to actuator.roles. Added a NOTE comment under
#    guardian.roles explaining the B341 correction so future
#    operators don't try to put them back.
#
# 2. tests/unit/test_d4_advanced_rollout.py:
#    Renamed test_guardian_roles_in_guardian_genre →
#    test_actuator_roles_in_actuator_genre. Docstring explains
#    why the placement was corrected.
#
# Operationally NOTHING changes for the operator — YELLOW
# posture queues every dispatch regardless of genre.
# GREEN-posture release_gatekeeper emits decisions freely; the
# real gate is the operator tag-time review.
#
# Sandbox-verified 33/33 pass on test_d4_advanced_rollout.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add config/genres.yaml \
        tests/unit/test_d4_advanced_rollout.py \
        dev-tools/birth-test-author.command \
        dev-tools/birth-migration-pilot.command \
        dev-tools/birth-release-gatekeeper.command \
        dev-tools/finish-d4-rollout.command \
        dev-tools/commit-bursts/commit-burst341-d4-genre-correction.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(d4): genre correction + birth-script EOF tolerance (B341)

Burst 341. Two corrections caught during the finish-d4-rollout
attempt on 2026-05-16:

1. migration_pilot + release_gatekeeper moved guardian → actuator.
   ADR-0077 placed them in guardian for the 'advisory only'
   framing, but their kits include shell_exec (external) +
   pytest_run (filesystem) which exceed guardian's read_only
   ceiling. The advisory stance is correctly enforced via
   constitutional policies (forbid_release_action,
   require_human_approval_for_apply), not by genre. Genre =
   kit-tier action ceiling.

2. birth-*.command scripts: trailing 'read -n 1' is now
   EOF-tolerant (|| true). When invoked from the umbrella
   with '< /dev/null', the read returns rc=1 which under
   'set -e' was aborting the script even though the actual
   birth succeeded. The umbrella now propagates the real
   birth status correctly.

  - config/genres.yaml: migration_pilot + release_gatekeeper
    moved to actuator.roles. Note under guardian.roles
    explains the B341 correction.
  - tests/unit/test_d4_advanced_rollout.py: renamed test to
    test_actuator_roles_in_actuator_genre with docstring
    explaining the correction.
  - dev-tools/birth-{test-author,migration-pilot,release-
    gatekeeper}.command: 'read -n 1 || true' at the end so
    non-interactive callers (the umbrella) don't trip set -e.
  - dev-tools/finish-d4-rollout.command: operator finisher
    that reloads skills + births remaining agents + verifies.

Sandbox-verified 33/33 pass on test_d4_advanced_rollout.
Operator next step: force-restart-daemon to pick up the
corrected genre assignments, then re-fire finish-d4-rollout."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 341 complete — birth pipeline correction ==="
echo "Restart daemon + re-fire finish-d4-rollout to complete."
echo ""
echo "Press any key to close."
read -n 1 || true
