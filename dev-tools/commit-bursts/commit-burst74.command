#!/usr/bin/env bash
# Burst 74: ADR-0040 T2.3 — extract _verification_mixin.py.
#
# Iron Gate (ADR-003X K1) trust surface extracted out of the Memory
# class body into _VerificationMixin. Same pattern as T2.2 consents.
# Test suite stays green at 2072 passing.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 74 — ADR-0040 T2.3 _verification_mixin.py extraction ==="
echo
clean_locks
git add src/forest_soul_forge/core/memory/__init__.py \
        src/forest_soul_forge/core/memory/_verification_mixin.py \
        commit-burst74.command
clean_locks
git status --short
echo
clean_locks
git commit -m "refactor: ADR-0040 T2.3 — extract _verification_mixin.py

Second per-trust-surface mixin extraction. Moves mark_verified /
unmark_verified / is_verified / get_verifier (Iron Gate, ADR-003X K1)
out of Memory class body into _VerificationMixin in
_verification_mixin.py. Memory class declaration extended:
  class Memory(_ConsentsMixin, _VerificationMixin)

Trust surface owned by this mixin: verified-memory promotion. The
Iron Gate primitive — an external human verifier promotes a memory
entry to confidence='high' standing. Reads via memory_recall.v1's
K1 fold; writes via memory_verify.v1 tool (operator-only by
constitutional kit gate).

Why a separate file specifically (per ADR-0040 §1):
- Iron Gate is operator-driven only — agents never autonomously
  promote entries to verified.
- The verifier identifier is a human handle (key fingerprint /
  signing handle), not an agent instance_id.
- An agent given allowed_paths to _verification_mixin.py could
  extend the Iron Gate model — multi-signature verification,
  per-verifier reputation, etc. — without inheriting consent
  grants, contradiction flagging, or core memory writes.

Sizes after this burst:
- __init__.py: 931 -> 869 lines (-62)
- _verification_mixin.py: 119 lines (NEW)

Verification:
- All Memory methods (grant_consent, mark_verified, unmark_verified,
  is_verified, get_verifier, append, flag_contradiction) confirmed
  present on the class via hasattr probe.
- Full test suite: 2072 passed, 3 skipped, 1 xfailed.

Pattern proven across 2 mixin extractions now:
  T2.2 (consents)      shipped 32f0f3d
  T2.3 (verification)  this burst

Remaining mixin extractions:
  T2.4 — _challenge_mixin.py (mark_challenged + is_entry_stale)
  T2.5 — _contradictions_mixin.py (flag_contradiction +
         set_contradiction_state + find_candidate_pairs +
         unresolved_contradictions_for + the helper imports)
After T2.5, what remains in __init__.py is the core CRUD trust
surface (append/recall/get/count/soft_delete/purge) plus the class
declaration assembling the mixins."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 74 landed. ADR-0040 T2.3 complete. _verification_mixin.py shipped."
echo "2 of 4 mixin extractions done. 2 remaining: challenge + contradictions."
echo "Next: Burst 75 — _challenge_mixin.py extraction."
echo ""
read -rp "Press Enter to close..."
