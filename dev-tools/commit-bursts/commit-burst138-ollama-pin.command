#!/bin/bash
# Burst 138 — restart-ollama-pin.command + KEEP_ALIVE doc corrections.
#
# B136 shipped .env documentation claiming OLLAMA_KEEP_ALIVE=-1 in
# Forest's .env would pin the model in Ollama. That was wrong on
# macOS specifics — Forest's .env is read by Forest's daemon, not by
# Ollama.app. Ollama.app launched from /Applications/ reads its env
# from launchd's per-user (gui/$UID) domain. Putting KEEP_ALIVE in
# .env had no effect on Ollama's behavior; it was documented behavior
# that didn't happen.
#
# Discovered live 2026-05-05 when running tune-priority.command's
# reminder section: the user followed the .env path, but `ollama ps`
# would have shown "UNTIL: 5 minutes from now" instead of "Forever"
# because Ollama wasn't seeing the env var. The correct macOS path is
# `launchctl setenv` in the gui/$UID domain BEFORE Ollama.app starts.
#
# What ships:
#
#   restart-ollama-pin.command (new at repo root) — end-to-end
#     KEEP_ALIVE activation. Sets OLLAMA_KEEP_ALIVE=-1 and
#     OLLAMA_NUM_PARALLEL=1 via launchctl in the gui/$UID domain
#     (no sudo), gently quits Ollama.app via AppleScript, force-kills
#     after 10s if it's still running, reopens, waits up to 20s for
#     the API to come back, then sends a one-token generate request
#     to qwen2.5-coder:7b. After this, `ollama ps` shows the model
#     loaded with "UNTIL: Forever" — proof KEEP_ALIVE is active.
#     Verified live 2026-05-05.
#
#   .env.example — corrects the B136 KEEP_ALIVE docs. Now explains:
#     (a) putting KEEP_ALIVE in this .env doesn't affect Ollama.app,
#     (b) the correct path is launchctl setenv + restart Ollama,
#     (c) restart-ollama-pin.command is the one-shot helper,
#     (d) launchctl setenv values are session-scoped — survive
#     until logout, then need re-running (or persisted via ~/.zprofile
#     or ~/Library/LaunchAgents/ which is Layer 3 of the 24/7 recipe).
#
#   tune-priority.command — section 4 reminder text rewritten. Was
#     instructing the operator to manually quit Ollama and curl a
#     model, all of which would have failed silently because the env
#     wasn't set. Now points at restart-ollama-pin.command instead.
#
# What this commit doesn't change:
#
#   - quit-bg-apps.command: correct as-is, no change
#   - The actual launchctl env vars: those were set live during the
#     2026-05-05 run; they persist until logout in this session
#   - The .env file (gitignored) at /Users/llm01/Forest-Soul-Forge/.env
#     still has the misleading KEEP_ALIVE lines from B136. They're
#     ignored by Forest's daemon and Ollama, so they're harmless
#     noise — left as a memo so the operator remembers the
#     launchctl + restart sequence is needed for them to matter
#
# Verification (2026-05-05 live run):
#   - launchctl getenv OLLAMA_KEEP_ALIVE returns "-1"
#   - launchctl getenv OLLAMA_NUM_PARALLEL returns "1"
#   - ollama ps shows qwen2.5-coder:7b loaded, UNTIL=Forever, 100% GPU
#   - Memory used 14.04 GB (was 12.99 before pin); the 5 GB model
#     is now resident in unified memory
#   - /api/tags continues to enumerate all 5 native Ollama models
#
# Closes the docs accuracy gap on the KEEP_ALIVE recipe. T3 stays
# closed; this is a B137-style follow-on fix (recipe correction +
# new utility script, not new task work).

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add restart-ollama-pin.command \
        .env.example \
        tune-priority.command \
        dev-tools/commit-bursts/commit-burst138-ollama-pin.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(ops): KEEP_ALIVE needs launchctl + Ollama restart, not .env (B138)

Burst 138. B136 shipped .env documentation claiming
OLLAMA_KEEP_ALIVE=-1 in Forest's .env would pin Ollama's loaded
model. That was wrong on macOS specifics: Forest's .env is read by
Forest's daemon, not by Ollama.app. Ollama.app launched from
/Applications/ reads its env from launchd's per-user (gui/UID)
domain. Putting KEEP_ALIVE in .env had no effect on Ollama.

Discovered live 2026-05-05 when restart-ollama-pin.command's
verify pass would have shown UNTIL=5min instead of UNTIL=Forever
under the old recipe.

Ships:

- restart-ollama-pin.command (new at repo root): end-to-end
  KEEP_ALIVE activation. launchctl setenv → quit Ollama.app via
  AppleScript → reopen → touch qwen2.5-coder:7b. After this run
  'ollama ps' shows the model loaded with UNTIL=Forever and
  100% GPU. Verified live 2026-05-05 — memory used jumped from
  12.99 GB to 14.04 GB, confirming the 5 GB model is resident.

- .env.example: corrects the B136 KEEP_ALIVE docs. Explains that
  the .env path doesn't work, points at restart-ollama-pin.command
  for the correct path, documents that launchctl setenv values are
  session-scoped (need re-running after logout, or persisted via
  ~/.zprofile or ~/Library/LaunchAgents/ which is Layer 3 of the
  24/7 ops recipe).

- tune-priority.command: section 4 reminder text rewritten. Was
  instructing the operator to manually quit Ollama and curl a
  model — all of which would have failed silently without the env
  set. Now points at restart-ollama-pin.command.

Doesn't change:
- quit-bg-apps.command (correct as-is)
- The local .env (gitignored) — its misleading KEEP_ALIVE lines
  from B136 are harmless and serve as a memo

Closes the docs accuracy gap on the KEEP_ALIVE recipe. T3 stays
closed; this is a B137-style follow-on fix."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 138 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
