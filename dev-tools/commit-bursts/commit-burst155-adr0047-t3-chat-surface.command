#!/bin/bash
# Burst 155 — ADR-0047 T3 — auto-create persistent conversation +
# chat surface inside the bound-assistant pane.
#
# Third implementation tranche of ADR-0047 (Persistent Assistant
# Chat). T1 (B147) shipped the mode toggle scaffold. T2 (B154)
# shipped the birth flow + bound-instance state machine. T3 closes
# the loop: when an assistant is bound, the pane now shows a
# functional chat surface (history + composer) with auto-resolved
# conversation against the existing ADR-003Y conversation runtime.
#
# Three-step conversation resolution on every entry to the bound
# state (cheap path; no operator action required):
#
#   1. Cached conv id (localStorage ASSISTANT_CONV_KEY) — verify with
#      GET; fall through if 404.
#   2. List /conversations?domain=assistant&operator_id=<op>; pick
#      the first non-archived row whose participants include the
#      bound instance_id. Cache.
#   3. Otherwise create fresh: POST /conversations (domain=assistant,
#      retention_policy=full_indefinite per ADR-0047 Decision 3),
#      then POST .../participants with the bound instance_id. Cache.
#
# The 1:1 layout uses max_chain_depth=1 and history_limit=30. No
# @mention chains, no bridging, no ambient quota — those affordances
# stay in the multi-agent Rooms mode where they earn their cost.
#
# What ships:
#
#   frontend/index.html — replaces the T2 placeholder content in
#     #chat-assistant-ready with a functional surface:
#     - .chat-assistant-strip (instance_id + conv_id + reset button)
#     - #chat-assistant-turns (scrollable history)
#     - #chat-assistant-composer (textarea + send button + feedback)
#
#   frontend/js/chat.js — adds:
#     - ASSISTANT_CONV_KEY + ASSISTANT_OPERATOR_ID constants
#     - in-memory state: assistantConvId / assistantTurns /
#       assistantAgentName / assistantLoading
#     - loadAssistantConversation(instanceId) — three-step resolver
#     - loadAssistantTurns() — fetches /turns, calls render
#     - renderAssistantTurns() — paints rows reusing .chat-turn--*
#       styles for visual consistency with the multi-agent surface
#     - sendAssistantTurn() — POST /turns with auto_respond=true
#       and max_chain_depth=1 (1:1 conversation)
#     - composer wiring (Cmd/Ctrl+Enter + click)
#     - reset button now also clears ASSISTANT_CONV_KEY
#     - refreshAssistantPane() triggers loadAssistantConversation
#       when transitioning into bound state
#
#   frontend/css/style.css — surface layout:
#     .chat-assistant-surface (flex column),
#     .chat-assistant-strip + __id + __actions,
#     .chat-assistant-turns (scrollable, capped 480px),
#     .chat-assistant-composer + __row,
#     .chat-assistant-input (overrides generic .inp 360px cap with
#     !important so the composer fills the surface width).
#
# Per ADR-0047 Decision 1 (userspace-only): no daemon code touched,
# no kernel ABI surface changed, no new endpoints. T3 composes the
# existing /conversations + /participants + /turns surfaces.
#
# Verification:
#   - Browser refresh on Chat tab → click Assistant mode → bound
#     pane appears (assistant from B154) → chat surface loads with
#     "No turns yet. Send the first message…" placeholder
#   - Type a message → click send → operator turn appended → agent
#     thinks (provider round-trip) → reply turn appended → both
#     render in the surface; auto-scrolls to bottom
#   - Reload the page → returns to assistant mode → same conversation
#     with full history (cached conv id resolves; turns reload)
#   - Reset binding → clears assistant + conv localStorage; pane
#     returns to birth prompt; agent + conversation row preserved
#     in the registry (audit-chain principle: removals only via
#     explicit operator action from the appropriate tab)
#
# Closes ADR-0047 T3. Next tranches:
#   T4: settings panel (posture, allowances → ADR-0048)
#   T5: memory_recall.v1 integration into prompt-building
#   T6: dedicated `assistant` role + role_base + archetype kit +
#       companion-genre claim (replaces the operator_companion
#       interim role used in T2 birth)

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add frontend/index.html \
        frontend/js/chat.js \
        frontend/css/style.css \
        dev-tools/commit-bursts/commit-burst155-adr0047-t3-chat-surface.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(chat): ADR-0047 T3 — assistant chat surface + auto-conv (B155)

Burst 155. Third implementation tranche of ADR-0047 (Persistent
Assistant Chat). When an assistant is bound, the pane now shows a
functional chat surface with auto-resolved conversation against
the ADR-003Y conversation runtime.

Three-step conversation resolution on entry to the bound state:
1. Cached conv id in localStorage (verify with GET; fall through
   on 404).
2. List /conversations?domain=assistant&operator_id=<op>; pick
   first non-archived row whose participants include the bound
   instance_id.
3. Create fresh: POST /conversations (domain=assistant,
   retention_policy=full_indefinite per Decision 3), then add
   the assistant as participant.

1:1 layout uses max_chain_depth=1 + history_limit=30. The Y3
multi-agent affordances (chains, bridging, ambient) stay in the
Rooms mode where they earn their cost.

Ships (frontend-only, per ADR-0047 Decision 1):
- index.html: surface markup (strip + turns + composer) replacing
  T2 placeholder.
- chat.js: ASSISTANT_CONV_KEY + ASSISTANT_OPERATOR_ID constants,
  loadAssistantConversation/loadAssistantTurns/renderAssistantTurns
  /sendAssistantTurn helpers, composer wiring, reset clears both
  binding + conv pointers, refreshAssistantPane triggers load.
- style.css: .chat-assistant-surface flex layout, scrollable
  history capped at 480px, composer width override.

Verification: Assistant mode → surface loads → send → agent reply
appended → reload preserves conversation. Reset clears localStorage;
agent + conversation row remain in registry.

Closes ADR-0047 T3. Next: T4 (settings + allowances → ADR-0048),
T5 (memory_recall integration), T6 (dedicated assistant role
config to replace operator_companion interim)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 155 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
