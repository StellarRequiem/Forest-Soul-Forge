#!/usr/bin/env bash
# Burst 71: ADR-0040 — Trust-Surface Decomposition Rule.
#
# Captures the principle from the 2026-05-02 orchestrator conversation
# about god objects, agent governance, and AI-grade safeties. Files the
# rule and the decomposition queue (memory.py, writes.py); the actual
# refactors are T2-T3 in subsequent bursts.
#
# T1 only this burst — keeps scope bounded; the principle is the load-
# bearing artifact. Test count unchanged (2072 passing) — no code
# changes.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 71 — ADR-0040 trust-surface decomposition rule ==="
echo
clean_locks
git add docs/decisions/ADR-0040-trust-surface-decomposition-rule.md \
        commit-burst71.command
clean_locks
git status --short
echo
clean_locks
git commit -m "ADR-0040: trust-surface decomposition rule (Accepted)

Captures the principle from the 2026-05-02 orchestrator conversation
about god objects, agent governance, and AI-grade safeties. The
synthesis: god objects are fine for cohesive surfaces (one trust
surface, large file) but not for non-cohesive ones (multiple trust
surfaces in one file), because FSF's allowed_paths governance is
file-grained — a file mixing N trust surfaces forces every agent
constraint targeting that file to inherit the union of all N
policies, collapsing the genre/initiative-level/kit governance
discipline into 'do you have the file or not.'

Key sections:
- Sec.1 — trust-surface count rule (a file warrants decomposition
  if it bundles >= 2 distinct trust surfaces, regardless of LoC).
- Sec.2 — cohesive god objects are fine if Sec.3 holds. Lists
  current cohesive god objects in the codebase: dispatcher.py,
  audit_chain.py, constitution.py, trait_engine.py, genre_engine.py.
- Sec.3 — required safeties for cohesive god objects: per-public-
  method invariant docstrings; property-based invariant tests;
  pre-commit static analysis (already shipped as Phase G.1.A);
  audit-chain coverage; behavioral signature tests.
- Sec.4 — non-cohesive god objects MUST decompose.
- Sec.5 — required decomposition list:
  * core/memory.py (5 trust surfaces: core CRUD, consents,
    verification, challenge, contradictions) → core/memory/ package
  * daemon/routers/writes.py (~9 endpoints) → writes/ package
- Sec.6 — apply at trust-surface boundary, not size threshold.
- Sec.7 — mixin-class pattern for class-level decomposition
  (preserves public API exactly).

Trade-offs section explicitly rejects:
- Just keep god objects + add safeties (insufficient for
  governance scope blast radius)
- Mandatory size threshold (causes premature decomposition of
  cohesive surfaces)
- Composition over mixins (breaks public API)
- Defer all decomposition (concerns accrete; cost compounds)
- Method-level allowed_paths (same work as splitting, weaker
  resulting safety)

Implementation tranches:
- T1: file this ADR (this burst)
- T2: apply rule to core/memory.py (estimated 2-3 bursts)
- T3: apply rule to daemon/routers/writes.py (2-3 bursts)
- T4: STATE.md / CLAUDE.md cross-references

After T1-T4 land, the rule is the operational discipline. Future
ADRs that introduce new trust surfaces file their own package
from the start.

Test count unchanged (2072 passing) — no code changes this burst.

Promoted to Accepted on landing because the principle was already
endorsed by the orchestrator in conversation; the ADR captures
the wording, not a tentative proposal.

Next: Burst 72 — start T2 (apply rule to core/memory.py).
Approach: convert core/memory.py to core/memory/__init__.py first
(safe rename), then extract _helpers.py (lowest-risk piece), then
per-surface mixin extractions in subsequent bursts. Test suite
green at every step."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 71 landed. ADR-0040 in production."
echo "Trust-surface decomposition rule documented; queue committed."
echo "Next: Burst 72 — start applying rule to core/memory.py (T2)."
echo ""
read -rp "Press Enter to close..."
