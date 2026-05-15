#!/bin/bash
# Burst 323 - ADR-0076 T5: fsf index rebuild CLI.
#
# Operator command to rebuild PersonalIndex from the SQL truth.
# Daemon-offline path: stop the daemon, run `fsf index rebuild`,
# restart. Useful after embedder swap, consolidation merge, or
# backup restore.
#
# What ships:
#
# 1. src/forest_soul_forge/cli/index_cmd.py (NEW):
#    - `fsf index status`: counts scope='personal' entries +
#      shows layer breakdown. Skips encrypted + deleted rows.
#    - `fsf index rebuild`: walks the registry, clears the
#      index, batch-embeds every plaintext personal-scope entry.
#      --dry-run reports counts + sample without loading the
#      embedder. --batch-size tunes the embed batch (default 32).
#    - _load_personal_entries(path): central helper used by both
#      subcommands. Filters scope='personal' AND deleted_at IS
#      NULL. Skips content_encrypted=1 rows (CLI is offline
#      plaintext path; encrypted rebuild needs daemon-resident
#      master key, deferred).
#
# 2. src/forest_soul_forge/cli/main.py:
#    - Wires `fsf index ...` into the subcommand tree.
#
# Tests (test_cli_index.py - 11 cases):
#   _load_personal_entries (3):
#     skips non-personal scope, skips deleted entries, skips
#     encrypted rows
#   status (3):
#     missing registry rc=2, empty registry 0 + clean, populated
#     shows layer breakdown
#   rebuild (5):
#     missing registry rc=2, empty registry clean noop, --dry-run
#     skips embedder load + shows sample, real rebuild populates
#     index (mock embedder), encrypted rows skipped from indexed
#     tally
#
# Sandbox-verified 11/11 pass.
#
# === ADR-0076 progress: 5/6 tranches closed ===
# Next: T6 operator runbook for personal-index lifecycle.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/cli/index_cmd.py \
        src/forest_soul_forge/cli/main.py \
        tests/unit/test_cli_index.py \
        dev-tools/commit-bursts/commit-burst323-adr0076-t5-index-cli.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(memory): ADR-0076 T5 - fsf index rebuild CLI (B323)

Burst 323. Operator command to rebuild PersonalIndex from the
SQL truth. Daemon-offline path: stop the daemon, run \`fsf index
rebuild\`, restart. Useful after embedder swap, consolidation
merge, or backup restore.

What ships:

  - cli/index_cmd.py (NEW): two subcommands.
    \`fsf index status\` counts scope='personal' entries + layer
    breakdown. \`fsf index rebuild\` clears the index, batch-
    embeds every plaintext personal-scope entry. --dry-run
    skips embedder load + shows count + sample. --batch-size
    tunes embed batch (default 32). _load_personal_entries
    central helper filters scope='personal' + deleted_at IS NULL
    + skips content_encrypted=1 rows (CLI is offline plaintext
    path; encrypted rebuild needs daemon-resident master key,
    deferred).

  - cli/main.py: wires the subcommand tree.

Tests: test_cli_index.py — 11 cases covering 3 _load helper
(scope filter, deleted filter, encrypted filter), 3 status (rc=2
on missing, empty 0-entry, populated layer breakdown), 5 rebuild
(rc=2 on missing, empty noop, dry-run no embedder load, real
rebuild populates via mock embedder, encrypted rows skipped from
tally). Sandbox-verified 11/11 pass.

ADR-0076 progress: 5/6 tranches closed. Next: T6 runbook."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 323 complete - ADR-0076 T5 index CLI shipped ==="
echo "ADR-0076: 5/6 tranches closed."
echo ""
echo "Press any key to close."
read -n 1
