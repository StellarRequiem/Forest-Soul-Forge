#!/bin/bash
# Burst 154 — ADR-0047 T2 — first-use birth flow for assistant agent.
#
# Second implementation tranche of ADR-0047 (Persistent Assistant
# Chat). T1 (B147) shipped the mode toggle scaffold. T2 ships the
# birth flow + bound-instance state machine inside the assistant
# pane.
#
# Two-state machine:
#
#   [no assistant bound] → birth prompt (name input, genre locked
#                          to companion, birth button)
#                            ↓ on click → POST /birth
#                            ↓ store instance_id in localStorage
#   [assistant bound]    → "ready" panel showing instance_id +
#                          reset-binding button
#                            ↑ on reset → clear localStorage
#                              (agent itself preserved in registry)
#
# Pragmatic interim: birth uses role=operator_companion (existing
# in trait_tree.yaml + constitution_templates.yaml). A follow-up
# burst will add a dedicated `assistant` role per ADR-0047
# Decision 2. This lets T2 ship without role-config edits.
#
# What ships:
#
#   frontend/index.html — replaces the T1 scaffold placeholder
#     in #chat-pane-assistant with two sections:
#     (1) #chat-assistant-birth — name input + birth button +
#         genre/role explanation
#     (2) #chat-assistant-ready — bound-instance display + reset
#         button + T3-coming notice
#     Status indicator in the panel header tracks the state.
#
#   frontend/js/chat.js — three additions:
#     1. ASSISTANT_INSTANCE_KEY localStorage constant
#     2. wireAssistantBirthFlow() — wires birth button (POST /birth
#        with operator_companion role, stores instance_id) and
#        reset button (clears localStorage)
#     3. refreshAssistantPane() — toggles birth/ready visibility
#        based on whether instance is bound. Called from showChatMode
#        when entering assistant mode.
#
#   frontend/css/style.css — minimal addition to widen .inp inside
#     the assistant pane to a max of 360px (better fit for the
#     name field).
#
# Per ADR-0047 Decision 1 (userspace-only): no daemon code touched,
# no kernel ABI surface changed, no new endpoints. Birth uses the
# existing /birth endpoint exactly as multi-agent rooms (and the
# Forge tab) do.
#
# Verification:
#   - Browser refresh on the Chat tab + click Assistant mode →
#     "no assistant bound" prompt appears
#   - Type a name + click "Birth my assistant" → birth fires;
#     toast confirms; pane transitions to "ready" state showing
#     the new instance_id
#   - Click "reset assistant binding" + confirm → returns to
#     birth prompt (agent preserved in registry; check Agents tab)
#   - localStorage persists across reloads — operator who bound an
#     assistant lands on the "ready" pane on next visit
#
# Closes ADR-0047 T2. Next tranches:
#   T3: auto-create persistent conversation + chat surface
#   T4: settings panel (posture, allowances → ADR-0048)
#   T5: memory integration
#   T6: dedicated `assistant` role in trait_tree.yaml + role_base
#       in constitution_templates.yaml + archetype kit in
#       tool_catalog.yaml + companion-genre claim

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add frontend/index.html \
        frontend/js/chat.js \
        frontend/css/style.css \
        dev-tools/commit-bursts/commit-burst154-adr0047-t2-birth-flow.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(chat): ADR-0047 T2 — assistant birth flow + bound state (B154)

Burst 154. Second implementation tranche of ADR-0047 (Persistent
Assistant Chat). Two-state machine inside the assistant pane:
[no bound] → birth prompt; [bound] → ready panel.

Pragmatic interim: uses role=operator_companion (existing). A
follow-up burst defines a dedicated assistant role per ADR-0047
Decision 2.

Ships (frontend-only, per ADR-0047 Decision 1):
- index.html: replaces T1 scaffold placeholder in
  #chat-pane-assistant with #chat-assistant-birth (name input,
  genre locked to companion, birth button) and
  #chat-assistant-ready (instance_id display, reset-binding
  button, T3-coming notice). Status indicator in header.
- chat.js: ASSISTANT_INSTANCE_KEY constant + wireAssistantBirthFlow()
  (birth via POST /birth, reset clears localStorage) +
  refreshAssistantPane() called from showChatMode.
- style.css: name input width fit.

Verification: browser refresh → click Assistant tab → no-bound
prompt → enter name → click Birth → POST /birth → toast →
ready pane shows instance_id. localStorage persists. Reset
clears binding (agent preserved in registry).

Closes ADR-0047 T2. Next: T3 (auto-create conversation + chat
surface), T4 (settings + allowances → ADR-0048), T5 (memory),
T6 (dedicated assistant role config)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 154 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
