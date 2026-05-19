#!/bin/bash
# Close all stale Terminal windows from prior commit/tag bursts.
# Preserves anything not matching the patterns — specifically:
#   - start-full-stack.command (running daemon)
#   - verify-burst86-scheduler.command (scheduler health monitor)
#   - any other Terminal window whose title doesn't match
#
# Patterns matched (closed):
#   - "commit-burst*"  — every per-burst commit script's window
#   - "commit-adr*"    — every ADR-commit script's window
#   - "tag-v0.5"       — both v0.5.0-rc and v0.5.0 tag windows
#
# `saving no` skips the per-window confirmation dialog.

osascript <<'OSAEOF'
tell application "Terminal"
    set winList to every window
    repeat with w in winList
        try
            set wName to name of w
            if wName contains "commit-burst" or wName contains "commit-adr" or wName contains "tag-v0.5" or wName contains "close-stale-terminals" then
                close w saving no
            end if
        end try
    end repeat
end tell
OSAEOF

echo "stale terminals closed."
