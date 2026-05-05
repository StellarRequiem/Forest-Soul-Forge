#!/usr/bin/env bash
# Burst 73: ADR-0040 T2.2 — extract _consents_mixin.py.
#
# First per-surface mixin extraction. grant_consent / revoke_consent
# / is_consented move out of the Memory class body into a
# _ConsentsMixin class in _consents_mixin.py. Memory class inherits
# from the mixin. Public API exactly preserved.
#
# Test suite stays green at 2072 passing.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 73 — ADR-0040 T2.2 _consents_mixin.py extraction ==="
echo
clean_locks
git add src/forest_soul_forge/core/memory/__init__.py \
        src/forest_soul_forge/core/memory/_consents_mixin.py \
        commit-burst73.command
clean_locks
git status --short
echo
clean_locks
git commit -m "refactor: ADR-0040 T2.2 — extract _consents_mixin.py

First per-trust-surface mixin extraction per ADR-0040 §7. Moves
grant_consent / revoke_consent / is_consented out of the Memory
class body into a _ConsentsMixin class in _consents_mixin.py. The
Memory class now declares 'class Memory(_ConsentsMixin)' to
inherit the methods through the MRO. Public API exactly preserved
— memory.grant_consent(...) still works the same.

Trust surface owned by this mixin: cross-agent disclosure boundary
(ADR-0027 §2). The memory_consents table records who has been
granted access to whose entries. An agent given allowed_paths to
_consents_mixin.py can extend the consent grant model — adding
TTL / expiry, per-recipient permission scopes, etc. — without
inheriting the ability to flag contradictions, mark verifications,
or write core memory rows. That's the file-grained governance
ADR-0040 §1 identifies as the value of decomposing non-cohesive
god objects.

Mixin design notes (preserved across all subsequent T2.x extractions):
- The mixin's methods reference self.conn directly. The Memory
  class's __init__ populates self.conn; the mixin doesn't define
  its own __init__.
- _now_iso is imported from _helpers.py at module scope.
- Audit-chain emission stays in the runtime — the mixin only
  records the row. Comment preserved verbatim from the original.
- Mixin file is ~100 LoC; well under any cohesion / godliness
  threshold.

Verification:
- All five Memory methods (grant_consent, revoke_consent,
  is_consented, append, flag_contradiction) confirmed present on
  the class via hasattr() probe.
- Full test suite passes: 2072 passed, 3 skipped, 1 xfailed.

Sizes after this burst:
- __init__.py: 984 -> 931 lines (-53)
- _helpers.py: 253 lines (unchanged)
- _consents_mixin.py: 100 lines (NEW)

Subsequent T2.x extractions follow the same pattern:
- T2.3: _verification_mixin.py (Iron Gate — mark_verified /
  unmark_verified / is_verified / get_verifier)
- T2.4: _challenge_mixin.py (mark_challenged / is_entry_stale)
- T2.5: _contradictions_mixin.py (flag_contradiction /
  set_contradiction_state / find_candidate_pairs /
  unresolved_contradictions_for + the contradictions stopwords)
- After T2.5, what's left in __init__.py is the core CRUD path
  (append / recall / get / count / soft_delete / purge) plus
  the class declaration assembling the mixins. That residual
  surface is the 'core memory' trust surface and stays in
  __init__.py rather than being extracted to its own file."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 73 landed. ADR-0040 T2.2 complete."
echo "First mixin extraction shipped; pattern proven; 4 more to go."
echo "Next: Burst 74 — _verification_mixin.py extraction."
echo ""
read -rp "Press Enter to close..."
