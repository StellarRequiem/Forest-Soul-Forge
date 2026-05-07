#!/bin/bash
# Burst 190 — ADR-0056 E4 — display-mode chat-tab pane.
#
# Read-only review surface for Smith's branch-isolated work
# cycles. New chat-tab mode (third mode alongside Rooms +
# Assistant) that lists experimenter/cycle-* branches and
# expands per-cycle into commit message + cycle report + full
# diff (size-capped at 200 KB).
#
# Approve / Deny / Counter-propose action surfaces are
# DEFERRED to E5 (B191) because they overlap with the self-
# augmentation flow's merge + tools_add automation. E4 ships
# pure visibility — operator merges/discards manually via
# `git merge --no-ff` or `git branch -D` until E5 lands.
#
# What ships:
#
#   src/forest_soul_forge/daemon/config.py:
#     - NEW DaemonSettings.experimenter_workspace_path field.
#       Defaults to ~/.fsf/experimenter-workspace/Forest-Soul-Forge
#       (provisioned by birth-smith.command). Path | None — None
#       when no experimenter substrate is wired (test contexts,
#       headless deployments). Cycles router treats None as
#       'no cycles available' and returns empty list rather
#       than crashing.
#
#   src/forest_soul_forge/daemon/schemas/cycles.py (NEW):
#     - CycleStatus literal: pending|ready|passed|failed|merged
#     - CycleSummary (one row in list view): cycle_id, branch,
#       head_sha, head_message, head_timestamp, files_changed,
#       insertions, deletions, has_cycle_report, status
#     - CycleDetail (expand view): all of CycleSummary plus
#       full_commit_message, diff (size-capped), diff_truncated,
#       cycle_report_path, cycle_report_content,
#       requested_tools (parsed from yaml fence in report)
#     - CycleListOut: list + workspace_path + workspace_available
#
#   src/forest_soul_forge/daemon/routers/cycles.py (NEW):
#     - GET /agents/{instance_id}/cycles — list cycles. Cheap
#       O(branches) git operations: rev-parse + diff-stat per
#       branch. Suitable for ~5s refresh tick.
#     - GET /agents/{instance_id}/cycles/{cycle_id} — detail
#       view. Full diff (size-capped at 200 KB), full commit
#       message, cycle report content, parsed requested_tools.
#       cycle_id validated against ^cycle-\d+$ regex (path-
#       traversal defense — refuses arbitrary cycle_id strings).
#     - Both endpoints READ-ONLY. No git mutations, no daemon
#       state mutations, no audit emission.
#     - Reads git via subprocess (no GitPython dep). 8s timeout
#       per command — bug-shaped if exceeded. Defensive: every
#       failure mode degrades to empty data rather than 500.
#
#   src/forest_soul_forge/daemon/schemas/__init__.py:
#     - Export the four new schema classes.
#
#   src/forest_soul_forge/daemon/app.py:
#     - Import + include cycles_router. Sits AFTER
#       marketplace_router in the include order.
#
#   frontend/index.html:
#     - NEW 'Cycles' button in the chat-mode-toggle next to
#       Rooms + Assistant.
#     - NEW chat-pane-cycles section: panel header (title +
#       refresh button + status), three states (empty,
#       no-workspace, list+detail), all hidden until JS
#       populates.
#
#   frontend/js/chat.js:
#     - VALID_MODES extended to ["rooms", "assistant", "cycles"].
#     - showChatMode() handles three panes (was two).
#     - NEW refreshCyclesPane(): resolves Smith's instance_id
#       via /agents query (finds first role=experimenter row),
#       fetches /cycles, renders list newest-first.
#     - NEW _expandCycle(): fetches detail + renders commit
#       message + cycle report + diff in <details> blocks.
#     - Wire refresh button.
#     - Note in detail view: 'Approve / Deny / Counter-propose
#       surfaces are E5'. Includes copy-pasteable git commands
#       for manual merge/discard until then.
#
#   frontend/css/style.css:
#     - NEW .chat-cycles-* classes covering pane layout, row
#       styling (hover + active), status badges (color-coded
#       per status), detail panel + nested <details> styling,
#       monospace pre blocks for commit/report/diff content.
#     - Status badge colors: pending=gray, ready=green-25%,
#       passed=green-45%, failed=red-45%, merged=blue-35%.
#       Operator gets a glanceable list view.
#
#   tests/unit/test_cycles_router.py (NEW):
#     - 6 unit tests via FastAPI TestClient + a real temp git
#       repo with two cycle branches:
#       - cycle-1 with CYCLE_REPORT.md (test_outcome: passed)
#       - cycle-2 without report
#       Coverage: list returns 2 cycles sorted ascending,
#       status differentiation (cycle-1=passed vs
#       cycle-2=pending), detail endpoint returns diff +
#       report content, unknown agent → 404, invalid
#       cycle_id → 400, missing cycle → 404.
#
# Per ADR-0044 D3: zero kernel ABI breakage. New endpoints
# are additive; new settings field is optional with a sensible
# default. Pre-E4 daemons reading post-E4 .env files just
# don't see the new field (Pydantic ignores extra env vars
# by default per the daemon's settings config).
#
# Per ADR-0001 D2: read-only endpoints. Touch no agent
# identity. cycle_id validation defends against path traversal
# (refuses anything not matching ^cycle-\d+$).
#
# Verification:
#   - 88 passed across the touched-modules sweep
#     (test_cycles_router 6, test_mode_kit_clamp 22,
#     test_marketplace_index 13, test_tool_dispatcher 47)
#   - build_app() imports clean
#   - cycles routes registered:
#       GET /agents/{instance_id}/cycles
#       GET /agents/{instance_id}/cycles/{cycle_id}
#
# Operator-facing follow-up (NOT in this commit):
#   - Open the chat tab → click 'Cycles' button. With Smith
#     just-born and no work-mode cycles fired yet, the pane
#     shows: 'No cycles yet. Smith works on branches under
#     experimenter/cycle-N...' (cycles=[], workspace_available=true).
#   - Once a work-mode cycle lands a commit on
#     experimenter/cycle-1 with optional CYCLE_REPORT.md,
#     the pane lists it with status badge + diff stats.
#     Click to expand for full diff + report.
#   - Until E5 ships, manually merge approved cycles via
#     `cd ~/.fsf/experimenter-workspace/Forest-Soul-Forge &&
#      git merge --no-ff experimenter/cycle-N` from the
#     workspace; or delete with `git branch -D ...`.
#
# Next burst: B191 — E5 self-augmentation flow (operator
# approval triggers automated merge + tools_add).

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/config.py \
        src/forest_soul_forge/daemon/schemas/cycles.py \
        src/forest_soul_forge/daemon/schemas/__init__.py \
        src/forest_soul_forge/daemon/routers/cycles.py \
        src/forest_soul_forge/daemon/app.py \
        frontend/index.html \
        frontend/js/chat.js \
        frontend/css/style.css \
        tests/unit/test_cycles_router.py \
        dev-tools/commit-bursts/commit-burst190-adr0056-e4-cycles-pane.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(experimenter): ADR-0056 E4 — display-mode cycles pane (B190)

Burst 190. Read-only review surface for Smith's branch-isolated
work cycles. New chat-tab mode (third alongside Rooms +
Assistant) that lists experimenter/cycle-* branches and
expands per-cycle into commit message + cycle report + full
diff (size-capped at 200 KB).

Approve / Deny / Counter-propose surfaces deferred to E5
(B191) because they overlap with the self-augmentation flow's
merge + tools_add automation. E4 ships pure visibility —
operator merges/discards manually until E5 lands.

Ships:

config.py: NEW experimenter_workspace_path setting (default
~/.fsf/experimenter-workspace/Forest-Soul-Forge — provisioned
by birth-smith.command). None when substrate not wired; the
cycles router degrades to empty list.

schemas/cycles.py: 4 Pydantic models — CycleStatus literal,
CycleSummary, CycleDetail, CycleListOut.

routers/cycles.py: 2 read-only endpoints. List uses cheap
git operations (rev-parse + diff-stat per branch). Detail
includes full diff (size-capped), full commit message, cycle
report content, parsed requested_tools yaml fence. Reads git
via subprocess (no GitPython dep), 8s per-call timeout.
cycle_id validated against ^cycle-\\d+\$ regex (path-traversal
defense).

frontend/index.html: NEW 'Cycles' button + chat-pane-cycles
panel.

frontend/js/chat.js: VALID_MODES extended; new
refreshCyclesPane resolves Smith's instance_id via /agents
query, fetches /cycles, renders newest-first. _expandCycle
fetches detail + renders commit/report/diff in details
blocks. Includes manual merge/discard git command hints
until E5.

frontend/css/style.css: NEW .chat-cycles-* classes —
status badges color-coded (pending=gray, ready=green-25%,
passed=green-45%, failed=red-45%, merged=blue-35%).

tests/unit/test_cycles_router.py: 6 tests via TestClient +
real temp git repo with two cycle branches.

Per ADR-0044 D3: zero ABI breakage. New endpoints + setting
are additive.

Per ADR-0001 D2: read-only. cycle_id regex-validated against
path traversal.

Verification: 88 passed across the touched-modules sweep,
build_app() imports clean.

Next burst: B191 — E5 self-augmentation flow (operator
approval triggers automated merge + tools_add)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 190 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
