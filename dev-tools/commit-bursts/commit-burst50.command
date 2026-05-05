#!/usr/bin/env bash
# Burst 50: external-review-readiness pass.
#
# - STATE.md numbers refreshed (1567 -> 1589 tests, 32 -> 35 ADRs,
#   add tools-with-annotations row).
# - New docs/external-review-readiness.md as the next reviewer's
#   first stop. Structured to make the SarahR1-style stale-snapshot
#   cost recoverable in advance: changed-since-last-review summary,
#   load-bearing invariants, "where to look for this was already
#   discussed" map, gap list with explicit deferral reasoning,
#   ground rules, directory map, "high-yield review surfaces" hints.
#
# Pure doc commit. No code. No tests. Test count unchanged.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 50 — external-review-readiness pass ==="
echo
clean_locks
git add STATE.md \
        docs/external-review-readiness.md \
        commit-burst50.command
clean_locks
git status --short
echo
clean_locks
git commit -m "Burst 50: external-review-readiness pass

Two changes preparing the codebase for the next external analysis:

1. STATE.md numbers refreshed:
   - Test count 1567 -> 1589 (post Bursts 46 + 49 per-tool
     initiative annotations).
   - ADR count 32 -> 35 (filed 0035/0036/0037 as Proposed in Burst 47).
   - New row: tools-with-initiative-annotations 12 of 41 (covers
     the heaviest action surfaces; remaining 29 are read-only or
     memory-write tools where the genre's max_side_effects ceiling
     already gates them).

2. New docs/external-review-readiness.md.

The doc lowers the cost-of-stale-snapshot the prior SarahR1 review
paid (snapshot was 2-3 days behind; ~30% of her critique evaporated
once corrected). It's structured to be the reviewer's first stop:

- Snapshot in 60 seconds (latest tag, HEAD, test counts, schema, etc.).
- 'What changed since the last external review' table — three of
  SarahR1's surfaces with before/after rows.
- Load-bearing invariants list (the 8 things from CLAUDE.md, plus
  the local-first commitment) with explicit 'please don't propose
  breaking these' framing.
- 'Where to look for this was already discussed' map — common
  topics keyed to ADRs / docs.
- Active gaps + deliberate decisions section, split into:
    - Implementation gaps queued for v0.3 (ADR-0035/0036/0037)
    - Implementation gaps that are NOT necessarily v0.3 (A/V,
      federation, frontend tests)
    - Deliberate decisions that may look like gaps (initiative
      coverage, embodied state, soul.md framing)
- Ground rules for proposing changes (verify before proposing,
  §0 gate, attribution discipline, one bite at a time, disagreement
  is fine).
- Quick orientation directory map.
- Specific surfaces that benefit from external eyes vs surfaces
  where review yield is lower.
- Closing note framing the absorption pattern (review -> ADRs ->
  implementation -> tag -> response of record).

This is the response to Alex's request: 'prepare for another
analysis from outside.'

No code. No test impact (1589 still passing)."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 50 landed. External-review-readiness doc filed. Codebase is ready for the next analysis."
echo ""
read -rp "Press Enter to close..."
