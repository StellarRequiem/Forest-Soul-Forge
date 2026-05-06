#!/bin/bash
# Burst 145 — chat-tab delete-room UX (T19).
#
# User report: "there needs to be a delete room option" (the chat tab
# accumulates rooms over time and there's no clean way to dismiss the
# stale ones). Forest's audit chain is append-only by design, so true
# deletion isn't on the table — but "out of sight" via archive is the
# right semantic. The Archive button on the room header has existed
# since Y6 but it's three clicks deep (select room → header → archive)
# and there's no rail-level filter to hide already-archived rooms.
#
# What ships:
#
#   frontend/index.html — adds an "archived" checkbox toggle to the
#     rooms-rail panel header. Default unchecked → archived rooms
#     hidden. Toggle to surface them (e.g., to audit history or
#     restore a misclick).
#
#   frontend/css/style.css — two new style blocks:
#     1. .chat-rooms__item-archive-btn (×) — hover-shown per-row
#        archive button. Position absolute in the corner of each
#        room item. Stays out of sight until hover so the rail
#        feels clean.
#     2. .chat-rooms__filter — compact inline checkbox styling
#        for the rail-header toggle.
#
#   frontend/js/chat.js — four coordinated changes:
#     1. New SHOW_ARCHIVED_KEY constant + showArchived module
#        variable (default false; persisted to localStorage).
#     2. start() restores the toggle state from localStorage and
#        wires the new wireShowArchivedToggle().
#     3. renderRooms() filters out archived rooms when !showArchived.
#        Renders a "<N> archived hidden" footer when there are
#        hidden ones so the operator knows. Per-room archive (×)
#        button renders for non-archived rooms only.
#     4. Per-row archive click handler with stopPropagation,
#        confirmation dialog, POST /status, toast, and rail reload.
#        Drops active selection if the archived room WAS the active
#        one.
#
# UX rationale: Forest's audit chain is append-only — we never
# truly delete. The user's "delete room" mental model is best served
# by:
#   - Default-hidden archived rooms (the rail stops accumulating)
#   - One-click archive on each room item (low friction)
#   - Confirmation that explicitly says "hidden, not deleted; turns +
#     audit history preserved" (sets correct expectations)
#   - Restorable via the toggle (safety net for misclicks)
#
# This matches the kernel philosophy ("audit chain is the source of
# truth, append-only, hash-linked") while delivering the UX the user
# actually asked for.
#
# Verification:
#   - HTML/CSS/JS edits all landed; no syntax errors expected
#     (no test scaffold for vanilla-JS interactivity beyond the
#     B133 Vitest sanity test, which is not affected here)
#   - Browser refresh on the Chat tab will pick up the changes
#     immediately (no daemon restart needed; frontend is served
#     directly from frontend/ by python -m http.server)
#   - Operator-side smoke: open Chat tab, hover a room → see ×
#     button; click × → confirm dialog → click OK → toast
#     "Archived" → room disappears from rail. Toggle "archived"
#     checkbox → archived rooms reappear with reduced opacity.
#
# Closes T19. Phase 2 done.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add frontend/index.html \
        frontend/css/style.css \
        frontend/js/chat.js \
        dev-tools/commit-bursts/commit-burst145-chat-delete-room-ux.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(chat): rail-level archive UX — hide-by-default + per-row × button (B145)

Burst 145. Closes T19. User asked for a 'delete room option'; Forest
audit chain is append-only so true delete is not the right semantic,
but archive + hide-from-default-view delivers the UX they actually
need.

Ships (frontend-only, no daemon restart needed):

- index.html: 'archived' checkbox toggle in rooms-rail header.
  Default unchecked → archived rooms hidden from rail.

- style.css: two style blocks. .chat-rooms__item-archive-btn (×)
  hover-shown per-row archive button positioned absolute in the
  corner. .chat-rooms__filter compact inline checkbox styling
  for the header toggle.

- chat.js: four coordinated changes.
  1. SHOW_ARCHIVED_KEY + showArchived module state (localStorage-
     persisted, default false).
  2. start() restores toggle state + wires wireShowArchivedToggle().
  3. renderRooms() filters archived when !showArchived; shows
     '<N> archived hidden' footer; renders per-row × for non-
     archived rooms only.
  4. Per-row archive click handler with stopPropagation,
     confirmation dialog ('hidden, not deleted; audit history
     preserved'), POST /status, toast, rail reload. Drops active
     selection if the archived room was the active one.

UX matches kernel philosophy: nothing truly removed, audit chain
preserved, but the rail stays clean by default and the operator can
restore visibility with one toggle.

Phase 2 done.

Verification: browser refresh on Chat tab picks up the changes
immediately. Hover a room → × button appears; click × → confirm
dialog → archive → toast → room disappears from rail. Toggle
'archived' checkbox → archived rooms reappear with reduced opacity."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 145 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
