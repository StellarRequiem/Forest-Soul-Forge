#!/usr/bin/env bash
# Burst 72: ADR-0040 T2.1 — memory.py -> memory/ package + _helpers extraction.
#
# First step of the memory.py decomposition. Mechanical move only;
# zero behavior change. Test suite stays green at 2072 passing.
#
# Per-trust-surface mixin extractions follow in Burst 73+.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 72 — ADR-0040 T2.1 memory.py -> memory/ package ==="
echo
clean_locks
git add src/forest_soul_forge/core/memory/__init__.py \
        src/forest_soul_forge/core/memory/_helpers.py \
        commit-burst72.command
clean_locks
git status --short
echo
clean_locks
git commit -m "refactor: ADR-0040 T2.1 — memory.py -> memory/ package + _helpers

First step of the core/memory.py decomposition per ADR-0040 §5+§7.
Mechanical move; zero behavior change. Test suite stays green at
2072 passing. Per-trust-surface mixin extractions follow in
subsequent bursts.

Step 1: convert core/memory.py to core/memory/ package.
- git mv core/memory.py core/memory/__init__.py
- Python treats them identically; all 'from forest_soul_forge.core.memory
  import X' callers continue to work without modification.
- Verified by full test rerun before continuing to step 2.

Step 2: extract _helpers.py from __init__.py.
Moved out of the Memory class file into core/memory/_helpers.py:
- Constants (LAYERS, SCOPES, RECALL_MODES, GENRE_CEILINGS,
  CLAIM_TYPES, CONFIDENCE_LEVELS, plus the private _SCOPE_RANK,
  _CLAIM_TYPE_SET, _CONFIDENCE_SET sets)
- Error classes (MemoryError + 5 subclasses)
- The MemoryEntry dataclass
- Module-level helpers (_now_iso, _sha256, _row_to_entry,
  _OVERLAP_STOPWORDS, _tokenize_for_overlap)

The Memory class itself stays in core/memory/__init__.py and
imports from _helpers. The package __init__.py re-exports
everything via __all__ so the public API is exactly preserved:
  from forest_soul_forge.core.memory import Memory       # works
  from forest_soul_forge.core.memory import MemoryEntry  # works
  from forest_soul_forge.core.memory import _now_iso     # works
  ... and so on for every existing import.

Verification:
- 16 distinct call sites that import from forest_soul_forge.core.memory
  surveyed before the move; all bound to names that survive the
  re-export.
- Imports verified live: Memory, MemoryEntry, MemoryScopeViolation,
  UnknownClaimTypeError, GENRE_CEILINGS, CLAIM_TYPES, _now_iso,
  _sha256, _tokenize_for_overlap all resolve cleanly.
- Full test suite passes: 2072 passed, 3 skipped, 1 xfailed.

Sizes:
- core/memory/__init__.py: 984 lines (down from 1177; will shrink
  more as mixins are extracted)
- core/memory/_helpers.py: 253 lines (constants + errors + dataclass
  + pure helpers; single trust surface = read-only data)

Why _helpers.py is its own trust surface per ADR-0040:
This file holds CONSTANTS and PURE FUNCTIONS only — no state-mutating
logic. An agent given allowed_paths access to _helpers.py can
contribute new claim-type enums, new stopwords, new helper functions,
but CAN'T touch the Memory class's flag_contradiction, flag a
memory entry, change consents, etc. That's the exact scope tightening
ADR-0040 §1 identifies as the value of decomposing non-cohesive
god objects.

Subsequent bursts (T2.2+) extract per-surface mixins:
- _core_mixin.py  — append/recall/get/count/soft_delete/purge
- _consents_mixin.py — grant_consent/revoke_consent/is_consented
- _verification_mixin.py — mark_verified/unmark_verified/is_verified/
  get_verifier (ADR-003X K1 Iron Gate)
- _challenge_mixin.py — mark_challenged/is_entry_stale (ADR-0027-am
  §7.4)
- _contradictions_mixin.py — flag_contradiction/set_contradiction_state/
  find_candidate_pairs/unresolved_contradictions_for (ADR-0027-am §7.3
  + ADR-0036)
- __init__.py keeps a thin Memory(...mixins...) class definition.

After the full split lands, agent constitutions can target the
specific mixin file an agent is allowed to touch, and the Verifier
loop can edit _contradictions_mixin.py without inheriting access
to consent grants or verification stamps. That's the governance
scope tightening the rule was filed for."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 72 landed. ADR-0040 T2.1 complete. memory/ package + _helpers.py shipped."
echo "Test suite green at 2072 passing. Public API exactly preserved."
echo "Next: Burst 73 — start per-surface mixin extractions (T2.2)."
echo ""
read -rp "Press Enter to close..."
