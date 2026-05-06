#!/bin/bash
# Burst 147 — ADR-0047 T1 (frontend mode toggle).
#
# First implementation tranche of ADR-0047 (Persistent Assistant Chat
# Mode). Adds a pill-style mode toggle at the top of the Chat tab so
# the operator can switch between:
#
#   - "Rooms" (existing ADR-003Y multi-agent UI, default)
#   - "Assistant" (ADR-0047 single-agent, placeholder until T2-T6)
#
# Strictly additive — no existing UI moved or removed. The Rooms pane
# keeps everything chat.js currently does. The Assistant pane is a
# placeholder that documents what's coming in T2-T6 and links to both
# ADRs.
#
# Mode preference persists in localStorage (key fsf.chat.mode).
#
# What ships:
#
#   frontend/index.html — adds the .chat-mode-toggle bar + the
#     #chat-pane-assistant placeholder + wraps the existing
#     chat-grid in #chat-pane-rooms. Total +30 lines, no removals.
#     HTML nesting balance verified (51 opens / 51 closes in the
#     chat tab section).
#
#   frontend/js/chat.js — three additions:
#     1. CHAT_MODE_KEY constant (localStorage)
#     2. wireChatModeToggle() function — restores mode from localStorage,
#        wires both buttons
#     3. showChatMode(mode) helper — toggles pane visibility +
#        applies --active class to the selected button
#     start() now calls wireChatModeToggle() before everything else.
#
#   frontend/css/style.css — pill-style segmented control:
#     .chat-mode-toggle (container), .chat-mode-btn (button),
#     .chat-mode-btn--active (selected state), .chat-pane-assistant
#     (placeholder pane width).
#
# Per ADR-0047 Decision 1 (userspace-only): NO daemon code touched,
# NO kernel ABI surface changed, NO new endpoints. The operator gets
# the mode toggle on browser refresh; no daemon restart.
#
# Verification:
# - HTML nesting balance verified via Python script
# - Mode toggle visually distinct (pill segment, accent color when
#   selected)
# - Default mode "rooms" matches existing UX (no behavior change for
#   existing operators)
# - Stored mode survives page refresh
# - Assistant pane explicitly says "scaffold" + links to ADR-0047 +
#   ADR-0048 so the operator knows what's coming
#
# Closes ADR-0047 T1. Next tranche (T2): birth flow with trait sliders.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add frontend/index.html \
        frontend/js/chat.js \
        frontend/css/style.css \
        dev-tools/commit-bursts/commit-burst147-adr0047-t1-mode-toggle.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(chat): ADR-0047 T1 — mode toggle Assistant / Rooms scaffold (B147)

Burst 147. First implementation tranche of ADR-0047. Adds a
pill-style mode toggle at the top of the Chat tab so the operator
can switch between Rooms (existing ADR-003Y multi-agent UI,
default) and Assistant (ADR-0047 single-agent, placeholder until
T2-T6).

Strictly additive — no existing UI moved or removed. Per ADR-0047
Decision 1 (userspace-only): no daemon code touched, no kernel ABI
surface changed.

Ships:
- index.html: .chat-mode-toggle bar + #chat-pane-assistant
  placeholder + wraps existing chat-grid in #chat-pane-rooms.
  +30 lines. HTML nesting balance verified.
- chat.js: CHAT_MODE_KEY constant + wireChatModeToggle() +
  showChatMode(mode) helper. start() calls wire... first.
- style.css: pill segmented-control styling.

Mode preference persists in localStorage (fsf.chat.mode).

Effect: operator gets mode toggle on browser refresh — no daemon
restart needed. Default 'rooms' = existing UX, no behavior change.
Switch to 'Assistant' = placeholder explaining T2-T6 are coming
with links to ADR-0047 + ADR-0048.

Closes ADR-0047 T1. Next tranche (T2): first-use birth flow with
trait sliders."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 147 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
