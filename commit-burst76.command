#!/usr/bin/env bash
# Burst 76: ADR-0040 T2.5 — extract _contradictions_mixin.py.
#
# FINAL mixin extraction. Closes ADR-0040 T2 (memory.py decomposition).
# Memory class now composed of 4 mixins + core CRUD body. Test suite
# stays green at 2072 passing.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 76 — ADR-0040 T2.5 _contradictions_mixin.py extraction ==="
echo
clean_locks
git add src/forest_soul_forge/core/memory/__init__.py \
        src/forest_soul_forge/core/memory/_contradictions_mixin.py \
        commit-burst76.command
clean_locks
git status --short
echo
clean_locks
git commit -m "refactor: ADR-0040 T2.5 — extract _contradictions_mixin.py (closes T2)

FINAL per-trust-surface mixin extraction. Moves flag_contradiction
+ set_contradiction_state + find_candidate_pairs +
unresolved_contradictions_for + VALID_FLAGGED_STATES out of the
Memory class body into _ContradictionsMixin in
_contradictions_mixin.py. Memory class declaration finalized:
  class Memory(_ConsentsMixin, _VerificationMixin,
               _ChallengeMixin, _ContradictionsMixin)

Trust surface owned by this mixin: cross-entry contradiction
tracking (ADR-0027-am §7.3 + ADR-0036). Verifier agents flag
contradictions; operators ratify them through the flagged_state
lifecycle (Burst 70 T6+T7 work). All four contradiction-table
methods + the VALID_FLAGGED_STATES enum live together because
they share both the table and the lifecycle semantics.

Methods:
- flag_contradiction: writer. Stamps a contradiction row naming
  both sides + kind enum from ADR-0027-am §7.3.
- set_contradiction_state: writer. Operator ratification —
  flagged_unreviewed -> {confirmed, rejected, auto_resolved}.
- find_candidate_pairs: read-only. Pre-filter for Verifier Loop
  scan (ADR-0036 §2.1). Uses _tokenize_for_overlap from _helpers.
- unresolved_contradictions_for: read-only. Recall surface
  (ADR-0036 T7). Default-filters flagged_rejected.

Closes ADR-0040 T2 (the entire memory.py decomposition queue).
Final layout under src/forest_soul_forge/core/memory/:
  __init__.py              465 lines (Memory class + core CRUD)
  _helpers.py              253 lines (constants/errors/dataclass/helpers)
  _consents_mixin.py       100 lines
  _verification_mixin.py   121 lines
  _challenge_mixin.py      136 lines
  _contradictions_mixin.py 388 lines
  ----------------------------------
  Total                   1463 lines

What stays in __init__.py: core CRUD trust surface — append /
recall / get / count / soft_delete / purge — plus the class
declaration assembling the mixins. That residual surface IS the
'core memory' trust surface per ADR-0040 §1; it's intentionally
not extracted because the methods there are genuinely cohesive
(they all touch memory_entries directly + share the genre ceiling
+ scope check + claim_type/confidence validation infrastructure).

Verification:
- All Memory methods (grant_consent, mark_verified, mark_challenged,
  flag_contradiction, set_contradiction_state, find_candidate_pairs,
  unresolved_contradictions_for, append, recall, get, count,
  soft_delete, purge) confirmed present via hasattr probe.
- VALID_FLAGGED_STATES still resolves on the class via
  _ContradictionsMixin (the Verifier scan runner reads it).
- MRO: Memory -> _ConsentsMixin -> _VerificationMixin ->
        _ChallengeMixin -> _ContradictionsMixin -> object
- Full test suite: 2072 passed, 3 skipped, 1 xfailed.

ADR-0040 status after this burst:
- T1 (file ADR-0040): shipped Burst 71
- T2 (memory.py decomposition): CLOSED this burst
  - T2.1 helpers extraction: shipped Burst 72
  - T2.2 _consents_mixin: shipped Burst 73
  - T2.3 _verification_mixin: shipped Burst 74
  - T2.4 _challenge_mixin: shipped Burst 75
  - T2.5 _contradictions_mixin: this burst
- T3 (apply rule to writes.py — 1183 LoC, 9 endpoints): pending
- T4 (cross-references in STATE.md / CLAUDE.md): pending

The package layout now lets a future agent constitution declare
allowed_paths surgically by trust surface — e.g., a Verifier role
gets _contradictions_mixin.py, and a verified-memory operator role
gets _verification_mixin.py, without either inheriting access to
the other surface. That's the file-grained governance value
ADR-0040 §1 was designed to deliver."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 76 landed. ADR-0040 T2 CLOSED. _contradictions_mixin.py shipped."
echo "memory.py decomposition complete. 4 of 4 mixins extracted."
echo "Next: T3 — apply ADR-0040 rule to writes.py (1183 LoC, 9 endpoints)."
echo ""
read -rp "Press Enter to close..."
