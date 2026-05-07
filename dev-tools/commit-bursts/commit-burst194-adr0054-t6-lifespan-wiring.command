#!/bin/bash
# Burst 194 — ADR-0054 T6 — lifespan wiring (flip the master
# switch). Final tranche of the procedural-shortcut substrate.
#
# Substrate has been sitting 80% built since B182 (5/6 tranches
# shipped, master switch defaulted off). This burst wires it
# into the dispatcher at app.lifespan so the existing
# ProceduralShortcutStep has real tables + closures to call.
#
# Default state stays OFF. Operators opt in by setting
# FSF_PROCEDURAL_SHORTCUT_ENABLED=1 in .env + restarting the
# daemon. With it on, every llm_think dispatch checks for a
# matching shortcut BEFORE firing the LLM:
#   - cosine match >= 0.92 (default; tunable)
#   - reinforcement score >= 2 (default; tunable)
#   - if both pass: substitute the recorded response, emit
#     tool_call_shortcut audit event, return in <100ms
#
# What ships:
#
#   src/forest_soul_forge/daemon/config.py:
#     - 4 new DaemonSettings fields under section header
#       'ADR-0054 T6 — procedural-shortcut substrate (opt-in)':
#         * procedural_shortcut_enabled (bool, default False) —
#           master switch
#         * procedural_cosine_floor (float, default 0.92) —
#           ADR-0054 D2 baseline
#         * procedural_reinforcement_floor (int, default 2) —
#           ADR-0054 D2 baseline
#         * procedural_embed_model (str, default
#           'nomic-embed-text:latest') — Forest standing baseline
#     - All 4 mapped via FSF_PROCEDURAL_* env vars per
#       pydantic-settings convention.
#
#   src/forest_soul_forge/daemon/deps.py:
#     - In build_or_get_tool_dispatcher, after dispatcher
#       construction: build ProceduralShortcutsTable from
#       fsf_registry._conn (single-writer SQLite discipline
#       preserved via the daemon's write lock — the table reads
#       and writes only when the dispatcher's pipeline holds the
#       lock).
#     - Inject 4 closure-style getters onto the dispatcher
#       (procedural_shortcut_enabled_fn, procedural_cosine_floor_fn,
#       procedural_reinforcement_floor_fn, procedural_embed_model_fn).
#       Closures (not constants) so an env-var flip + restart
#       picks up new values without code change.
#     - Defensive try/except around the wiring — any failure
#       (table construction, settings read, etc.) does NOT crash
#       daemon startup. Dispatcher's _resolve_shortcut_match
#       handles None table by short-circuiting to no-match.
#
# Per ADR-0054 D1 + ADR-0001 D2: shortcuts are per-instance state,
# not identity. constitution_hash + DNA stay constant across
# table growth. Operators can rebuild the table freely (or wipe
# it via DELETE FROM memory_procedural_shortcuts WHERE instance_id=?)
# without touching agent identity.
#
# Per ADR-0044 D3: zero ABI changes. New settings fields all have
# safe defaults; pre-T6 .env files unaffected. Pre-T6 daemons
# reading post-T6 schema (the procedural_shortcuts table that
# B178 added at v15→v16) just don't query it because their
# dispatcher doesn't have the table reference. Post-T6 daemons
# reading pre-T6 schemas would fail at table construction —
# but only schemas at v15+ have the table; the migration ran
# at B178 so any current daemon has v16 already.
#
# Verification:
#   - DaemonSettings() defaults: procedural_shortcut_enabled=False,
#     cosine_floor=0.92, reinforcement_floor=2,
#     embed_model='nomic-embed-text:latest'
#   - build_app() imports clean with substrate OFF
#   - build_app(DaemonSettings(procedural_shortcut_enabled=True))
#     imports clean with substrate ON
#   - 180 passed across procedural_shortcut_dispatch +
#     tool_dispatcher + governance_pipeline +
#     procedural_shortcuts test files
#
# Operator-facing follow-up (NOT in this commit):
#   - To turn on: append FSF_PROCEDURAL_SHORTCUT_ENABLED=true to
#     /Users/llm01/Forest-Soul-Forge/.env, then run
#     dev-tools/restart-daemon.command. With the switch on,
#     dispatch resolution becomes: pipeline gates → posture →
#     mode-kit-clamp → SHORTCUT (if match) → otherwise LLM.
#   - First few dispatches won't have any shortcuts to match
#     (the table is empty). Operators populate the table either
#     via auto-capture (T7 future tranche) or explicitly via
#     `INSERT INTO memory_procedural_shortcuts (...)`.
#   - The chat-tab thumbs UI (T5b — B195 next) lets operators
#     reinforce hits via memory_tag_outcome.v1 tagging.
#
# This burst CLOSES the ADR-0054 substrate. T5b ships the
# operator-facing reinforcement UI. Together: fast-path
# response substitution + operator reinforcement loop, both
# opt-in, zero default behavior change.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/config.py \
        src/forest_soul_forge/daemon/deps.py \
        dev-tools/commit-bursts/commit-burst194-adr0054-t6-lifespan-wiring.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(memory): ADR-0054 T6 — lifespan wiring (flip switch) (B194)

Burst 194. Closes ADR-0054 T6 — wires the procedural-shortcut
substrate into the dispatcher at app.lifespan. Substrate has
been sitting 80% built since B182; this burst makes it
operable. Default OFF. Operators flip via
FSF_PROCEDURAL_SHORTCUT_ENABLED=1 + daemon restart.

With it on, every llm_think dispatch checks for a stored
shortcut BEFORE firing the LLM (cosine >= 0.92 AND
reinforcement >= 2 AND posture green). Match → substitute
recorded response, emit tool_call_shortcut audit event,
return in <100ms.

Ships:

config.py: 4 new DaemonSettings fields —
procedural_shortcut_enabled (master switch, default False),
procedural_cosine_floor (0.92), procedural_reinforcement_floor
(2), procedural_embed_model (nomic-embed-text:latest). All
mapped to FSF_PROCEDURAL_* env vars.

deps.py: in build_or_get_tool_dispatcher, after dispatcher
construction, builds ProceduralShortcutsTable from the
registry's connection and injects 4 closure-style getters
onto the dispatcher (so env-var flips + restart picks up
new values without code change). Defensive try/except —
any failure does not crash startup; dispatcher's
_resolve_shortcut_match handles None table by short-
circuiting to no-match.

Per ADR-0001 D2: shortcuts are per-instance state, not
identity. constitution_hash + DNA constant across table
growth.

Per ADR-0044 D3: zero ABI changes; defaults safe; pre-T6
.env files unaffected.

Verification: defaults correct, build_app clean both modes,
180 tests pass.

Operator follow-up (not in this commit):
- Flip FSF_PROCEDURAL_SHORTCUT_ENABLED=true in .env
- Restart daemon
- B195 (T5b) will ship the chat-tab thumbs UI for
  reinforcement.

ADR-0054 substrate complete with this burst (T1-T6 all
shipped). T5b chat thumbs UI follows in B195."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 194 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
