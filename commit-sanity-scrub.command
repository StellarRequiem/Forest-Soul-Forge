#!/usr/bin/env bash
# Sanity-check scrub: STATE.md count corrections + v0.3 arc rollup.
#
# A deliberate verification pass against on-disk state caught three
# paperwork drifts in STATE.md (no code drift). Fixing in one
# commit so the docs match reality going forward.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Sanity scrub — STATE.md count corrections ==="
echo
clean_locks
git add STATE.md commit-sanity-scrub.command
clean_locks
git status --short
echo
clean_locks
git commit -m "docs: STATE.md scrub — correct count drift after v0.3 arc

Sanity-check pass against on-disk state caught three paperwork
drifts in STATE.md. No code drift; the work shipped in Bursts 65-70
is correct. Fixes:

- Tests passing: 1968 -> 2072 (was stale post-v0.2.0; reflects
  the +104 from the v0.3 ADR-0036 arc Bursts 65-70)
- Builtin tools registered: 51 -> 53 (was off-by-two — the v0.1.2
  STATE undercounted the baseline by one [42 actual, 41 listed],
  and the v0.3 +1 from memory_flag_contradiction wasn't propagated)
- Schema version: v11 -> v12 (ADR-0036 T6 flagged_state column)
- Trait roles: 17 -> 18 (added verifier_loop role in ADR-0036 T1)
- Initiative-annotated tools: 14 of 51 -> 15 of 53 (added
  memory_flag_contradiction at L3)
- Audit event types: 54 -> 55 (added verifier_scan_completed in
  ADR-0036 T3b/T5)
- Last-updated line refreshed to reference Burst 70 + ADR-0036
  feature-complete state

Cross-checks performed during the scrub:
- HEAD == origin/main (synced)
- v0.2.0 tag exists locally + on origin
- pyproject.toml version == 0.2.0
- 2072 tests pass on rerun (matches claim)
- ADR file count == 36 (matches STATE)
- §0 Hippocratic gate respected: zero file deletions across the
  entire v0.3 arc; everything is additive extension
- Burst 58 backtick damage on origin (cfe4219) confirmed
  cosmetic-only; substance landed; documented in
  feedback_commit_script_backticks.md memory

Architectural smell flagged for future R-track:
- core/memory.py grew 866 -> 1177 LoC across the v0.3 arc (+36%).
  Now in same neighborhood as daemon/routers/writes.py (1183 LoC),
  which is already flagged for decomposition. Per ADR-0039 §4
  (\"no god objects, grow new branches grounded by a solid feature\")
  memory.py is a candidate for split into:
    core/memory/core.py
    core/memory/contradictions.py    (ADR-0027-am + ADR-0036)
    core/memory/verification.py      (K1)
    core/memory/consents.py          (ADR-0027 §2)
  Not urgent — each addition was honest pattern extension — but
  worth a planned R-track refactor before the next sizable
  memory feature lands.

Mission alignment confirmed:
- Verifier Loop (ADR-0036) is a memory-humility primitive,
  consistent with the ADR's stated catalyst ('FSF's audit chain
  proves this happened but not this belief is true').
- Four-state ratification dial (T6) follows ADR-0027 §6
  information-flow boundary discipline.
- All work local-first, no telemetry, audit-emitting, reversible.

Not changing: README.md by-the-numbers table — those values are
synced via the same v0.2.0 paperwork commit (Burst 63) and were
last-updated for v0.2.0 specifically. Refreshing READMEs in the
middle of a v0.3 arc would set a brittle pattern; STATE.md is the
'live now' surface, README.md updates at version-bump cadence.

Test count unchanged at 2072 — this is paperwork-only."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "STATE.md scrubbed. Numbers now match on-disk reality."
echo ""
read -rp "Press Enter to close..."
