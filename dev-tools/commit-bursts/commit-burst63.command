#!/usr/bin/env bash
# Burst 63: docs refresh + version bump for v0.2.0 release.
#
# - STATE.md: Last-updated note + numbers table updated
#   (1567 -> 1968 tests, 35 -> 36 ADRs, 41 -> 51 builtins,
#   12 -> 14 initiative-annotated tools).
# - README.md: by-the-numbers table + LoC bump.
# - CHANGELOG.md: new [0.2.0] entry capturing Phase G.1.A close
#   + ADR-0039 + benchmark plan + external-review-readiness +
#   the 3 caught-and-fixed bugs.
# - pyproject.toml: version bump 0.1.0 -> 0.2.0.
#
# This is the paperwork commit that prepares the tag in Burst 64.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 63 — docs refresh + version bump for v0.2.0 ==="
echo
clean_locks
git add STATE.md \
        README.md \
        CHANGELOG.md \
        pyproject.toml \
        commit-burst63.command
clean_locks
git status --short
echo
clean_locks
git commit -m "docs: v0.2.0 paperwork — STATE / README / CHANGELOG + version bump

The paperwork commit that prepares the v0.2.0 tag. No code changes;
this is purely the version + docs refresh that reflect Phase G.1.A's
close.

Updates:
- pyproject.toml: version 0.1.0 -> 0.2.0
- STATE.md: 'Last updated' note rewritten to reflect Phase G.1.A
  close + numbers table updated (1567 -> 1968 tests; 35 -> 36 ADRs;
  41 -> 51 builtin tools registered; 12 -> 14 initiative-annotated
  tools — pytest_run + pip_install_isolated added at L4)
- README.md: by-the-numbers table updated to v0.2.0 (51 builtins,
  1968 tests, 36 ADRs); LoC bumped to ~46,000 (from ~36,400) to
  reflect the +9.6k from Phase G.1.A's 10 primitives + tests
- CHANGELOG.md: new [0.2.0] entry capturing
  * the 10 Phase G.1.A primitives in dependency order with commit
    SHAs
  * ADR-0039 Distillation Forge / Swarm Orchestrator filed as
    Proposed for v0.4
  * docs/audits/2026-05-01-fsf-dispatch-overhead-benchmark-plan.md
  * external-review-readiness pass (Burst 50)
  * round 2 per-tool initiative_level annotations (Burst 49)
  * the 3 catches-and-fixes from the run (Burst 58 backtick gotcha,
    Burst 61 -llll flag bug, Burst 62 mock-capture wrong subprocess)

Test count unchanged (1968 passing) — this is a paperwork-only
commit. Next: Burst 64 ships the annotated v0.2.0 tag."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 63 landed. Docs refreshed. Version bumped to 0.2.0."
echo "Next: Burst 64 (annotated v0.2.0 tag)."
echo ""
read -rp "Press Enter to close..."
