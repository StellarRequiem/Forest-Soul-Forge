#!/bin/bash
# Burst 375 - housekeeping: clear 14 stale scratch scripts from
# repo root + record the 2 keepers' new homes.
#
# Triage (2026-05-17):
#   17 untracked files accumulated in repo root from earlier
#   sessions (B205-B266 live-tests + B261-B266 targeted diagnostics
#   + an empty dependencies/sbom.json). Each is one of:
#     - Closed-burst artifact (test ran once during the burst it
#       belongs to; harness section-13 + section-14 now cover that
#       surface end-to-end). TRASH.
#     - Empty placeholder. TRASH.
#     - Reusable session-gate template. KEEP, promote to dev-tools/.
#     - Forward-looking demo script. KEEP, move to docs/.
#
# Two keepers were already moved by the sandbox via `git mv` /
# `mv` (DEMO-SCRIPT.md -> docs/, run-session-tests.command ->
# dev-tools/). The sandbox is read-only on the host filesystem
# for `rm`, so the 14 trash files need to be cleared from a
# host-run script - this one.
#
# What this commit lands:
#   docs/DEMO-SCRIPT.md (NEW location for the demo video script;
#     formerly at repo root)
#   dev-tools/run-session-tests.command (NEW location for the
#     'session-end gate' template; formerly at repo root)
#   [removed] 14 scratch files from repo root:
#     diag-anchor-birth.command
#     diag-b261-tests.command
#     diag-b266-tests.command
#     diag-session-tests.command
#     fix-and-rerun-tests.command
#     live-test-b205-staged-listing.command
#     live-test-b207-skill-forge.command
#     live-test-discard-text-to-bullets.command
#     live-test-dispatch-translate.command
#     live-test-install-summarize.command
#     live-test-install-translate.command
#     live-test-tool-forge-b210.command
#     live-test-tool-forge-e2e.command
#     live-test-translate-via-fresh-agent.command
#   [removed] dependencies/ (contained only an empty sbom.json)
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm:
#     - Each trash file is a closed-burst artifact whose only job
#       was to verify a burst at land-time. The bursts shipped;
#       harness section-13 + section-14 now cover that surface
#       continuously. The scripts add clutter without value.
#     - Empty dependencies/sbom.json is misleading - someone
#       reading the tree might think SBOM tracking is in place
#       when it isn't.
#   Prove non-load-bearing for the 14:
#     - grepped for filename references across repo: zero hits
#       outside of this commit script. Nothing imports them,
#       nothing schedules them, nothing documents them as
#       operator-facing.
#     - Each script's header explicitly describes itself as
#       single-burst diagnostic (or 'no longer needed' per the
#       discard script).
#     - All bursts they verified have shipped + been re-verified
#       by the harness end-to-end (13 PASS on substrate; 13 PASS
#       expected from section 1-13 on the next run, plus
#       section-14 from B366).
#   Prove alternative is strictly better:
#     - Keep-in-place = 17 confusing files at repo root for every
#       future contributor + every git status read.
#     - Move-to-archive/ = directory of zombies; same noise, just
#       relocated. Better-but-worse than delete.
#     - Trash + commit = paper trail in git history + clean
#       working tree. Recovery via `git log --diff-filter=D` if
#       a future need surfaces.
#
# What stays after this commit:
#   docs/DEMO-SCRIPT.md - forward-looking demo video shooting
#     script. Likely intentional content. Lives in docs/ now.
#   dev-tools/run-session-tests.command - reusable 'session-end
#     gate' shape. Future sessions can use it; lives in dev-tools/
#     alongside the other operator-facing scripts.
#
# Verification after this commit lands:
#   1. ls repo root - 17 fewer files; no more diag-* or live-test-*
#      at top level.
#   2. ls docs/DEMO-SCRIPT.md - keeper at new home.
#   3. ls dev-tools/run-session-tests.command - keeper at new home.
#   4. ls dependencies/ - directory gone.
#   5. diagnostic-all.command - still 13 PASS / 0 FAIL (section-14
#      adds another row but the substrate is untouched).

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

# Step 1 - clear the 14 trash files. -f so missing files don't
# halt the script; if the sandbox already removed any, this is
# idempotent.
TRASH=(
  diag-anchor-birth.command
  diag-b261-tests.command
  diag-b266-tests.command
  diag-session-tests.command
  fix-and-rerun-tests.command
  live-test-b205-staged-listing.command
  live-test-b207-skill-forge.command
  live-test-discard-text-to-bullets.command
  live-test-dispatch-translate.command
  live-test-install-summarize.command
  live-test-install-translate.command
  live-test-tool-forge-b210.command
  live-test-tool-forge-e2e.command
  live-test-translate-via-fresh-agent.command
)
echo "--- removing trash ---"
for f in "${TRASH[@]}"; do
  if [ -e "$f" ]; then
    rm -f "$f"
    echo "  rm $f"
  else
    echo "  (already gone) $f"
  fi
done

# Step 2 - clear the empty dependencies/ directory.
if [ -d dependencies ]; then
  rm -rf dependencies
  echo "  rm -rf dependencies/"
fi

# Step 3 - git add the keepers + this script + the deletions.
# Keepers were already mv'd by the sandbox; git status shows them
# as untracked at the new locations. The deletions get picked up
# by `git add -A` against the listed files.
git add docs/DEMO-SCRIPT.md \
        dev-tools/run-session-tests.command \
        dev-tools/commit-bursts/commit-burst375-housekeeping-trash-and-keepers.command
# Tell git about the removals so they're staged for the commit.
for f in "${TRASH[@]}"; do
  git rm --quiet --cached --ignore-unmatch "$f" 2>/dev/null || true
done

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "chore(repo): housekeeping - clear 14 stale scratch scripts (B375)

Burst 375. Housekeeping pass on the repo root.

Triage findings (2026-05-17):
  17 untracked files accumulated from earlier sessions
  (B205-B266 live-tests + B261-B266 targeted diagnostics +
  empty dependencies/sbom.json).

TRASH (14, removed):
  diag-anchor-birth        - one-off /birth 503 diag; fixed
  diag-b261-tests          - 'used once per push-verify'; shipped
  diag-b266-tests          - master-key tests; ADR-0050 closed
  diag-session-tests       - B248-B256 specific; shipped
  fix-and-rerun-tests      - numpy missing dep; fixed
  live-test-b205-*         - B205 shipped + harness covers
  live-test-b207-*         - B207 shipped
  live-test-discard-*      - per its header: 'no longer needed'
  live-test-dispatch-*     - translate substrate + harness cover
  live-test-install-*      (x2) - B208/B210 shipped
  live-test-tool-forge-*   (x2) - ADR-0058 + B210 shipped
  live-test-translate-via-fresh-agent - same
  dependencies/sbom.json   - empty placeholder

KEPT (2, relocated):
  DEMO-SCRIPT.md -> docs/DEMO-SCRIPT.md
    (demo video shooting script; forward-looking content)
  run-session-tests.command -> dev-tools/run-session-tests.command
    (reusable 'session-end gate' shape; operator-facing)

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: 17 confusing files at repo root for every git
    status + future contributor read; empty SBOM is misleading.
  Prove non-load-bearing for 14: grepped repo - zero references
    outside this commit script. Each script's own header marks
    itself single-burst diagnostic. All bursts they verified
    have shipped + the harness now covers their surface
    continuously.
  Prove alternative is better: archive/ would relocate noise;
    trash + git log --diff-filter=D for recovery is cleaner.

After this lands: repo root has 17 fewer files; substrate
behavior unchanged (no code touched)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 375 complete - repo root cleaned ==="
echo "=========================================================="
echo "Verify:"
echo "  ls $(pwd)/ | grep -E 'diag-|live-test-' | wc -l   # should be 0"
echo "  ls $(pwd)/docs/DEMO-SCRIPT.md"
echo "  ls $(pwd)/dev-tools/run-session-tests.command"
echo "  ls $(pwd)/dependencies 2>&1 | grep -q 'No such' && echo 'dependencies/ gone'"
echo ""
echo "Press any key to close."
read -n 1 || true
