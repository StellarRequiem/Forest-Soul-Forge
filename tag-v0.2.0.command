#!/usr/bin/env bash
# Tag v0.2.0 — Phase G.1.A close (programming primitives).
#
# Run AFTER Burst 63 (commit-burst63.command) has landed STATE/README/
# CHANGELOG/pyproject paperwork to origin. This script just creates the
# annotated tag at HEAD and pushes it.
#
# Idempotent-ish: if the tag already exists locally we don't overwrite
# it; we just try the push (origin will either accept or report
# "already up to date").

set -euo pipefail

cd "$(dirname "$0")"

echo "=== Tag v0.2.0 — Phase G.1.A close ==="
echo

# Verify clean tree before tagging.
if [ -n "$(git status --porcelain)" ]; then
  echo "ERROR: working tree is not clean. Land all open commits first."
  echo "Uncommitted changes:"
  git status --short
  read -rp "Press Enter to exit..."
  exit 1
fi

# Verify HEAD is on origin.
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main 2>/dev/null || echo "")
if [ "$LOCAL" != "$REMOTE" ]; then
  echo "WARNING: local HEAD differs from origin/main."
  echo "  local:  $LOCAL"
  echo "  origin: $REMOTE"
  read -rp "Press Enter to continue anyway, or Ctrl-C to abort: "
fi

# Create the annotated tag (skip if already exists locally).
if git tag -l v0.2.0 | grep -q v0.2.0; then
  echo "Tag v0.2.0 already exists locally — skipping create."
else
  git tag -a v0.2.0 -m "Phase G.1.A close — programming primitives release

Test suite: 1567 -> 1968 passing (+401, +25.6%). Zero regressions
across the entire v0.2 arc.

The 10 programming primitives that complete the SW-track agent
change-loop (in dependency order):

  1. ruff_lint.v1                read_only           97d09b3
  2. pytest_run.v1               filesystem  L4      3628656
  3. git_log_read.v1             read_only           6288834
  4. git_diff_read.v1            read_only           b077d3e
  5. git_blame_read.v1           read_only           41d642c
  6. mypy_typecheck.v1           read_only           cfe4219
  7. semgrep_scan.v1             read_only           52dc571
  8. tree_sitter_query.v1        read_only           6b3cdcc
  9. bandit_security_scan.v1     read_only           90f80d5
 10. pip_install_isolated.v1     filesystem  L4      a59d08f

Eight read_only inspection tools + two filesystem-tier actuators
gated at L4 (reversible-with-policy per ADR-0021-am section 5).
The change loop is now agent-completable: code_read -> static
gates -> code_edit -> pytest_run -> pip_install_isolated when
a missing dep surfaces.

Architectural additions:
- ADR-0039 Distillation Forge / Swarm Orchestrator (Proposed,
  v0.4 candidate)
- docs/audits/2026-05-01-fsf-dispatch-overhead-benchmark-plan.md
- External-review-readiness pass + docs/external-review-readiness.md

See CHANGELOG.md [0.2.0] entry for the full ledger."
  echo "Tag v0.2.0 created locally."
fi

echo
echo "Pushing tag to origin..."
git push origin v0.2.0

echo
echo "v0.2.0 tagged and pushed."
echo "Verify at: https://github.com/StellarRequiem/Forest-Soul-Forge/releases"
echo
read -rp "Press Enter to close..."
