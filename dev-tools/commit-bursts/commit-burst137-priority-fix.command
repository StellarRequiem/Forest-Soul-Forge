#!/bin/bash
# Burst 137 — fix tune-priority.command for actual macOS taskpolicy semantics.
#
# Burst 136 shipped tune-priority.command using
# `sudo taskpolicy -c user-interactive -p PID`. That command failed
# with "Could not parse 'user-interactive' as a QoS clamp" + rc=64
# when first run on the live stack 2026-05-05.
#
# Root cause: the recipe inherited from SESSION-HANDOFF §4b was
# incorrect for macOS:
#
#   1. The -c clamp on `taskpolicy` only accepts utility / background
#      / maintenance — it can only LOWER priority, not raise.
#      "user-interactive" is not a valid clamp value.
#   2. The `-p PID` form (modify existing process) doesn't accept -c
#      at all; per the usage spec, -p only accepts -b/-B/-t/-l.
#   3. The user-interactive QoS class is something processes opt INTO
#      via libdispatch APIs in their own code — it's not externally
#      imposable on a running process. macOS doesn't expose a knob
#      for this from userland.
#   4. The verify pass used -G which doesn't exist on macOS taskpolicy
#      (added illegal-option errors to the output).
#
# What ships:
#
#   tune-priority.command (rewritten) — replaces taskpolicy with
#     `sudo renice -n -10 -p PID`. BSD nice IS the correct macOS knob
#     for externally prioritizing an existing process. -10 = noticeably
#     higher than default (0) without being aggressive enough to starve
#     other system work. Verify pass uses `ps -o pid,nice,comm,user`
#     which actually works. Comments explain why the previous taskpolicy
#     approach was wrong so future readers don't repeat the recipe.
#
# Verification:
#   - Re-ran the corrected script live 2026-05-05 against same PIDs
#     used in the failed B136 run (Ollama PID 52883, Forest daemon
#     PID 63641). Both renice operations succeeded; ps verified
#     nice -10 on both processes.
#   - Inference performance unchanged (Metal does GPU work, nice
#     affects CPU scheduling) — this is for API responsiveness under
#     contention, not token speed.
#
# What this doesn't change:
#
#   - .env tuning (OLLAMA_KEEP_ALIVE=-1, OLLAMA_NUM_PARALLEL=1) still
#     applies; still requires Ollama restart to activate. Documented
#     in .env.example via B136; no .env.example change needed here.
#   - quit-bg-apps.command was correct; no change needed.
#   - Tasks #2 and #3 close as designed; the priority bump is now
#     a working operator op.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add tune-priority.command \
        dev-tools/commit-bursts/commit-burst137-priority-fix.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(ops): tune-priority uses renice, not taskpolicy clamp (B137)

Burst 137. The B136 tune-priority.command used
'sudo taskpolicy -c user-interactive -p PID', which failed with
'Could not parse user-interactive as a QoS clamp' + rc=64 when
first run live on 2026-05-05.

Root cause: the recipe inherited from SESSION-HANDOFF was wrong
for macOS:

- taskpolicy -c clamp only accepts utility/background/maintenance
  (downgrades only). user-interactive is not a valid clamp.
- The -p PID form (modify existing process) doesn't accept -c at
  all per the usage spec (-p only takes -b/-B/-t/-l).
- user-interactive QoS is opted-into via libdispatch in process code,
  not externally imposable on a running process.
- The verify -G flag I used doesn't exist on macOS taskpolicy.

Replaces taskpolicy with sudo renice -n -10 -p PID — the correct
macOS knob for externally prioritizing an existing process. BSD
nice is universal, well-understood, and actually works.

Verification:
- Re-ran corrected script live 2026-05-05 on same PIDs that failed
  in the B136 attempt. Both renice operations succeeded; ps verified
  nice -10 on both Ollama (PID 52883) and Forest daemon (PID 63641).

Inline comments now document why the previous taskpolicy approach
was wrong so future readers don't repeat the recipe.

Inference speed unchanged — Metal does GPU work, nice affects CPU
scheduling. This is for API responsiveness under contention, not
token throughput.

Tasks #2 and #3 of the cross-session task list now close cleanly."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 137 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
