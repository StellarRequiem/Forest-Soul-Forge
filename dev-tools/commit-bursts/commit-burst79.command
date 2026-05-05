#!/usr/bin/env bash
# Burst 79: ADR-0040 T3.3 — extract writes/voice.py.
#
# Third extraction in T3. Moves /regenerate-voice (the LLM voice
# regeneration surface, ADR-0017 follow-up) out of writes/__init__.py
# into writes/voice.py. Same sub-router pattern as birth.py — empty
# deps, mounted via include_router under the parent package's deps.
# Test suite stays green at 2072 passing.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 79 — ADR-0040 T3.3 writes/voice.py extraction ==="
echo
clean_locks
git add -A src/forest_soul_forge/daemon/routers/writes/
git add commit-burst79.command
clean_locks
git status --short
echo
clean_locks
git commit -m "refactor: ADR-0040 T3.3 — extract writes/voice.py

Third extraction in T3. Moves the /regenerate-voice handler out
of writes/__init__.py into its own writes/voice.py sub-router.
Same shape as the birth extraction (T3.2): the sub-router declares
no governance deps; the parent package router holds those, and
include_router applies them to the included routes once.

Trust surface owned by this file (per ADR-0040 §1):
voice-regeneration governance — soul.md frontmatter parsing,
trait-profile reconstruction from disk, voice render dispatch,
soul.md in-place update, voice_regenerated audit event. An agent
given allowed_paths to writes/voice.py can extend voice-iteration
logic — alternate frontmatter formats, multi-provider voice racing,
regression diffing — without inheriting the ability to create new
agents (writes/birth.py) or archive them (writes/archive.py after
T3.4).

Why this surface stays distinct from birth:
The /regenerate-voice endpoint operates on EXISTING agents. It
preserves the agent's identity (dna, instance_id, constitution_hash)
and only rewrites the soul.md ## Voice section + three narrative_*
frontmatter fields. None of the creation governance applies — no
genre kit-tier check, no trait-floor validation, no parent lineage
construction, no constitution-hash recomputation. ADR-0040 §1 is
explicit that 'governance surface' is the right axis for splitting,
not LoC-shape similarity.

Sizes after this burst:
- writes/__init__.py: 171 lines (was 342 — net -171)
- writes/voice.py:    233 lines (NEW)
- writes/birth.py:    863 lines (unchanged)
- writes/_shared.py:  148 lines (unchanged)

After T3.3, writes/__init__.py contains:
  - package docstring + filtered imports
  - parent APIRouter declaration
  - 2 include_router calls (birth + voice)
  - the /archive handler (still here — moves out in T3.4)

Verification:
- import probe: parent router exposes all 4 routes; voice
  sub-router exposes /agents/{instance_id}/regenerate-voice on
  its own.
- Full unit test suite: 2072 passed, 3 skipped, 1 xfailed.

Unused-import cleanup:
After moving /regenerate-voice out, writes/__init__.py no longer
needs:
  - Path, threading.Path-only constructs
  - Lineage, GenreEngine, TraitEngine + trait engine errors
  - DaemonSettings, ProviderRegistry
  - VoiceText, update_soul_voice
  - get_genre_engine, get_provider_registry, get_settings,
    get_trait_engine
  - _ingest (only regenerate_voice parsed soul.md frontmatter)
The /archive handler — the only remaining @router decorator in
__init__.py — uses a much smaller import set. T3.4 will leave
writes/__init__.py as a pure facade with no decorators of its own.

Remaining T3 work:
- T3.4 — writes/archive.py: /archive (~62 LoC). Closes T3."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 79 landed. ADR-0040 T3.3 complete. writes/voice.py shipped."
echo "Voice-regeneration surface moved into its own grant-able file."
echo "Next: Burst 80 — T3.4 writes/archive.py extraction (closes T3)."
echo ""
read -rp "Press Enter to close..."
