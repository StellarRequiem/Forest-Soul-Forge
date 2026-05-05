#!/usr/bin/env bash
# Burst 78: ADR-0040 T3.2 — extract writes/birth.py.
#
# Largest extraction in T3. Moves /birth + /spawn + _perform_create
# orchestrator + 10 creation helpers (~820 LoC) out of writes/__init__.py
# into writes/birth.py. _maybe_render_voice promoted from creation
# helpers to writes/_shared.py because /regenerate-voice (still in
# __init__.py until T3.3) also dispatches through it. Test suite stays
# green at 2072 passing.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 78 — ADR-0040 T3.2 writes/birth.py extraction ==="
echo
clean_locks
git add -A src/forest_soul_forge/daemon/routers/writes/
git add commit-burst78.command
clean_locks
git status --short
echo
clean_locks
git commit -m "refactor: ADR-0040 T3.2 — extract writes/birth.py (creation surface)

Largest extraction in T3. Moves the cohesive agent-creation surface
out of writes/__init__.py into writes/birth.py:
- /birth and /spawn endpoints
- _perform_create (the artifact-authoritative ordering orchestrator,
  ADR-0006: soul+constitution -> disk -> chain -> registry)
- 10 creation-specific helpers: _build_trait_profile,
  _parent_lineage_from_registry, _resolve_tool_kit,
  _resolve_tool_constraints, _resolve_enrich,
  _enforce_genre_kit_tier, _enforce_genre_trait_floors,
  _resolve_initiative_posture, _resolve_genre

writes/birth.py declares its own APIRouter with NO governance deps;
the parent router in writes/__init__.py declares require_writes_enabled
+ require_api_token and mounts birth via include_router(birth.router).
This avoids the dependency-double-stacking that include_router would
otherwise produce.

_maybe_render_voice promoted from creation helpers to writes/_shared.py
in this burst (was originally planned for T3.3) because the
/regenerate-voice handler — still in writes/__init__.py until T3.3 —
also dispatches through it. The shared utility surface is the right
home: /regenerate-voice and /birth+/spawn both render voice at write
time, the helper bridges asyncio internals across both, and putting
it in either sub-router would force cross-imports between siblings.

Trust-surface scope (per ADR-0040 §1):
An agent given allowed_paths to writes/birth.py can extend the
agent-creation logic — new genre rules, additional trait-floor
checks, alternate parent-lineage shapes — without inheriting the
ability to regenerate voices on existing agents (writes/__init__.py
for now, writes/voice.py after T3.3) or archive them
(writes/archive.py after T3.4).

Sizes after this burst:
- writes/__init__.py:  342 lines (was 1154 — net -812)
- writes/_shared.py:   148 lines (was   86 — net  +62, _maybe_render_voice)
- writes/birth.py:     863 lines (NEW)

Verification:
- import probe: writes_router.router resolves with all 4 routes
  (/birth, /spawn, /agents/{instance_id}/regenerate-voice, /archive).
  birth sub-router exposes /birth + /spawn; mounted via include_router.
- _shared.py voice helper imports cleanly.
- Full unit test suite: 2072 passed, 3 skipped, 1 xfailed.

Surgery notes (recorded for future-me):
- The _perform_create rationale comment block (R2 history + helper-
  parameter table) was reunited with _perform_create in birth.py
  during this burst. An earlier mechanical slice put it in _shared.py
  by mistake; I caught the boundary error before the test pass and
  moved it back where it belongs (the comment is load-bearing — it
  encodes ADR-0006/R2 rationale that future readers need adjacent
  to the function).
- _ingest was used by _perform_create (registry.ingest.parse_soul_file
  for soul-file roundtrip checking) but not initially imported into
  birth.py. Caught by the test suite's first-failure surface; added.
- compute_hardware_fingerprint is imported lazily inside the function
  body (line 573 of birth.py) — that pattern was preserved verbatim
  from the pre-extraction module.

Remaining T3 work:
- T3.3 — writes/voice.py: /regenerate-voice (~155 LoC). _maybe_render_voice
  already in _shared so the move is mechanical.
- T3.4 — writes/archive.py: /archive (~62 LoC). Closes T3."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 78 landed. ADR-0040 T3.2 complete. writes/birth.py shipped."
echo "Creation surface moved into its own grant-able file."
echo "Next: Burst 79 — T3.3 writes/voice.py extraction."
echo ""
read -rp "Press Enter to close..."
