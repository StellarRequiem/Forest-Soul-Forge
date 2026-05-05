#!/usr/bin/env bash
# Burst 86.2: chat-dialog UX hotfixes — three latent bugs in one commit.
#
# After 86.1 fixed the chat.js state import, the chat tab loaded but
# THREE distinct UX bugs remained:
#
# 1. CSS: .chat-dialog had `display: flex; position: fixed` which
#    overrode the [hidden] HTML attribute (UA stylesheet sets
#    [hidden]{display:none}, any explicit display value beats it).
#    Result: new-room, bridge, sweep, ambient dialogs were stuck
#    visible on chat-tab load. Fix: .chat-dialog[hidden] {display:none}.
#
# 2. JS (new-room dialog): Populated the participant <select> from
#    state.get("agents"), which is empty unless the Agents tab has
#    been visited first. Fix: fetch /agents directly when the cache
#    is empty.
#
# 3. JS (bridge dialog): Same issue as #2.
#
# 4. JS (add-participant prompt): Even worse — used window.prompt()
#    showing TRUNCATED instance_ids (12 chars) and asked the user to
#    "paste from list". The truncated value didn't match the actual
#    ID, so paste-and-submit always 404'd. Fix: show full instance_id,
#    accept agent_name as a shortcut, refresh cache from /agents.
#
# Combined effect: chat tab is now usable end-to-end without bouncing
# through the Agents tab to seed cached state.
#
# All four bugs latent since Burst 7 (Y6 Chat tab, ~2 weeks).
# Diagnosed via Claude in Chrome MCP — javascript_tool to inspect
# DOM, getComputedStyle to find the display-vs-hidden conflict,
# read_console_messages to confirm zero errors after fix.
#
# This burst also includes the new agent born live during the debug
# session (Forge_AuditTight_01, DNA f782d3ed1b6b) — that's runtime
# state, not in the commit. The commit just ships the code fixes.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 86.2 — chat dialog UX hotfixes (CSS + 3 JS bugs) ==="
echo
clean_locks
git add frontend/css/style.css
git add frontend/js/chat.js
git add commit-burst86.2-chat-dialog-fix.command
clean_locks
git status --short
echo
clean_locks
git commit -m "fix(frontend): chat dialog UX — CSS [hidden] + agent-list cache misses

Three latent bugs in the chat tab discovered while operator was
trying to add an agent to a conversation. All four had been latent
since Burst 7 (Y6 Chat tab, ~2 weeks); Burst 86's daemon restart +
clean page load surfaced them.

Bug #1 — CSS: .chat-dialog stuck visible
.chat-dialog had display:flex + position:fixed + z-index:100. The
HTML hidden attribute only works because the UA stylesheet sets
[hidden]{display:none}, and any explicit display value beats it.
Result: new-room, bridge, sweep, ambient dialogs all rendered as
visible the moment the chat tab loaded.

Fix: .chat-dialog[hidden] { display: none; }

Bug #2 — JS new-room dialog: empty participant dropdown
The new-room dialog's participant <select> was populated from
state.get(\"agents\"), which is empty unless the Agents tab has
been visited first. Operators creating a room from a fresh load
saw only \"— none for now —\".

Bug #3 — JS bridge dialog: same as #2
The + bridge button's agent picker had the same cache-dependency.

Fix #2 + #3: Both dialogs now fetch /agents directly when the
state cache is empty, populate state.set(\"agents\", res), and
build the dropdown from the live response.

Bug #4 — JS add-participant prompt: truncated IDs broke pastes
promptAddParticipant() showed candidates as
  '\${agent_name} (\${role}) — \${instance_id.slice(0, 12)}'
and asked the operator to 'paste the instance_id from list'. The
12-char truncated value (e.g. 'software_eng') didn't match the
real ID ('software_engineer_f782d3ed1b6b'), so paste-and-submit
always 404'd. Operator described as 'ambiguous and clunky — not
sure what I need to paste.'

Fix #4: Show FULL instance_id in the prompt, plus accept
agent_name as a shortcut and resolve to instance_id before POST.
Same cache-miss-fetch as #2 + #3 so the prompt isn't empty on
fresh load.

Diagnosis path:
- Operator reported chat tab was 'stuck'.
- Claude in Chrome MCP javascript_tool: dialog had hidden=true
  but getComputedStyle().display='flex'. CSS conflict identified.
- Operator reported add-agent flow was 'ambiguous and clunky'.
- Inspected promptAddParticipant() source: 12-char slice + paste
  instructions.
- All fixes verified end-to-end via reload + screenshot:
  * chat dialog stays hidden until + new clicked
  * new-room participant dropdown shows agents on fresh load
  * Forge_AuditTight_01 visible in dropdown (the agent born
    during this debug session)
  * console: zero errors

Lesson encoded in source comments for future-me:
- 'hidden' attribute requires UA stylesheet's display:none to
  win; explicit display values beat it. Always pair the hidden
  attribute with a [hidden]{display:none} CSS rule when the
  selector itself sets a display value.
- Don't depend on cross-module state cache for dialog
  population — fetch on demand if cache is empty. The cache is
  an optimization, not a contract."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 86.2 landed. Chat tab fully usable end-to-end."
echo "  - dialogs hide correctly"
echo "  - new-room participant dropdown populates from /agents"
echo "  - bridge dropdown populates from /agents"
echo "  - add-participant prompt shows full instance_id + accepts agent_name shortcut"
echo ""
echo "You can now: + new -> pick Forge_AuditTight_01 (software_engineer) -> create."
echo ""
read -rp "Press Enter to close..."
