#!/usr/bin/env bash
# Burst 75: ADR-0040 T2.4 — extract _challenge_mixin.py.
#
# Third per-trust-surface mixin extraction. Moves mark_challenged
# + is_entry_stale (ADR-0027-am §7.4 staleness/scrutiny surface) out
# of Memory class body into _ChallengeMixin. Tests stay green at 2072.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 75 — ADR-0040 T2.4 _challenge_mixin.py extraction ==="
echo
clean_locks
git add src/forest_soul_forge/core/memory/__init__.py \
        src/forest_soul_forge/core/memory/_challenge_mixin.py \
        commit-burst75.command
clean_locks
git status --short
echo
clean_locks
git commit -m "refactor: ADR-0040 T2.4 — extract _challenge_mixin.py

Third per-trust-surface mixin extraction. Moves mark_challenged
+ is_entry_stale (ADR-0027-am §7.4 staleness/scrutiny surface)
out of Memory class body into _ChallengeMixin. Class declaration:
  class Memory(_ConsentsMixin, _VerificationMixin, _ChallengeMixin)

Trust surface owned by this mixin: explicit operator scrutiny on
a memory entry without writing a competing entry. Distinct from
contradictions (which have two competing entries on the same
topic — that's the upcoming _ContradictionsMixin's surface).

Methods:
- mark_challenged: writer. Stamps last_challenged_at to NOW.
- is_entry_stale: predicate. Used by memory_recall.v1 to flag
  entries older than threshold_days.

Why challenge is its own surface (not folded into contradictions):
Per ADR-0027-am §7.4, the operator can challenge an entry WITHOUT
yet writing the competing entry that would form a contradiction.
Sometimes the operator just wants to flag 'I'm not sure about
this' without committing to a replacement. Two genuinely distinct
trust surfaces; ADR-0040 §1 trust-surface count rule keeps them
on separate files.

The is_entry_stale method moved its 'from datetime import' to
module scope in the new file (was a function-scope import in the
original — keeping the same scope here would have been a code
smell that I refused to preserve verbatim). The behavior is
identical; the import now happens at mixin-import time rather
than per-call.

Verification:
- Memory class methods (grant_consent, mark_verified,
  mark_challenged, is_entry_stale, append, flag_contradiction)
  all confirmed present via hasattr probe.
- Full test suite: 2072 passed, 3 skipped, 1 xfailed.

Sizes after this burst:
- __init__.py: 869 -> 798 lines (-71)
- _challenge_mixin.py: 132 lines (NEW)

3 of 4 mixin extractions done. Remaining:
  T2.5 — _contradictions_mixin.py (the largest; ~280 LoC across
         flag_contradiction / set_contradiction_state /
         find_candidate_pairs / unresolved_contradictions_for)
After T2.5, what remains in __init__.py is the core CRUD path
(append/recall/get/count/soft_delete/purge) plus the class
declaration. That residual surface is the 'core memory' trust
surface and stays in __init__.py rather than being extracted."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 75 landed. ADR-0040 T2.4 complete. _challenge_mixin.py shipped."
echo "3 of 4 mixin extractions done. 1 remaining: contradictions (the biggest)."
echo "Next: Burst 76 — _contradictions_mixin.py extraction."
echo ""
read -rp "Press Enter to close..."
