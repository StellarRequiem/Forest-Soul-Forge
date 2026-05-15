#!/bin/bash
# Burst 319 - ADR-0068 T8: profile migration substrate.
#
# Closes ADR-0068 8/8. Version-aware loader + per-version
# migration registry + auto-migrate-on-load with disk backup +
# fsf operator migrate CLI with --dry-run + --restore-from-backup.
# Schema stays at 1; substrate is in place ahead of any breaking
# change.
#
# What ships:
#
# 1. src/forest_soul_forge/core/operator_profile.py:
#    - PROFILE_MIGRATIONS: dict[int, callable] — empty by default.
#      Each entry transforms a v<N> raw dict to v<N+1>.
#    - register_profile_migration(from_version=N) decorator
#      registers under the source version; double-register raises.
#    - migrate_raw_profile(raw, from_version, to_version) walks
#      the chain, stamps schema_version onto each intermediate
#      result, raises OperatorProfileError on missing-step or
#      future-version-than-daemon.
#    - _validate_and_construct now version-aware: when sv !=
#      SCHEMA_VERSION, invoke migrate_raw_profile, then re-pull
#      operator dict (a migration may have restructured nesting).
#    - load_operator_profile detects pre-migration version,
#      runs the migration in-memory, writes profile.yaml.bak.v<N>
#      with the original content, atomically saves the migrated
#      form. Backup-path collisions get a .seq suffix so older
#      backups are never overwritten.
#    - _backup_path + _write_migration_backup helpers.
#
# 2. src/forest_soul_forge/core/audit_chain.py:
#    KNOWN_EVENT_TYPES gains operator_profile_migrated for the
#    CLI / loader path that drives an actual migration.
#
# 3. src/forest_soul_forge/cli/operator_cmd.py:
#    `fsf operator migrate` subcommand:
#      --profile-path     override target (default: data/operator/profile.yaml)
#      --dry-run          print the migrated YAML; don't touch disk
#      --restore-from-backup  read the latest .bak.v<N> + restore
#    Idempotent: re-running when already at SCHEMA_VERSION exits
#    0 with 'nothing to do'. Refuses when profile is missing OR
#    schema_version is not an integer OR no backup exists for
#    --restore-from-backup.
#
# Tests (test_operator_profile_migration.py - 19 cases):
#   Framework primitives (8):
#     - PROFILE_MIGRATIONS empty by default
#     - register decorator adds to registry
#     - double-register refused
#     - same-version no-op
#     - future-version refused
#     - missing-step refused
#     - registered chain walks + bumps version
#     - inner exception wrapped in OperatorProfileError
#   Loader integration (4):
#     - auto-migrate writes backup + replaces target
#     - load at current version writes NO backup
#     - non-integer schema_version refused
#     - future-version-no-migration refused
#   CLI (6):
#     - dry-run prints + doesn't touch disk
#     - real migrate writes backup + replaces
#     - already-current exits clean
#     - restore-from-backup rolls back
#     - restore refuses when no backup exists
#     - missing profile refused
#   Audit event registration (1)
#
# Per-test fixture autouse: clean PROFILE_MIGRATIONS +
# SCHEMA_VERSION across tests so registry-style monkey-patching
# doesn't leak.
#
# Sandbox-verified all 8 framework cases + the end-to-end auto-
# migrate + all 5 CLI scenarios.
#
# === ADR-0068 CLOSED 8/8 ===
# Operator profile / personal context arc complete. Phase alpha
# scorecard: 7/10 closed (ADR-0050, 0067, 0068, 0071, 0073, 0074,
# 0075). Only ADR-0070 voice + ADR-0072 provenance + ADR-0076
# vector index still partial.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/operator_profile.py \
        src/forest_soul_forge/core/audit_chain.py \
        src/forest_soul_forge/cli/operator_cmd.py \
        tests/unit/test_operator_profile_migration.py \
        dev-tools/commit-bursts/commit-burst319-adr0068-t8-migration.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(operator): ADR-0068 T8 - migration substrate (B319) — ARC CLOSED 8/8

Burst 319. Closes ADR-0068. Version-aware loader + per-version
migration registry + auto-migrate-on-load with disk backup +
fsf operator migrate CLI with --dry-run + --restore-from-backup.
Schema stays at 1 today; substrate is in place ahead of any
future breaking change (rename, restructure, narrowed enum).

What ships:

  - core/operator_profile.py: PROFILE_MIGRATIONS dict registry,
    register_profile_migration decorator (double-register
    raises), migrate_raw_profile walks the chain and stamps
    schema_version onto each step. _validate_and_construct is
    now version-aware: invokes the chain when sv != SCHEMA_VERSION,
    re-pulls operator dict after migration in case a step
    restructured nesting. load_operator_profile detects
    pre-migration version, runs migration in-memory, writes
    .bak.v<N> with original content, atomically saves migrated
    form. Backup-collision gets .seq suffix so older backups
    never get overwritten. _backup_path + _write_migration_backup
    helpers.

  - core/audit_chain.py: KNOWN_EVENT_TYPES gains
    operator_profile_migrated.

  - cli/operator_cmd.py: fsf operator migrate subcommand with
    --profile-path / --dry-run / --restore-from-backup.
    Idempotent (no-op when already at current version), refuses
    missing profile + non-int schema_version + missing backup
    on restore.

Tests: test_operator_profile_migration.py - 19 cases covering
8 framework primitives (empty default, decorator register, double-
register refused, no-op same-version, future-version refused,
missing-step refused, chain walks + version bump, inner exception
wrapping), 4 loader integration cases (auto-migrate-with-backup,
at-current-no-backup, non-int refused, future-no-migration
refused), 6 CLI scenarios (dry-run, real-run, idempotent,
restore-from-backup, missing-backup refused, missing-profile
refused), and audit event registration. Autouse fixture cleans
PROFILE_MIGRATIONS + SCHEMA_VERSION across tests.

Sandbox-verified all 8 framework cases + end-to-end auto-migrate +
all 5 CLI scenarios.

=== ADR-0068 CLOSED 8/8 ===
Operator profile / personal context arc complete. Phase alpha
scorecard: 7/10 closed (0050, 0067, 0068, 0071, 0073, 0074,
0075). Only ADR-0070 voice + ADR-0072 provenance + ADR-0076
vector index still partial."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 319 complete - ADR-0068 CLOSED 8/8 ==="
echo "Phase alpha: 7/10 scale ADRs closed."
echo ""
echo "Press any key to close."
read -n 1
