#!/usr/bin/env bash
# Burst 86.1: hotfix chat.js broken import + commit verify/start helpers.
#
# Bug: chat.js line 20 imported `{ state }` from ./state.js, but state.js
# exports {get, set, subscribe, update} — there's no `state` named export.
# ES module bind failure halts the WHOLE module graph, which is why every
# tab in the frontend was dead even though all 22 JS files loaded with
# 200 OK. The bug had been latent since Burst 7 (Y6 Chat tab) — it only
# surfaced on a fresh page load, which is what the Burst 86 daemon
# restart triggered.
#
# Fix:
#   - `import { state } from "./state.js"`  ->  `import * as state from "./state.js"`
#   - 6 occurrences of `state.agents`        ->  `state.get("agents")`
#     (matches every other module's pattern; state.js's API is the
#     function-call form, not direct property access)
#
# Diagnosed via Claude in Chrome MCP — read_console_messages found the
# exact SyntaxError on the first try; fix verified in browser with zero
# remaining console errors and all 8 tabs functional.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 86.1 — chat.js import hotfix + verify/start helpers ==="
echo
clean_locks
git add frontend/js/chat.js
git add verify-burst86-scheduler.command
git add start-full-stack.command
git add open-in-chrome.command
git add commit-burst86.1-chat-fix.command
clean_locks
git status --short
echo
clean_locks
git commit -m "fix(frontend): chat.js broken state import — was killing entire frontend

frontend/js/chat.js had two bugs that together broke the whole UI:

1. Line 20: import { state } from \"./state.js\"
   state.js exports {get, set, subscribe, update} — there is no
   named export called 'state'. ES modules halt the whole module
   graph when one fails to bind, so app.js never finished
   importing and zero event handlers ever wired.

2. Property-style access: state.agents (6 occurrences)
   state.js's API is function-call form: state.get(\"agents\").
   Every other module in the codebase uses this pattern; chat.js
   alone had it wrong.

Fix:
- import { state } -> import * as state  (matches agents.js,
  audit.js, forms.js, genres.js — the canonical pattern)
- state.agents      -> state.get(\"agents\")  (6 sites)

Diagnosis path:
- Burst 86's daemon restart triggered a fresh page load
- frontend.log showed all 22 JS modules loaded 200 OK
- daemon log showed only one /healthz call and nothing else —
  meaning JS had loaded but never made any API calls
- Hooked up Claude in Chrome MCP to the running browser
- read_console_messages surfaced the exact SyntaxError
  (chat.js:19:9, 'requested module ./state.js does not provide
  an export named state')
- Static review confirmed every other module uses
  'import * as state' + state.get() — chat.js was the outlier
- Two-line edit fixed it
- Verified post-fix: 0 console errors, Forge tab renders trait
  sliders + live preview + radar chart, Chat tab renders Y6
  ambient/retention panels + room creation modal, all 8 tabs
  switchable.

How long was this latent: since Burst 7 (Y6 Chat tab, ~2 weeks).
The bug only surfaces on a clean page load; if a session was
already in flight when chat.js was authored (HMR or browser
sticky state), the original author would never have seen the
SyntaxError. The Burst 86 daemon restart + fresh load was the
first clean boot since the regression landed.

Plus committing the diagnosis + driver scripts that were useful
during this debug:
- verify-burst86-scheduler.command — restart daemon, curl
  /scheduler/{status,tasks}, dump startup_diagnostics + daemon
  log tail. Reusable for future scheduler-wiring verifications.
- start-full-stack.command — stop standalone daemon, hand off
  to run.command which brings up daemon + frontend + opens
  browser. Reusable bridge between verify scripts and live use.
- open-in-chrome.command — open the frontend in Chrome
  specifically (system default may be Safari; Chrome has the
  Claude MCP extension available for in-browser debugging).

Why this is Burst 86.1 not 87: it's a hotfix, not new
functionality. ADR-0041 T3 (tool_call task type implementation)
remains the next burst, now unblocked by a working frontend
for live verification."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 86.1 landed. Frontend hotfix shipped + diagnosis tools committed."
echo "You can test the whole thing now. Daemon + frontend are running."
echo ""
read -rp "Press Enter to close..."
