#!/usr/bin/env bash
# Burst 77: ADR-0040 T3.1 — convert writes.py to package + extract _shared.py.
#
# First step in writes.py decomposition. Converts the single-file
# router to a package layout so subsequent bursts (T3.2-T3.4) can
# extract per-endpoint sub-routers without disturbing the public
# import path. _shared.py owns the idempotency-replay helpers used
# by every write endpoint. Test suite stays green at 2072 passing.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 77 — ADR-0040 T3.1 writes.py → writes/ package + _shared.py ==="
echo
clean_locks
# git add -A picks up the rename (writes.py -> writes/__init__.py),
# the new _shared.py, and the burst script. Without -A the deletion
# of the old writes.py wouldn't be captured.
git add -A src/forest_soul_forge/daemon/routers/
git add commit-burst77.command
clean_locks
git status --short
echo
clean_locks
git commit -m "refactor: ADR-0040 T3.1 — writes.py → writes/ package + _shared.py

First step in writes.py decomposition (ADR-0040 T3, the second
non-cohesive god object). Converts the single-file router to a
package so subsequent bursts (T3.2-T3.4) can extract per-endpoint
sub-routers without breaking the public import path.

§0 Hippocratic gate (verified before action):
1. Prove harm: an agent given allowed_paths to writes.py to extend
   voice-prompt iteration ALSO inherits the ability to rewrite
   genre kit-tier enforcement. File-grained-scope failure ADR-0040
   §1 was filed to prevent.
2. Prove non-load-bearing: only one consumer (app.py:55 imports
   'writes_router.router'). Package layout preserves that symbol.
3. Prove alternative is strictly better: allowed_paths is
   file-grained, not symbol-grained. Decomposition is the only
   mechanism to deliver scoped governance for this file.

What moved this burst:
- src/forest_soul_forge/daemon/routers/writes.py renamed to
  src/forest_soul_forge/daemon/routers/writes/__init__.py
  (git rename detection should pick this up — same body except
   for the docstring update + the helper extraction).
- _maybe_replay_cached + _cache_response moved to writes/_shared.py.
  These two helpers are reused by every endpoint (/birth, /spawn,
  /regenerate-voice, /archive); pulling them into _shared lets
  per-endpoint sub-routers (landing in T3.2-T3.4) import them
  without inheriting the creation-surface helpers above.

Trust-surface scope (per ADR-0040 §1):
_shared.py is the *shared utility surface* — code that genuinely
benefits from reuse without expanding any one sub-router into the
others' governance domain. An agent given allowed_paths to a
sub-router does NOT automatically inherit _shared.py.

Verification:
- import probe: writes_router.router resolves; routes register
  correctly (/birth, /spawn, /agents/{instance_id}/regenerate-voice,
  /archive — all 4 endpoints intact).
- _shared helpers import cleanly under the new path.
- Full unit test suite: 2072 passed, 3 skipped, 1 xfailed.

Sizes after this burst:
- writes/__init__.py: 1154 lines (was 1183 — net -29 from helper
  extraction + unused-import cleanup)
- writes/_shared.py:    86 lines (NEW)

Unused-import cleanup (the helpers I extracted were the only
callers of these in writes.py — they live in _shared.py now):
- Response from fastapi
- IdempotencyMismatchError from registry.registry
- idempotency_now alias from birth_pipeline

Remaining T3 work:
- T3.2 (next burst) — writes/birth.py: /birth + /spawn + the entire
  _perform_create helper stack (~820 LoC, the cohesive creation
  surface).
- T3.3 — writes/voice.py: /regenerate-voice (~155 LoC). Will
  promote _maybe_render_voice from creation helpers to _shared.py
  since both birth and voice use it.
- T3.4 — writes/archive.py: /archive (~62 LoC). Closes T3 — at
  that point writes/__init__.py is the APIRouter facade plus
  include_router calls."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 77 landed. ADR-0040 T3.1 complete. writes/ package layout established."
echo "Next: Burst 78 — T3.2 writes/birth.py extraction (the big creation surface)."
echo ""
read -rp "Press Enter to close..."
