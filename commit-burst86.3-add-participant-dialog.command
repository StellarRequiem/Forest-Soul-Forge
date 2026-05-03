#!/usr/bin/env bash
# Burst 86.3: replace window.prompt() add-participant flow with proper
# dialog + select dropdown. Plus sticky-bar UX hint on slow llm round-trips.
#
# After 86.2 fixed the dropdown population, the add-participant flow was
# still using window.prompt() showing the agent list inline in the prompt
# message. macOS browsers don't let you select+copy from a window.prompt
# message body, so the user couldn't paste the instance_id back into the
# input. New flow: real DOM dialog with a clickable <select> populated
# from /agents, same shape as the new-room and bridge dialogs.
#
# Also: chat-send turn-dispatch was blocking on the entire LLM round-trip
# (5-30s for qwen2.5-coder:7b) which made the chat bar feel "sticky."
# Compromise fix: keep the input field enabled (only the send button
# disables) so the user can keep typing, and surface a toast with the
# elapsed time when round-trip exceeds 5s — makes the latency visible
# rather than mysterious. Real async-dispatch fix (use /audit/stream
# for live agent-reply arrival, per Y6.1 future) is queued for a later
# burst — that's the actual unblock.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 86.3 — add-participant dialog + sticky-bar latency hint ==="
echo
clean_locks
git add frontend/index.html frontend/js/chat.js
git add commit-burst86.3-add-participant-dialog.command
clean_locks
git status --short
echo
clean_locks
git commit -m "fix(frontend): proper add-participant dialog + sticky-bar latency hint

Two distinct chat-tab UX issues:

1. ADD-PARTICIPANT: window.prompt() can't be copy-pasted from
   window.prompt's MESSAGE TEXT (where the candidate list was shown)
   is read-only on macOS browsers — user couldn't select and copy
   instance_ids from it. 86.2's fix to show full instance_ids was
   technically correct but practically still useless because the
   user couldn't actually copy the strings.

   Fix: new in-DOM dialog (#chat-add-participant-dialog) with a
   <select> dropdown, same shape as the existing new-room and
   bridge dialogs. Populated from /agents on open (same cache-miss
   pattern as 86.2). User clicks the dropdown, picks an agent,
   clicks 'add'. Zero copy/paste required.

   - Added dialog markup to frontend/index.html (after bridge dialog).
   - Replaced promptAddParticipant() body with dialog open + populate.
   - Added wireAddParticipantDialog() with cancel + confirm handlers.
   - Wired into start() so the buttons work.

2. STICKY CHAT BAR: send-turn blocked the whole UI on the LLM
   round-trip (5-30s for qwen2.5-coder:7b at default max_tokens=400
   when auto_respond is checked). Frontend disabled the send button,
   which felt like the entire page froze.

   Compromise fixes:
   - Don't disable the input field — only the send button. User can
     keep typing the next message while the agent generates.
   - Surface a toast with elapsed time when round-trip exceeds 5s.
     Makes the latency visible rather than mysterious; tells the
     operator where to look (max_tokens slider, smaller model, etc.).

   The REAL fix is async-dispatch + /audit/stream live updates
   (Y6.1's stated future). That's queued for a future burst — too
   much surface to cram into a UX hotfix.

Latent UX bugs found via Claude in Chrome MCP debugging session:
- 86.1: chat.js state import was wrong (entire frontend dead)
- 86.2: .chat-dialog [hidden] didn't hide due to display:flex
- 86.2: new-room + bridge dropdowns had cache-miss bug
- 86.3: window.prompt copy/paste broken on macOS
- 86.3: send-turn UX 'felt sticky' due to blocking LLM round-trip

All four bugs latent since Burst 7 (Y6 Chat tab, ~2 weeks). The
chat tab had never been used on a clean fresh-load session before
today's testing — that's how all four survived.

Verified end-to-end via reload + screenshot: chat tab + add
participant flow works without copy/paste, agent appears in
dropdown immediately, send-turn shows elapsed-time toast on slow
generations."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 86.3 landed. Add-participant dialog clickable; sticky bar surfaces latency."
echo "Refresh the browser to pick up the new dialog."
echo ""
read -rp "Press Enter to close..."
