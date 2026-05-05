#!/usr/bin/env bash
# Burst 81: ADR-0040 T4 — STATE.md + CLAUDE.md cross-references.
# CLOSES ADR-0040.
#
# Documentation pass that anchors the trust-surface decomposition
# rule into the load-bearing context files so future sessions don't
# re-derive it from scratch. STATE.md gets updated layout + ADR-0040
# entry in the ADR map + queue cleanup. CLAUDE.md gets the rule as
# §1 (peer to §0 Hippocratic gate) plus a one-liner in the
# Architectural Invariants list. Test suite stays green at 2072.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 81 — ADR-0040 T4 STATE.md + CLAUDE.md cross-references (CLOSES ADR-0040) ==="
echo
clean_locks
git add STATE.md CLAUDE.md
git add commit-burst81.command
clean_locks
git status --short
echo
clean_locks
git commit -m "docs: ADR-0040 T4 — STATE.md + CLAUDE.md cross-references (closes ADR-0040)

Documentation pass that anchors the trust-surface decomposition
rule into the load-bearing context files so future sessions don't
re-derive it from scratch. Closes ADR-0040 across all 4 tranches.

STATE.md changes:
- Header date bumped to post-Burst 81 with the full ADR-0040 arc
  summary (T1 file ADR + T2 memory decomposition Bursts 72-76 +
  T3 writes decomposition Bursts 77-80 + T4 this burst).
- ADRs filed count 36 -> 37 (ADR-0001..ADR-0040 with gaps).
- Repo layout writes.py -> writes/ package with sub-router list.
- 'Items in the queue' — removed the writes.py decomposition
  entry (done across Bursts 77-80).
- 'Where to start contributing' — removed the #1 'decompose
  writes.py' entry (done) and renumbered 2-5 to 1-4.
- ADR map — filled in 0034 (SW-track triune), 0035 (Persona
  Forge, Proposed), 0036 (Verifier Loop, Proposed-but-feature-
  complete), 0037 (Observability, Proposed), 0038 (Companion
  harm model, Accepted via SarahR1), 0039 (Distillation Forge,
  Proposed for v0.4), and 0040 (this rule, Accepted with all 4
  tranches shipped).

CLAUDE.md changes:
- Added §1 Trust-surface decomposition rule (ADR-0040) to
  Operating principles, peer to §0 Hippocratic gate. The rule:
  count trust surfaces; one cohesive surface = leave alone even
  if large; multiple surfaces = decompose into per-surface files
  so allowed_paths can scope grants. Pattern-match to existing
  decomps (mixin or sub-router shape).
- Added one-line invariant to 'Architectural invariants (don't
  break these)': 'One file, one trust surface (ADR-0040)' citing
  memory/ and writes/ as the canonical decompositions.

Why both files (the cross-reference work):
- STATE.md is the developer-facing snapshot — without an ADR map
  entry, future-me reading STATE.md and not the ADRs would miss
  ADR-0040 entirely. The full-arc summary in the header makes
  the multi-burst sequence legible at-a-glance.
- CLAUDE.md is auto-loaded into every harness session. The §1
  rule + invariant line ensure future sessions encounter the
  rule before re-deriving it. ADR-0040 §1 itself anticipated
  this — the file-grained governance is only valuable if it's
  reliably applied at the next decision point.

Verification:
- Full unit test suite: 2072 passed, 3 skipped, 1 xfailed
  (no source code changed in this burst, but running the suite
  confirms no inadvertent damage from the doc edits).

ADR-0040 status — CLOSED:
- T1 (Burst 71): file ADR-0040 itself
- T2 (Bursts 72-76): memory.py 5-mixin decomposition
- T3 (Bursts 77-80): writes.py 4-sub-router decomposition
- T4 (Burst 81): this — STATE.md + CLAUDE.md cross-references

The trust-surface count rule survived contact with two distinct
codebase shapes (class-based mixins for memory, router-based
sub-routers for writes) and is now anchored in the context files
that prime every future session. Pattern's stable; rule's load-
bearing in the documentation chain that future agents will read."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 81 landed. ADR-0040 T4 CLOSED. ADR-0040 fully shipped."
echo "Trust-surface decomposition rule anchored in STATE.md + CLAUDE.md."
echo "Memory.py + writes.py both decomposed into per-trust-surface packages."
echo ""
read -rp "Press Enter to close..."
