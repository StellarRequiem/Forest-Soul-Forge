#!/bin/bash
# Burst 181 — ADR-0054 T4 — graduate the audit emission to a
# dedicated tool_call_shortcut event type.
#
# T3 (B180) emitted the dispatched + succeeded pair with
# shortcut_applied=True metadata as a stop-gap so an operator
# could already grep the chain for shortcut hits. T4 splits
# that into a single dedicated event type registered in
# AuditChain.KNOWN_EVENT_TYPES so:
#
#   - The event itself is honest. A shortcut isn't a tool
#     execution — the underlying tool never ran. Calling it
#     "tool_call_succeeded" was misleading.
#   - Operators querying "what did this agent do?" need to OR
#     tool_call_succeeded + tool_call_shortcut for the
#     complete picture; that asymmetry IS the legibility we
#     want, not a regression.
#   - Verifier and downstream filters can match on event_type
#     directly rather than parsing nested metadata.
#
# What ships:
#
#   src/forest_soul_forge/core/audit_chain.py:
#     - "tool_call_shortcut" added to KNOWN_EVENT_TYPES with a
#       comment block explaining the substitution semantics +
#       the OR-with-succeeded query pattern + the full
#       event_data field list (tool_key, instance_id,
#       session_id, shortcut_id, shortcut_similarity,
#       shortcut_action_kind, args_digest, result_digest,
#       tokens_used, call_count, side_effects, applied_rules,
#       side_effect_summary).
#
#   src/forest_soul_forge/tools/dispatcher.py:
#     - EVENT_SHORTCUT = "tool_call_shortcut" constant added
#       beside the other EVENT_* tool-call lifecycle constants.
#     - _shortcut_substitute refactored: emits ONE
#       tool_call_shortcut event instead of the
#       dispatched + succeeded pair. Same field coverage but
#       now under a dedicated event type. Counter increment +
#       record_match + record_call mirror all preserved.
#     - record_call mirror writes status="shortcut" so per-
#       session roll-ups can filter shortcut hits explicitly
#       without parsing audit data.
#     - DispatchSucceeded.audit_seq points at the shortcut
#       event's seq.
#     - ShortcutUnsupportedKind path UNCHANGED — still emits a
#       regular tool_call_failed (the substitution itself
#       failed; that's not a shortcut, it's a failure).
#
#   tests/unit/test_procedural_shortcut_dispatch.py:
#     - Existing TestShortcutSubstitute test that asserted
#       on the dispatched+succeeded PAIR rewritten to assert
#       on the SINGLE tool_call_shortcut event with full
#       payload coverage (args_digest, result_digest,
#       shortcut metadata, tokens_used, call_count, etc.).
#     - NEW TestShortcutEventRegistration class (4 tests):
#       - tool_call_shortcut registered in KNOWN_EVENT_TYPES
#       - EVENT_SHORTCUT constant matches the registered
#         string (drift between the two would surface as
#         verification warnings on every shortcut)
#       - chain hash-linkage stays valid across multiple
#         shortcut events (re-opens the chain from disk and
#         calls verify())
#       - record_call mirror status is "shortcut"
#
# Per ADR-0044 D3: the new event type is additive. Pre-T4
# daemons reading post-T4 audit chains see "tool_call_shortcut"
# as an unknown type and emit a verification warning rather
# than failing the chain — forward-compat by design (see the
# KNOWN_EVENT_TYPES docstring in core/audit_chain.py:40).
# Post-T4 daemons reading pre-T4 chains see the old
# dispatched+succeeded pair with shortcut_applied metadata
# (the B180 stop-gap) and replay them cleanly because both
# event types are registered.
#
# Verification:
#   PYTHONPATH=src:. pytest tests/unit/test_procedural_shortcut_dispatch.py
#                                tests/unit/test_governance_pipeline.py
#                                tests/unit/test_tool_dispatcher.py
#                                tests/unit/test_procedural_shortcuts.py
#                                tests/unit/test_procedural_embedding.py
#                                tests/unit/test_audit_chain.py
#                                tests/unit/test_plugin_grants.py
#                                tests/unit/test_registry.py
#   -> 268 passed, 1 documented pre-existing xfail
#
# Substrate ready for T5 (reinforcement tools) — the chat-tab
# thumbs surface needs a way to find "the most recent shortcut
# hit on this conversation". With T4 shipped, that query is a
# clean filter on event_type=tool_call_shortcut + session_id
# rather than parsing shortcut_applied metadata across two
# event types.
#
# Remaining ADR-0054 tranches:
#   T5 — reinforcement tools (memory_tag_outcome.v1) +
#        chat-tab thumbs surface
#   T6 — settings UI + daemon-lifespan wiring + operator
#        safety guide

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/audit_chain.py \
        src/forest_soul_forge/tools/dispatcher.py \
        tests/unit/test_procedural_shortcut_dispatch.py \
        dev-tools/commit-bursts/commit-burst181-adr0054-t4-shortcut-event.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(audit): ADR-0054 T4 — tool_call_shortcut event type (B181)

Burst 181. Graduates the procedural-shortcut audit emission
from the B180 stop-gap (dispatched + succeeded with
shortcut_applied metadata) to a dedicated event type
registered in AuditChain.KNOWN_EVENT_TYPES.

Why a dedicated type: a shortcut isn't a tool execution. The
underlying tool never ran. Calling it tool_call_succeeded was
misleading. Splitting it makes the substitution explicitly
visible rather than buried in metadata, and operators
querying agent activity can OR tool_call_succeeded +
tool_call_shortcut for the complete picture — that asymmetry
IS the legibility we want.

Ships:

audit_chain.py: tool_call_shortcut registered in
KNOWN_EVENT_TYPES with full field documentation
(tool_key, instance_id, session_id, shortcut_id,
shortcut_similarity, shortcut_action_kind, args_digest,
result_digest, tokens_used, call_count, side_effects,
applied_rules, side_effect_summary).

dispatcher.py: EVENT_SHORTCUT constant. _shortcut_substitute
emits a single tool_call_shortcut event instead of the
dispatched + succeeded pair. Counter increment, record_match,
and record_call mirror all preserved. record_call status
becomes 'shortcut' so per-session roll-ups can filter
shortcut hits explicitly. DispatchSucceeded.audit_seq points
at the shortcut event's seq. ShortcutUnsupportedKind path
unchanged — still emits a regular tool_call_failed because
the substitution itself failed.

Tests: existing TestShortcutSubstitute test rewritten to
assert on the single event with full payload coverage. NEW
TestShortcutEventRegistration class (4 tests) covering
registration, dispatcher-constant drift, chain hash-linkage
across multiple shortcut events (re-opens + verify()), and
the record_call status='shortcut' mirror.

Per ADR-0044 D3: additive event type. Pre-T4 daemons reading
post-T4 chains emit a verification warning rather than
failing — forward-compat per KNOWN_EVENT_TYPES docstring.

Verification: 268 passed across the touched-modules sweep,
1 documented pre-existing xfail.

Remaining ADR-0054 tranches:
- T5 reinforcement tools (memory_tag_outcome.v1)
- T6 settings UI + daemon-lifespan wiring + safety guide"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 181 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
