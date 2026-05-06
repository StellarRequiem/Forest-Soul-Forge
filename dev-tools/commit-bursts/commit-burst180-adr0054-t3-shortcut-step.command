#!/bin/bash
# Burst 180 — ADR-0054 T3 — ProceduralShortcutStep +
# StepResult.shortcut verdict + dispatcher SHORTCUT branch.
#
# T3 wires the procedural-shortcut substrate (T1 table + T2
# embedding adapter) into the live tool dispatcher. With this
# burst, an operator who flips FSF_PROCEDURAL_SHORTCUT_ENABLED=1
# AND has at least one stored shortcut can see the dispatcher
# bypass the LLM round-trip on a high-confidence match — sub-
# 100ms response instead of 2-15s for an llm_think dispatch.
#
# Per ADR-0054 D1 + D2: the substrate is OFF by default. The
# default state of every existing daemon is identical to pre-T3
# because:
#   - procedural_shortcuts_table=None         → resolver no-op
#   - procedural_shortcut_enabled_fn=None     → master switch off
#   - the daemon's app.lifespan doesn't wire any of these yet
#     (that's T6 — the settings UI commit will do that)
#
# What ships:
#
#   src/forest_soul_forge/tools/governance_pipeline.py:
#     - DispatchContext gains shortcut_match field (pre-computed
#       by the dispatcher because the pipeline is sync but
#       embed_situation + search_by_cosine are async).
#     - StepResult gains SHORTCUT verdict + shortcut_candidate +
#       shortcut_similarity fields. Constructor classmethod
#       .shortcut(candidate, similarity). is_shortcut property.
#       terminal recognizes SHORTCUT as terminal.
#     - ProceduralShortcutStep — sync verdict converter. Reads
#       dctx.shortcut_match and emits SHORTCUT or GO.
#       Defensive: malformed shortcut_match falls through to GO
#       rather than crashing.
#
#   src/forest_soul_forge/tools/dispatcher.py:
#     - 4 new ToolDispatcher fields: procedural_shortcuts_table +
#       procedural_shortcut_enabled_fn + cosine_floor_fn +
#       reinforcement_floor_fn + embed_model_fn. All default to
#       None for backward compat.
#     - ProceduralShortcutStep added to the pipeline LAST (after
#       PostureGateStep) so all upstream gates (hardware, args,
#       constitution, posture, genre, counter, approval) clear
#       before a shortcut can fire.
#     - dispatch() awaits _resolve_shortcut_match BEFORE building
#       dctx. The async helper:
#         1. Sync gates: substrate wired? master switch on? tool
#            is llm_think? task_kind=conversation? prompt non-
#            empty? provider has embed()? posture green/None?
#         2. If all pass: embed_situation(provider, prompt) +
#            search_by_cosine on the table.
#         3. Returns (candidate, cosine) on a match; None for
#            ALL other outcomes (eligibility fail / search empty
#            / EmbeddingError / unexpected exception).
#     - dispatch() handles the new SHORTCUT verdict via
#       _shortcut_substitute:
#         - increments the counter (a shortcut still costs a slot)
#         - emits tool_call_dispatched + tool_call_succeeded
#           with shortcut_applied=True + shortcut_id +
#           shortcut_similarity + shortcut_action_kind metadata
#         - builds a synthetic ToolResult from action_payload —
#           shape matches llm_think.v1 output ({"response": str,
#           "model": "shortcut", "task_kind": str, "elapsed_ms": 0})
#           so callers don't notice the substitution
#         - calls table.record_match() to update last_matched_at
#           + last_matched_seq for reinforcement telemetry (T5)
#       Action-kind scope: T3 substitutes for "response" only.
#       "tool_call" + "no_op" emit a tool_call_failed with
#       exception_type=ShortcutUnsupportedKind so an operator
#       can find + fix the row.
#     - T4 will graduate the audit emission to a dedicated
#       tool_call_shortcut event type; T3 reuses dispatched +
#       succeeded with shortcut metadata so an operator can
#       already grep the chain for shortcut hits.
#
#   tests/unit/test_procedural_shortcut_dispatch.py (NEW):
#     30 unit tests across three classes:
#
#       TestStepResultShortcut (6 tests):
#         - .shortcut() builds the right verdict shape
#         - is_shortcut / terminal / is_refuse / is_pending
#           properties
#
#       TestProceduralShortcutStep (3 tests):
#         - dctx.shortcut_match=None → GO
#         - dctx.shortcut_match=(candidate, score) → SHORTCUT
#         - malformed shortcut_match → GO (defensive)
#
#       TestResolveShortcutMatchEligibility (15 tests):
#         - unwired table / master switch off / wrong tool /
#           wrong task_kind / empty prompt / non-string prompt /
#           provider without embed() / red posture / yellow
#           posture all skip search entirely
#         - green posture / None posture both run search
#         - search empty returns None
#         - EmbeddingError returns None
#         - unexpected exception returns None
#         - table search exception returns None
#         - cosine + reinforcement floors pass through to
#           search_by_cosine via the injected closures
#
#       TestShortcutSubstitute (6 tests):
#         - response kind returns DispatchSucceeded with
#           synthetic result + record_match called
#         - audit emits dispatched + succeeded with full
#           shortcut_applied metadata in BOTH events
#         - tool_call action_kind returns DispatchFailed with
#           exception_type=ShortcutUnsupportedKind
#         - counter increments per shortcut hit (no DoS bypass)
#         - no-match falls through to llm_think.v1 normally
#         - master switch off falls through; search never runs
#
#   tests/unit/test_plugin_grants.py: schema-version assertion
#   bumped 15 → 16 + function name renamed for honesty (the
#   schema migrated through v14 → v15 → v16 over the past 3
#   bursts; B178 missed updating this one assertion).
#
#   tests/unit/test_daemon_readonly.py: same schema-version
#   bump in TestHealth::test_healthz_reports_local_provider.
#   Comment updated to track the v13 → v14 → v15 → v16
#   evolution.
#
# Per ADR-0044 D3: zero kernel ABI surface changes.
# DispatchContext.shortcut_match + StepResult.shortcut_candidate
# are NEW optional fields (default None / unset). The
# DispatchSucceeded / DispatchFailed return shapes are
# unchanged. Existing dispatcher tests pass without
# modification (53/53 in test_tool_dispatcher.py).
#
# Per ADR-0001 D2 identity invariance: shortcuts are per-
# instance state, not identity. constitution_hash + DNA stay
# immutable; only what the agent KNOWS evolves. Operators can
# delete the shortcuts table freely without touching agent
# identity.
#
# Verification:
#   PYTHONPATH=src:. pytest tests/unit/test_procedural_shortcut_dispatch.py
#                                tests/unit/test_governance_pipeline.py
#                                tests/unit/test_tool_dispatcher.py
#                                tests/unit/test_procedural_shortcuts.py
#                                tests/unit/test_procedural_embedding.py
#                                tests/unit/test_plugin_grants.py
#                                tests/unit/test_registry.py
#                                tests/unit/test_registry_concurrency.py
#   -> 236 passed, 1 documented pre-existing xfail
#
# Substrate ready for T4 (audit emission graduation +
# tool_call_shortcut event type), T5 (reinforcement tools +
# chat-tab thumbs surface), T6 (settings UI + the daemon-
# lifespan wiring that flips master switch ON for opting-in
# operators).
#
# Remaining ADR-0054 tranches:
#   T4 — tool_call_shortcut event type (audit graduation)
#   T5 — reinforcement tools (memory_tag_outcome.v1) + chat-tab
#        thumbs surface
#   T6 — settings UI + daemon-lifespan wiring + operator safety
#        guide

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/tools/governance_pipeline.py \
        src/forest_soul_forge/tools/dispatcher.py \
        tests/unit/test_procedural_shortcut_dispatch.py \
        tests/unit/test_plugin_grants.py \
        tests/unit/test_daemon_readonly.py \
        dev-tools/commit-bursts/commit-burst180-adr0054-t3-shortcut-step.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(memory): ADR-0054 T3 — shortcut step + dispatcher branch (B180)

Burst 180. Wires the procedural-shortcut substrate (T1 table + T2
embedding adapter) into the live tool dispatcher. With this burst,
an operator who flips FSF_PROCEDURAL_SHORTCUT_ENABLED=1 AND has at
least one stored shortcut can see the dispatcher bypass the LLM
round-trip on a high-confidence match.

Per ADR-0054 D1 + D2: substrate is OFF by default. Existing
daemons behave identically to pre-T3 because every new field
defaults to None / a no-op closure.

Ships:

governance_pipeline.py:
- DispatchContext gains shortcut_match field (pre-computed by
  the dispatcher because the pipeline is sync but embed +
  search are async).
- StepResult gains SHORTCUT verdict + shortcut_candidate +
  shortcut_similarity fields. .shortcut() factory + is_shortcut
  property + terminal recognition.
- ProceduralShortcutStep — sync verdict converter. Reads
  dctx.shortcut_match and emits SHORTCUT or GO. Malformed
  shortcut_match falls through to GO (defensive).

dispatcher.py:
- 4 new ToolDispatcher fields for the substrate (table +
  enabled / cosine / reinforcement / embed-model closures).
- ProceduralShortcutStep added LAST in the pipeline so all
  upstream gates (hardware, args, constitution, posture,
  genre, counter, approval) clear before a shortcut fires.
- dispatch() awaits _resolve_shortcut_match BEFORE building
  dctx. Eligibility: substrate wired, master switch on, tool
  is llm_think, task_kind=conversation, prompt non-empty,
  provider has embed(), posture green/None. EmbeddingError
  and unexpected exceptions return None — never crash
  dispatch on a shortcut bug.
- dispatch() handles new SHORTCUT verdict via
  _shortcut_substitute: counter increments (no DoS bypass),
  emits dispatched + succeeded with shortcut_applied=True
  metadata, builds synthetic ToolResult shaped like
  llm_think.v1 output, calls table.record_match() for
  reinforcement telemetry (T5). action_kind=response only;
  tool_call/no_op emit DispatchFailed with
  ShortcutUnsupportedKind so the operator can find the row.

Tests: 30 unit tests in test_procedural_shortcut_dispatch.py
across StepResult.shortcut, ProceduralShortcutStep,
_resolve_shortcut_match eligibility chain, and
_shortcut_substitute branch.

Plus schema-version assertion bumps in test_plugin_grants.py
and test_daemon_readonly.py (15 to 16) — B178 missed two
stale assertions.

Per ADR-0044 D3: zero kernel ABI changes. DispatchContext +
StepResult fields are additive; DispatchSucceeded shape
unchanged. Existing dispatcher tests pass without
modification (53/53).

Per ADR-0001 D2: shortcuts are per-instance state, not
identity. constitution_hash + DNA stay immutable.

Verification: 236 passed across the touched-modules sweep,
1 documented pre-existing xfail.

Remaining ADR-0054 tranches:
- T4 tool_call_shortcut event type (audit graduation)
- T5 reinforcement tools (memory_tag_outcome.v1)
- T6 settings UI + daemon-lifespan wiring + safety guide"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 180 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
