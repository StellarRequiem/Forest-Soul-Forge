#!/bin/bash
# Burst 157 — ADR-0047 T5 — memory_recall.v1 integration in the
# Persistent Assistant prompt.
#
# Closes the fifth implementation tranche of ADR-0047. T1 (B147)
# shipped the mode toggle scaffold; T2 (B154) the birth flow + bound-
# instance state machine; T3 (B155) the auto-conversation + chat
# surface; T6 (B156) the dedicated `assistant` role. T5 is the last
# substrate piece for an actually-useful assistant: persistent
# context across sessions.
#
# Scope: ONLY assistant-domain conversations get auto-injected
# memories. Multi-agent rooms keep the existing prompt shape — the
# memories param defaults to None and the helper byte-shape is
# preserved when omitted (asserted in test_no_memories_no_context_block).
#
# Why scoped to domain=assistant:
#
#   - The Persistent Assistant is purpose-built as an "across-session
#     memory" surface. Auto-injection matches the UX expectation.
#   - Multi-agent rooms (Security Swarm, SW-track triune, etc.) have
#     existing per-role kits where memory_recall is already an
#     explicit tool the agent can call. Auto-injection there would
#     change the prompt for every existing agent — that's a behavior
#     change with no T5 mandate.
#   - ADR-0027 §1: cross-agent reads cross an information-flow
#     boundary. Keeping mode='private' for the auto-call means the
#     assistant only ever sees its OWN accumulated memory. Cross-agent
#     visibility stays gated behind explicit memory_recall calls
#     with mode=lineage/consented.
#
# What ships:
#
#   src/forest_soul_forge/daemon/routers/conversation_helpers.py:
#     build_conversation_prompt now accepts an optional
#     memories: list[dict] | None = None param. When non-empty, a
#     "Persistent context (from your memory — facts you've accumulated
#     across earlier sessions):" block emits between the identity
#     frame and the conversation history. Each row renders as a
#     bullet, picking the first non-empty of {content, body, summary}
#     so it tolerates real memory_recall output shape variations.
#     Empty / None memories preserve the byte-shape of the multi-
#     agent rooms prompt exactly.
#
#   src/forest_soul_forge/daemon/routers/conversations.py:
#     Before each agent-dispatch in the Y3 chain loop, when
#     domain == "assistant" and the operator's body is non-empty,
#     dispatch memory_recall.v1 (mode='private', limit=5, query=
#     op_body[:500]) against the agent BEFORE building the prompt.
#     Pass results as memories= to build_conversation_prompt. All
#     failure paths fall back to memories=[]; the chat works either
#     way.
#
#   tests/unit/test_conversation_helpers.py:
#     Adds 4 tests under TestBuildConversationPrompt covering:
#     - default (memories=None) preserves byte shape (no
#       "Persistent context" block)
#     - non-empty memories emit the block + each row's content
#     - alternate dict keys (content/body/summary) all render
#     - empty memories list ([]) suppresses the block
#
# Audit-chain impact: each memory_recall call emits its own
# tool_call audit event (per ADR-0019), so the chain captures
# WHICH memories the assistant saw before generating each reply.
# That's the audit trail an operator wants when reviewing the
# assistant's behavior — every "context window" it had is on chain.
#
# Verification:
#   - PYTHONPATH=src python3 -m pytest
#       tests/unit/test_conversation_helpers.py
#       tests/unit/test_conversation_resolver.py
#       tests/unit/test_trait_engine.py
#       tests/unit/test_genre_engine.py
#       tests/unit/test_constitution.py
#       tests/unit/test_tool_catalog.py
#     -> 251 passed
#   - conversations.router imports OK; routes: 12 (unchanged)
#   - Browser refresh: Assistant tab → bound state → send a message
#     → /turns dispatch fires memory_recall first → llm_think prompt
#     contains "Persistent context" block (visible via audit/tail
#     of the dispatch's prompt arg) → reply incorporates memory if
#     any private rows match
#   - Multi-agent room (domain != assistant): no memory_recall
#     pre-call, prompt unchanged (operator-level shape regression
#     test catches this in test_no_memories_no_context_block)
#
# Closes ADR-0047 T5. Remaining: T4 (settings panel + ADR-0048
# allowance toggles). T4 is the last tranche of ADR-0047
# implementation; ADR-0048 implementation is its own arc.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/routers/conversation_helpers.py \
        src/forest_soul_forge/daemon/routers/conversations.py \
        tests/unit/test_conversation_helpers.py \
        dev-tools/commit-bursts/commit-burst157-adr0047-t5-memory-recall.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(chat): ADR-0047 T5 — memory_recall in assistant prompt (B157)

Burst 157. Closes ADR-0047 T5. Scoped to domain=assistant only —
multi-agent rooms keep the existing prompt shape (asserted in
test_no_memories_no_context_block).

Why scoped:
- Persistent Assistant is purpose-built as an across-session memory
  surface; auto-injection matches the UX expectation.
- Multi-agent rooms already have memory_recall in per-role kits
  where the agent calls it explicitly; auto-injection there would
  change every existing agent's prompt without a T5 mandate.
- ADR-0027 §1: cross-agent reads cross an info-flow boundary.
  Keeping mode='private' means the assistant only ever sees its OWN
  accumulated memory; cross-agent visibility stays behind explicit
  memory_recall calls with mode=lineage/consented.

Ships:
- conversation_helpers.build_conversation_prompt: optional
  memories param. When non-empty, emits a 'Persistent context
  (from your memory)' block between identity frame and conversation
  history. Tolerates content/body/summary key variations from
  real memory_recall output. Default None preserves byte-shape
  for multi-agent rooms.
- conversations.append_turn: before each agent dispatch in the
  Y3 chain loop, when domain=='assistant' and operator body
  non-empty, dispatch memory_recall.v1 (mode='private', limit=5,
  query=op_body[:500]) and pass results to build_conversation_prompt.
  All failure paths fall back to no-memories; chat works either way.
- test_conversation_helpers: 4 new tests covering default omission,
  non-empty rendering, alternate dict keys, empty list suppression.

Audit-chain impact: each memory_recall emits its own tool_call
audit event per ADR-0019, so the chain captures WHICH memories
the assistant saw before generating each reply.

Verification: 251 tests passed across conversation_helpers,
conversation_resolver, trait_engine, genre_engine, constitution,
tool_catalog. Router imports clean.

Closes ADR-0047 T5. Remaining: T4 (settings panel + ADR-0048
allowance toggles). T4 is the final ADR-0047 implementation
tranche; ADR-0048 implementation is its own arc."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 157 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
