#!/usr/bin/env bash
# Forest Soul Forge — load a pre-built demo scenario.
#
# Wraps reset.command + a copy step. Scenarios live in scenarios/<name>/
# and ship with the repo so a fresh checkout has demo-ready state without
# needing to run swarm-bringup.
#
# Usage (interactive):
#   ./scenarios/load-scenario.command
#       Prompts for which scenario to load.
#
# Usage (scripted):
#   ./scenarios/load-scenario.command synthetic-incident
#   ./scenarios/load-scenario.command fresh-forge
#
# What this script does:
#   1. Stops any running daemon  (calls stop.command)
#   2. Archives current state    (calls reset.command non-interactively)
#   3. Copies scenarios/<name>/* into the project root + data/
#   4. Tells the user to start.command
#
# Per-scenario contents:
#   audit_chain.jsonl     — top-level (daemon's default FSF_AUDIT_CHAIN_PATH)
#   registry.sqlite       — top-level (the derived index)
#   data/soul_generated/  — every birthed agent's soul.md + constitution.yaml

set -uo pipefail

# Find the repo root from this script's location (scenarios/ is one level deep).
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

BLUE="\033[1;34m"
GREEN="\033[1;32m"
YELLOW="\033[1;33m"
RED="\033[1;31m"
RESET="\033[0m"

say()  { printf "${BLUE}[scenario]${RESET} %s\n" "$*"; }
ok()   { printf "${GREEN}[scenario]${RESET} %s\n" "$*"; }
warn() { printf "${YELLOW}[scenario]${RESET} %s\n" "$*"; }
err()  { printf "${RED}[scenario]${RESET} %s\n" "$*" 1>&2; }

press_to_close() {
  echo ""
  echo "Press return to close this window."
  read -r _
}

# ---------- list available scenarios -------------------------------------

list_scenarios() {
  find scenarios -mindepth 1 -maxdepth 1 -type d \
    -not -name scripts \
    -not -name __pycache__ \
    | sort | sed 's|^scenarios/||'
}

NAME="${1:-}"

if [ -z "$NAME" ]; then
  echo ""
  echo "Available scenarios:"
  list_scenarios | nl -ba -s'. '
  echo ""
  read -r -p "Scenario name (or number): " choice
  if [[ "$choice" =~ ^[0-9]+$ ]]; then
    NAME="$(list_scenarios | sed -n "${choice}p")"
  else
    NAME="$choice"
  fi
fi

SCENARIO_DIR="scenarios/$NAME"
if [ ! -d "$SCENARIO_DIR" ]; then
  err "Scenario '$NAME' not found at $SCENARIO_DIR."
  echo ""
  echo "Available scenarios:"
  list_scenarios | sed 's|^|  - |'
  press_to_close
  exit 1
fi
say "Loading scenario: $NAME"

# ---------- stop daemon if running ---------------------------------------

if [ -x "stop.command" ]; then
  say "Stopping any running daemon..."
  ./stop.command < /dev/null > /dev/null 2>&1 || true
  ok "Stopped (or wasn't running)."
fi

# ---------- archive current state ----------------------------------------
# Inline what reset.command does, but skip the interactive prompt — the
# user already confirmed by picking a scenario.

STAMP="$(date +%Y%m%d-%H%M%S)"
say "Archiving current state with timestamp: $STAMP"

archive_one() {
  local target="$1"
  if [ -e "$target" ]; then
    mv "$target" "${target}.bak.${STAMP}"
    ok "  archived $target"
  fi
}
archive_one "audit_chain.jsonl"
archive_one "registry.sqlite"
archive_one "registry.sqlite-wal"
archive_one "registry.sqlite-shm"
archive_one "data/audit_chain.jsonl"
archive_one "data/registry.sqlite"
archive_one "data/soul_generated"

mkdir -p data/soul_generated data/forge/skills/installed data/plugins

# ---------- copy scenario into place -------------------------------------

say "Installing scenario contents..."

# Top-level files (daemon defaults: FSF_AUDIT_CHAIN_PATH=examples/audit_chain.jsonl
# would override these, but the canonical fresh-checkout layout puts the
# chain + registry at repo root). We copy what the scenario provides.
for f in audit_chain.jsonl registry.sqlite registry.sqlite-wal registry.sqlite-shm; do
  if [ -e "$SCENARIO_DIR/$f" ]; then
    cp "$SCENARIO_DIR/$f" "./$f"
    ok "  copied $f"
  fi
done

# data/ subtree
if [ -d "$SCENARIO_DIR/data" ]; then
  # Walk the scenario's data/ tree and mirror into the live data/.
  ( cd "$SCENARIO_DIR" && tar cf - data ) | tar xf - -C .
  ok "  copied data/ subtree"
fi

# ---------- presenter script pointer -------------------------------------

if [ -f "scenarios/scripts/$NAME.md" ]; then
  echo ""
  ok "Presenter script: scenarios/scripts/$NAME.md"
  echo "    open scenarios/scripts/$NAME.md   # or read it inline"
fi

echo ""
ok "Scenario '$NAME' loaded. Double-click start.command to bring the stack up."
press_to_close
