#!/bin/bash
# Burst 139 — launchd auto-start for Ollama + Forest daemon (Layer 3).
#
# Closes Layer 3 of the SESSION-HANDOFF §4b 24/7 ops recipe. After
# Layers 1 (background-app quits) and 2 (priority + KEEP_ALIVE), this
# locks both daemons under launchd supervision so they:
#
#   - Auto-start at login (RunAtLoad=true)
#   - Auto-restart on crash (KeepAlive=true)
#   - Run with EnvironmentVariables baked in at launch time
#     (no separate `launchctl setenv` step needed across reboots)
#   - Log to /tmp/{ollama,forest-daemon}.{out,err}.log
#
# What ships:
#
#   dev-tools/launchd/dev.forest.ollama.plist.template — Ollama
#     LaunchAgent. ProgramArguments points at @OLLAMA_BIN@ (substituted
#     at install time by install-launchagents.command). EnvironmentVariables
#     dict has OLLAMA_KEEP_ALIVE=-1 + OLLAMA_NUM_PARALLEL=1 baked in.
#     ProcessType=Interactive for foreground-app priority class.
#
#   dev-tools/launchd/dev.forest.daemon.plist.template — Forest
#     daemon LaunchAgent. ProgramArguments hard-codes
#     /Users/llm01/Forest-Soul-Forge/.venv/bin/python -m
#     forest_soul_forge.daemon (Forest's repo path is well-known on
#     this machine). WorkingDirectory set to the repo root.
#     EnvironmentVariables include FSF_LOCAL_MODEL=qwen2.5-coder:7b
#     and PATH so subprocesses are findable.
#
#   install-launchagents.command (new at repo root) — 14-section
#     installer. Detects Ollama path (probes /opt/homebrew, /usr/local,
#     /Applications/Ollama.app), verifies Forest venv + importability,
#     stops any currently-running Ollama.app/Forest daemon (port
#     conflict prevention), substitutes @OLLAMA_BIN@ into the Ollama
#     template, copies both plists to ~/Library/LaunchAgents/, lints
#     with plutil, unloads any prior versions via `launchctl bootout`,
#     bootstraps via `launchctl bootstrap gui/$UID`, waits up to 20s
#     for both APIs to come back, touches qwen2.5-coder:7b to confirm
#     KEEP_ALIVE is active, prints summary + revert instructions.
#
#   uninstall-launchagents.command (new at repo root) — clean revert.
#     `launchctl bootout` both, delete plist files, verify nothing
#     left, point the operator at how to use Forest manually again.
#
# Why headless Ollama (no .app menu bar): launchd-managed bare CLI
# gives proper supervision (KeepAlive restarts on crash) and clean
# env propagation. The Ollama.app's GUI is nice-to-have but not 24/7
# critical. To revert: uninstall-launchagents.command + re-enable
# Ollama.app in System Settings → Login Items.
#
# Verification (2026-05-05 live install):
#   - launchctl list shows dev.forest.ollama (PID 59838, exit 0)
#     and dev.forest.daemon (PID 59841, exit 0)
#   - Ollama API responsive after 2s
#   - Forest daemon /healthz responsive after 1s
#   - ollama ps shows qwen2.5-coder:7b loaded, UNTIL=Forever, 100% GPU
#   - Memory used 14.43 GB after install (model resident); pressure GREEN
#   - Side-discovery: launchctl revealed pre-existing agents from Alex's
#     OTHER Forest projects (com.forest.auto, com.forest.cus,
#     com.stellarrequiem.forest-api which is failing exit 1) — different
#     namespace from ours (dev.forest.*), no conflict
#
# Closes T4 of the cross-session task list. Layers 1+2+3 of the
# 24/7 ops recipe now all delivered. Operator-side optional next:
# System Settings → Battery for sleep prevention + power-failure
# auto-start + wake-for-network. Not blocking T6 (specialist agent
# stable births).

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/launchd/dev.forest.ollama.plist.template \
        dev-tools/launchd/dev.forest.daemon.plist.template \
        install-launchagents.command \
        uninstall-launchagents.command \
        dev-tools/commit-bursts/commit-burst139-launchd-agents.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(ops): launchd auto-start for Ollama + Forest daemon (B139)

Burst 139. Closes Layer 3 of the SESSION-HANDOFF §4b 24/7 ops
recipe. After Layers 1 (background-app quits) and 2 (renice +
KEEP_ALIVE via launchctl), this locks both daemons under launchd
supervision so they auto-start at login and auto-restart on crash.

Ships:

- dev-tools/launchd/dev.forest.ollama.plist.template: Ollama
  LaunchAgent. ProgramArguments points at @OLLAMA_BIN@ (substituted
  at install time). EnvironmentVariables bake in OLLAMA_KEEP_ALIVE=-1
  + OLLAMA_NUM_PARALLEL=1 + PATH. ProcessType=Interactive for
  foreground-app priority class.

- dev-tools/launchd/dev.forest.daemon.plist.template: Forest daemon
  LaunchAgent. Hard-codes /Users/llm01/Forest-Soul-Forge/.venv/bin/
  python -m forest_soul_forge.daemon. WorkingDirectory set.
  EnvironmentVariables include FSF_LOCAL_MODEL=qwen2.5-coder:7b.

- install-launchagents.command (new at repo root): 14-section
  installer. Detects Ollama path (/opt/homebrew vs /usr/local vs
  /Applications/Ollama.app), verifies Forest venv, stops running
  Ollama.app + Forest daemon for port-conflict prevention,
  substitutes paths into templates, copies to ~/Library/
  LaunchAgents/, lints with plutil, unloads prior versions via
  launchctl bootout, bootstraps via launchctl bootstrap gui/UID,
  waits up to 20s for APIs, touches qwen2.5-coder:7b to confirm
  KEEP_ALIVE active.

- uninstall-launchagents.command (new at repo root): clean revert.
  launchctl bootout both + delete plists + verify + show how to
  use Forest manually again.

Tradeoff: headless Ollama (no .app menu bar) for proper supervision
+ clean env propagation. To revert: uninstall + re-enable Ollama.app
in System Settings → Login Items.

Verified live 2026-05-05:
- launchctl list shows dev.forest.ollama (PID 59838, exit 0) and
  dev.forest.daemon (PID 59841, exit 0)
- Ollama API responsive in 2s, Forest daemon /healthz in 1s
- ollama ps: qwen2.5-coder:7b UNTIL=Forever, 100% GPU
- Memory used 14.43 GB, pressure GREEN
- Side-find: pre-existing launchd agents from Alex's other K8s
  Forest projects (com.forest.*, com.stellarrequiem.forest-api
  failing exit 1) — different namespace, no conflict

Closes T4. Layers 1+2+3 all delivered. Optional operator-side
next: System Settings → Battery for sleep prevention + power-
failure auto-start + wake-for-network. Not blocking T6 specialist
agent stable births."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 139 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
