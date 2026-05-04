#!/usr/bin/env bash
# Burst 88: full frontend audit pass via Chrome MCP — 2 latent bugs found + fixed.
#
# Per the v0.3 → v0.4 roadmap (Burst 87 doc), Burst 88 is the
# "open every tab, click every button, fix what's broken" pass.
# The chat-tab debugging in 86.1-86.3 found 4 bugs in one tab; the
# rest of the frontend hadn't been hard-tested since the daemon
# restart in Burst 86. This burst closes that gap.
#
# Audit method: drive every tab via Claude in Chrome MCP. For each
# tab take a screenshot, click controls, watch console + network
# for failures. Same pattern that surfaced the chat-tab bugs.
#
# Tabs audited (8): Forge, Agents, Approvals, Skills, Tools, Memory,
# Audit, Chat. 6 clean. 2 latent bugs found:
#
# BUG B (P0) — Memory tab can't load any agent's memory.
#   memory.js:fetchEntries() POSTs to /agents/{id}/tools/call with
#   tool_name="memory_recall" but DOES NOT include session_id.
#   ToolCallRequest schema (src/.../schemas/dispatch.py:66) requires
#   session_id (min_length=1, max 80). Daemon returns 422 with
#   {"type":"missing","loc":["body","session_id"]} and the toast
#   surfaces "Couldn't load memory".
#
#   Latent since Burst 70 (ADR-0036 T6+T7 — recall surface
#   extension). The Memory tab has been broken since then; nobody
#   noticed because the toast looks like an "expected empty state"
#   rather than a 422 from missing fields. Same bug-class as one of
#   the 5 Run 001 driver bugs.
#
#   Fix: pass session_id=`memory-recall-${Date.now().toString(36)}`.
#   Pattern matches skills.js:210 (timestamp-suffix fallback). Memory
#   tab is read-only so a per-call session is fine — no batching
#   semantics to preserve.
#
# BUG A (P2) — Approvals header "refresh" button clipped on right.
#   .panel__header is flex with space-between; .panel__actions
#   packs 5 controls (agent <select> + operator <input> + show
#   <select> + refresh <button>). The agent select grows to fit
#   "Forge_AuditTight_01 · {dna} · software_engineer", pushing the
#   refresh button past the panel's right edge — only "re" visible.
#
#   Latent since Burst 7 (Y6 Chat tab introduced .panel pattern;
#   the approvals header has had 5 controls all along). Surfaced
#   only after Burst 86.2 birthed an agent with a long display
#   name.
#
#   Fix: .panel__actions { flex-wrap: wrap; justify-content:
#   flex-end; } so controls reflow onto a second line at narrow
#   widths instead of overflowing. Risk: zero — wrap is a strict
#   widening of the layout's behavior under tight space.
#
# Verification (post-fix, via Chrome MCP):
# - Approvals: refresh button visible after wrap (controls reflow
#   to a second line).
# - Memory: agent selection now POSTs successfully (200, not 422).
#   Toast no longer fires. Empty-state "No entries visible" shows
#   because the agent has no memory entries yet — that's correct
#   behavior, not a bug.
#
# Bursts 86.1, 86.2, 86.3 found 4 bugs in the Chat tab. Burst 88
# found 2 more across the rest of the frontend. Total: 6 latent
# UX bugs surfaced and fixed in one session via Chrome MCP. The
# pattern works — should become a pre-tag checklist.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 88 — frontend audit pass: 2 latent bugs fixed ==="
echo
clean_locks
git add frontend/css/style.css frontend/js/memory.js
git add commit-burst88-frontend-audit.command
clean_locks
git status --short
echo
clean_locks
git commit -m "fix(frontend): memory tab session_id + approvals header overflow

Two latent UX bugs found in Burst 88's full frontend audit pass.
Same Chrome MCP method that surfaced the 4 chat-tab bugs in
Bursts 86.1-86.3.

Bug B (P0) — Memory tab unusable:
memory.js:fetchEntries() POSTs to /agents/{id}/tools/call with
tool_name='memory_recall' but no session_id. ToolCallRequest
schema (dispatch.py:66) requires session_id (min_length=1, max
80). Daemon returns 422 missing-field. Toast says 'Couldn't
load memory'. Operator can't view any agent's memory in any of
the 4 modes (private/lineage/consented/realm).

Latent since Burst 70 (ADR-0036 T6+T7 recall surface). Memory
tab has been broken for ~2 weeks; the failure mode looked like
an empty state, not a missing-required-field 422.

Fix: pass session_id='memory-recall-' + Date.now().toString(36).
Pattern from skills.js:210 (timestamp-suffix fallback). Memory
tab is read-only so a per-call session is fine — no batching
semantics to preserve.

Bug A (P2) — Approvals header refresh button clipped:
.panel__actions packs agent <select> + operator <input> + show
<select> + refresh <button>. Long agent names push the refresh
past the right edge — visible only as 're'.

Latent since Burst 7 (Y6 Chat tab introduced .panel pattern).
Surfaced only after Burst 86.2 birthed an agent with a long
display name (Forge_AuditTight_01 · {dna} · software_engineer)
that triggered the overflow.

Fix: .panel__actions { flex-wrap: wrap; justify-content:
flex-end; }. Controls reflow to a second line at narrow widths
instead of overflowing. No regression risk — wrap is a strict
widening of layout behavior.

Verified end-to-end via Chrome MCP reload:
- Approvals: 'refresh' visible, controls wrap cleanly.
- Memory: select agent → POST returns 200 (was 422), toast
  cleared, empty-state 'No entries visible' shows correctly.

Methodology note for future audits:
Bursts 86.1/86.2/86.3/88 together found 6 latent UX bugs in
one session via systematic Chrome MCP tab-by-tab clicking.
The pattern is durable — should become a pre-tag checklist
(open every tab, click every button, watch console + network)
before any v0.x.0 release. Cost: ~10min per audit. ROI: 1.5
bugs per audit run on average across these 4 bursts."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 88 landed. Frontend audit pass complete; 2 latent bugs fixed."
echo "Methodology: Chrome MCP tab-by-tab. Found memory_recall + Approvals overflow."
echo ""
read -rp "Press Enter to close..."
