#!/bin/bash
# Burst 195 — ADR-0054 T5b — chat-tab thumbs UI for shortcut
# reinforcement. Closes the operator-facing reinforcement loop
# that pairs with B194's lifespan wiring. Together: substrate
# is now turn-key for opt-in operators.
#
# What this completes (full ADR-0054 arc):
#   T1 schema + table accessor          (B178)
#   T2 embedding adapter                 (B179)
#   T3 ProceduralShortcutStep + dispatcher branch (B180)
#   T4 dedicated tool_call_shortcut event type   (B181)
#   T5a memory_tag_outcome.v1 tool       (B182)
#   T6 lifespan wiring + settings        (B194)
#   T5b chat-tab thumbs UI               (B195) ← THIS
#
# Operator UX after B195:
#   - Sage answers a recurring question via shortcut
#     substitution (sub-100ms response, no LLM round-trip)
#   - Assistant pane renders a green-bordered widget
#     under the conversation: 'Last response was a recorded
#     pattern sc-xxx (cosine 0.96). Reinforce so future
#     matches grow stronger / weaker.'
#   - Three buttons: good (success +1) / neutral / bad
#     (failure +1)
#   - Click → memory_tag_outcome.v1 dispatch → counters
#     update → next match score reflects the operator's
#     reinforcement
#   - Net-negative shortcuts (failure > success) soft-delete
#     from the search path automatically per ADR-0054 D2.
#
# What ships:
#
#   src/forest_soul_forge/daemon/routers/conversations.py:
#     - NEW endpoint GET /conversations/{id}/last-shortcut.
#       Walks audit.tail(200) for tool_call_shortcut events
#       matching session_id=conv-{conversation_id}; returns
#       the most recent {shortcut_id, similarity, action_kind,
#       audit_seq, timestamp, instance_id}. 404 when none —
#       normal pre-T6 daemons or before any shortcut has fired.
#       O(N) on chain depth via tail-window scan rather than
#       full-chain walk.
#
#   frontend/index.html:
#     - NEW <div id='chat-assistant-shortcut'> hidden-by-default
#       container under the assistant's turn list. chat.js
#       surfaces it on shortcut hits.
#
#   frontend/js/chat.js:
#     - loadAssistantTurns() now also calls
#       refreshAssistantShortcutWidget() after rendering turns.
#     - NEW refreshAssistantShortcutWidget(): finds Sage's most
#       recent agent turn, checks if model_used === 'shortcut',
#       fetches /last-shortcut for the conversation, renders
#       the widget HTML with three buttons + a status line.
#       Hides itself when no shortcut on the most recent turn.
#     - NEW _onShortcutTag(btn): dispatches memory_tag_outcome.v1
#       against the agent that owned the shortcut. Reads
#       outcome (good|neutral|bad) + shortcut_id from button
#       dataset. Disables buttons in flight; surfaces success
#       (with new counters + soft_deleted flag if relevant) or
#       error inline.
#
#   frontend/css/style.css:
#     - NEW .chat-shortcut-widget styles: green-tinted border +
#       background, button row, status line. Disabled-button
#       state during in-flight dispatch.
#
# Per ADR-0044 D3: zero ABI changes. New endpoint is read-only;
# no migration; pre-B195 daemons unaffected; pre-B195 frontends
# reading post-B195 turn data ignore the new chat-assistant-
# shortcut div (it's hidden-by-default in HTML).
#
# Per ADR-0001 D2: thumbs dispatch goes through the existing
# tools/call endpoint — full governance pipeline applies. The
# memory_tag_outcome.v1 tool's allowed_paths constraint is
# already in place (ADR-0054 T5a / B182). No identity surface
# touched.
#
# Verification:
#   - 175 passed across procedural_shortcut_dispatch +
#     tool_dispatcher + governance_pipeline +
#     memory_tag_outcome
#   - build_app() imports clean
#
# Operator-facing follow-up:
#   - Refresh dashboard tab after this commit's daemon restart
#     (the conversations.py change requires it).
#   - Open Chat → Assistant pane.
#   - Until shortcuts actually fire (need master switch on +
#     stored rows in the table), the widget stays hidden.
#     The widget activates the first time Sage answers via a
#     shortcut substitution.
#
# Substrate ready for the next direction (operator picks):
#   - Smith implementation cycle (validate paths, fire
#     code_edit chain)
#   - ADR-0055 marketplace M3 (install endpoint)
#   - forest-marketplace remote setup (push to GitHub)
#   - Wrap for the day

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/routers/conversations.py \
        frontend/index.html \
        frontend/js/chat.js \
        frontend/css/style.css \
        dev-tools/commit-bursts/commit-burst195-adr0054-t5b-thumbs-ui.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(memory): ADR-0054 T5b — chat-tab thumbs UI (B195) — closes arc

Burst 195. Closes the ADR-0054 procedural-shortcut substrate.
Pairs with B194's lifespan wiring to give operators a
turn-key reinforcement loop:

  shortcut hits → operator clicks thumbs → counters update
  → soft-delete on net-negative → search path adapts

Ships:

routers/conversations.py: NEW
GET /conversations/{id}/last-shortcut endpoint. Walks
audit.tail(200) for tool_call_shortcut events matching
the conversation's session_id; returns the most recent.
404 when none.

frontend/index.html: NEW chat-assistant-shortcut div under
the assistant's turn list, hidden by default.

frontend/js/chat.js: loadAssistantTurns now also calls
refreshAssistantShortcutWidget. The widget surfaces when
Sage's most recent turn has model_used === 'shortcut',
fetches /last-shortcut for the metadata, renders three
buttons (good / neutral / bad) wired to
memory_tag_outcome.v1 via the standard tool dispatch path.

frontend/css/style.css: green-tinted widget styling.

Per ADR-0044 D3: read-only endpoint + additive HTML/JS/CSS.
Pre-B195 daemons + frontends both unaffected.

Per ADR-0001 D2: thumbs dispatch goes through standard
tools/call governance — no identity surface touched.

ADR-0054 substrate complete:
- T1 (B178) schema + table
- T2 (B179) embedding adapter
- T3 (B180) ProceduralShortcutStep + dispatcher branch
- T4 (B181) tool_call_shortcut event type
- T5a (B182) memory_tag_outcome.v1
- T6 (B194) lifespan wiring + master switch
- T5b (B195) thumbs UI ← this

Verification: 175 passed across touched modules; build_app
imports clean.

To activate substrate: append
FSF_PROCEDURAL_SHORTCUT_ENABLED=true to .env and restart."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "--- restarting daemon to load conversations router changes ---"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 6
fi

echo ""
echo "=== Burst 195 commit + push + daemon-restart complete ==="
echo "=== ADR-0054 substrate CLOSED. Refresh Chrome tab to see the widget container. ==="
echo "Press any key to close this window."
read -n 1
