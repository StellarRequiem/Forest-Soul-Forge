#!/bin/bash
# Burst 191 — ADR-0056 E5 — cycle decision endpoint + frontend
# approve/deny/counter wiring.
#
# Closes the recursive-improvement loop. With this burst, an
# operator can:
#   - approve a cycle (merge --no-ff in workspace, audit emit)
#   - deny a cycle (audit emit; optionally delete branch)
#   - counter-propose with a note (audit emit; Smith picks up
#     the note in next explore-mode tick via memory_recall on
#     recent audit events)
#
# tools_add automation for requested_tools is DEFERRED to a
# follow-up — the merge mechanic is the load-bearing piece for
# E5. v0.1 tools_add is operator-driven via the existing
# POST /agents/{id}/tools/add endpoint after they review the
# cycle's requested_tools list.
#
# What ships:
#
#   src/forest_soul_forge/core/audit_chain.py:
#     - NEW event type: experimenter_cycle_decision. One event
#       covers all three actions (approve / deny / counter)
#       via the action field in event_data. event_data also
#       carries cycle_id, branch, head_sha, optional note,
#       merge_commit_sha (approve only), branch_deleted
#       (deny + delete_branch=true only).
#
#   src/forest_soul_forge/daemon/routers/cycles.py:
#     - NEW POST /agents/{instance_id}/cycles/{cycle_id}/decision
#       endpoint. Body: action ∈ {approve, deny, counter},
#       optional note (≤2000 chars), optional delete_branch
#       boolean (deny only). Response: ok, action, cycle_id,
#       branch, audit_seq, merge_commit_sha (approve), branch_
#       deleted (deny+delete), detail.
#     - Inline CycleDecisionRequest + CycleDecisionResponse
#       Pydantic models (router-scoped; not in
#       schemas/__init__.py).
#     - approve: switches to main + git merge --no-ff branch.
#       On conflict, aborts cleanly + returns 409 with detail.
#       Captures merge commit SHA for the audit + response.
#     - deny: optionally deletes branch via git branch -D.
#       Default keeps branch for forensics.
#     - counter: emits audit event with note. v0.1 doesn't
#       write to memory directly — Smith's explore-mode prompts
#       use memory_recall on recent audit events to pick up
#       counter-propose notes for the next tick. Memory-write
#       integration is a follow-up.
#     - All write paths take the daemon write lock (single-
#       writer SQLite + audit chain discipline).
#     - Path-traversal defense: cycle_id regex-validated
#       against ^cycle-\\d+\$ (same as the GET detail endpoint).
#     - require_writes_enabled + require_api_token gates apply.
#
#   frontend/js/chat.js:
#     - Detail view's bottom-of-expand block (previously a
#       muted note saying 'E5 will ship buttons') now renders
#       three real buttons + a textarea for the note + a
#       checkbox for delete_branch. _onCycleDecision handler
#       confirms destructive actions inline, POSTs to the
#       /decision endpoint, surfaces the response, and
#       refreshes the cycles list after 2s so status badges
#       update.
#     - Counter-propose enforces a non-empty note (the whole
#       point of countering is the note).
#
# Per ADR-0044 D3: new endpoint + new audit event type are
# additive. Pre-E5 daemons reading post-E5 audit chains see
# experimenter_cycle_decision as an unknown event type and
# emit a verification warning rather than failing — same
# forward-compat posture as ADR-0054 T4 introduced for
# tool_call_shortcut.
#
# Per ADR-0001 D2: decision endpoint emits an audit event but
# does NOT mutate Smith's constitution_hash or DNA. The merge
# into main is a state mutation in the workspace clone, not
# the kernel registry.
#
# Verification:
#   - 158 passed across the touched-modules sweep (cycles_router,
#     audit_chain, governance_pipeline, tool_dispatcher).
#   - build_app() imports clean.
#   - experimenter_cycle_decision present in KNOWN_EVENT_TYPES.
#
# After this burst's commit + push, the daemon needs ONE
# restart to load:
#   - B190's cycles_router (was the source of the 'error 404'
#     when the cycles pane was first opened)
#   - B191's decision endpoint
#   - Updated KNOWN_EVENT_TYPES
# This burst kicks the daemon at the end so the next
# /agents/{id}/cycles fetch from the chat tab returns real
# data instead of 404.
#
# Operator-facing follow-up (NOT in this commit):
#   - Open the chat tab → click 'Cycles' → after the daemon
#     restart, the pane should report 'workspace detected, 0
#     cycles' (since Smith hasn't run a work-mode cycle yet).
#   - To populate cycle 1, fire a work-mode dispatch against
#     Smith with a small target. Cycle report + diff will
#     surface in the pane; the new buttons let you decide
#     without leaving the chat.
#
# Next burst: B192 — E6 (operator safety runbook + posture-
# swap UI in the cycles pane).

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/audit_chain.py \
        src/forest_soul_forge/daemon/routers/cycles.py \
        frontend/js/chat.js \
        dev-tools/commit-bursts/commit-burst191-adr0056-e5-cycle-decision.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(experimenter): ADR-0056 E5 — cycle decision endpoint + buttons (B191)

Burst 191. Closes the recursive-improvement loop. Operator can
now approve / deny / counter-propose Smith's cycles directly
from the chat tab.

approve: git merge --no-ff in the workspace's main + emits
experimenter_cycle_decision audit event with the merge commit
SHA. On conflict, aborts cleanly and returns 409 so operator
resolves manually.

deny: emits the audit event with action=deny; optionally
deletes the branch (delete_branch=true). Default preserves
branch for forensics.

counter: emits audit event with note. Smith's next explore-mode
tick picks up the note via memory_recall on recent audit
events. Memory-write integration is a v0.2 polish.

tools_add automation for requested_tools is DEFERRED to a
follow-up — merge mechanic is the load-bearing piece for E5.

Ships:

audit_chain.py: experimenter_cycle_decision in
KNOWN_EVENT_TYPES. One event covers all three actions via
event_data.action.

routers/cycles.py: NEW POST .../cycles/{cycle_id}/decision
endpoint. Inline CycleDecisionRequest + CycleDecisionResponse
Pydantic models. require_writes_enabled + require_api_token.
Daemon write lock + path-traversal defense
(^cycle-\\d+\$ regex).

frontend/js/chat.js: cycles detail view renders 3 buttons +
note textarea + delete-branch checkbox. _onCycleDecision
confirms destructive actions inline, POSTs to /decision,
surfaces response, refreshes list 2s later for status
update. Counter-propose requires a non-empty note.

Per ADR-0044 D3: additive endpoint + additive event type;
forward-compat with pre-E5 daemons.

Per ADR-0001 D2: emits audit event; doesn't touch
constitution_hash or DNA. Merge is in workspace clone, not
kernel registry.

Verification: 158 passed across touched modules; build_app()
clean; experimenter_cycle_decision present in
KNOWN_EVENT_TYPES.

Daemon restart needed to load B190+B191 router code (was the
source of the '404 not found' on the cycles pane after
B190). This script restarts at the end.

Next burst: B192 — E6 (operator safety runbook +
posture-swap UI)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "--- restarting daemon to load B190 + B191 router code ---"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 6
  echo "      daemon should now respond to /agents/{id}/cycles"
else
  echo "      WARN: ${PLIST_LABEL} not registered with launchd."
  echo "      Restart the daemon by hand to pick up the new endpoints."
fi

echo ""
echo "=== Burst 191 commit + push + daemon-restart complete ==="
echo "Press any key to close this window."
read -n 1
