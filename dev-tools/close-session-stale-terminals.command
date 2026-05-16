#!/bin/bash
# One-shot session cleanup — same shape as close-stale-terminals.command
# at the repo root, but extends the pattern set with three more strings
# that accumulated during the B248-B260 session:
#
#   - "diag-session-tests"          — pytest runs left "Press return"
#   - "clean-git-locks"             — repeated lock-clear runs
#   - "fix-cryptography-dep"        — daemon-revival fix from B256
#   - "diagnose-import"             — earlier import smoke driver
#   - "fix-bug1-restart-and-reset"  — stale restart driver
#   - "force-restart-daemon"        — PRESERVED ONLY THE NEWEST ONE
#
# Anything that doesn't match a pattern stays open. In particular the
# active daemon tail (the most recent force-restart-daemon Terminal)
# stays running; we identify it as the window whose name contains
# "force-restart-daemon" AND whose ID is the highest such ID — the
# rest are stale prior runs that AppleScript's `close w saving no`
# can drop without prompting.

osascript <<'OSAEOF'
tell application "Terminal"
    -- Find the newest force-restart-daemon window (highest id);
    -- that's the live daemon tail we want to keep.
    set keepId to 0
    repeat with w in (every window)
        try
            set wName to name of w
            if wName contains "force-restart-daemon" then
                if (id of w) > keepId then
                    set keepId to id of w
                end if
            end if
        end try
    end repeat

    -- Now sweep. Close anything matching the stale-pattern set, EXCEPT
    -- the live daemon tail.
    repeat with w in (every window)
        try
            set wName to name of w
            set isStale to false
            if wName contains "commit-burst" then set isStale to true
            if wName contains "commit-adr" then set isStale to true
            if wName contains "tag-v0.5" then set isStale to true
            if wName contains "close-stale-terminals" then set isStale to true
            if wName contains "close-session-stale" then set isStale to true
            if wName contains "diag-session-tests" then set isStale to true
            if wName contains "clean-git-locks" then set isStale to true
            if wName contains "fix-cryptography-dep" then set isStale to true
            if wName contains "diagnose-import" then set isStale to true
            if wName contains "fix-bug1-restart-and-reset" then set isStale to true
            if wName contains "fix-frontier" then set isStale to true
            -- 2026-05-16 session additions:
            if wName contains "birth-test-author" then set isStale to true
            if wName contains "birth-migration-pilot" then set isStale to true
            if wName contains "birth-release-gatekeeper" then set isStale to true
            if wName contains "diag-import" then set isStale to true
            if wName contains "fix-multipart-dep" then set isStale to true
            if wName contains "generate-sbom" then set isStale to true
            if wName contains "force-restart-daemon" and (id of w) is not keepId then
                set isStale to true
            end if
            if isStale then
                close w saving no
            end if
        end try
    end repeat
end tell
OSAEOF

echo ""
echo "Session stale terminals swept. Live daemon tail preserved."
echo "Press any key to close."
read -n 1
