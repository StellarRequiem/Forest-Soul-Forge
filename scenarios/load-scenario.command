#!/usr/bin/env bash
# Forest Soul Forge — load a pre-built demo scenario.
#
# Scenarios live in scenarios/<name>/ and ship with the repo so a fresh
# checkout has demo-ready state without needing to run swarm-bringup.
#
# Usage:
#   ./scenarios/load-scenario.command                              # interactive picker
#   ./scenarios/load-scenario.command synthetic-incident           # default target = prod
#   ./scenarios/load-scenario.command synthetic-incident demo      # isolated demo/ dir
#   ./scenarios/load-scenario.command synthetic-incident prod      # explicit production
#
# Targets (F7):
#   prod (default) — replaces the top-level audit_chain.jsonl + registry.sqlite +
#                    data/soul_generated, the same state that start.command serves.
#                    Current state is archived to .bak.<timestamp> first.
#   demo           — installs into demo/ dir without touching prod state.
#                    Pair with start-demo.command to serve from there.
#                    Demo state never overwrites production.
#
# What this script does:
#   1. Stops any running daemon  (calls stop.command)
#   2. Archives current state for the chosen target (.bak.<timestamp>)
#   3. Copies scenarios/<name>/* into the target paths
#   4. Tells the user which start command to run next

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
TARGET="${2:-prod}"

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

if [ "$TARGET" != "prod" ] && [ "$TARGET" != "demo" ]; then
  err "Unknown target '$TARGET'. Use 'prod' (default) or 'demo'."
  press_to_close
  exit 1
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
say "Loading scenario: $NAME (target: $TARGET)"

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

if [ "$TARGET" = "demo" ]; then
  # Isolated demo target — only touch demo/ paths.
  archive_one "demo/audit_chain.jsonl"
  archive_one "demo/registry.sqlite"
  archive_one "demo/registry.sqlite-wal"
  archive_one "demo/registry.sqlite-shm"
  archive_one "demo/soul_generated"
  mkdir -p demo/soul_generated demo/forge/skills/installed demo/plugins
else
  # Production target — same as the F4 behavior, replaces top-level state.
  archive_one "audit_chain.jsonl"
  archive_one "registry.sqlite"
  archive_one "registry.sqlite-wal"
  archive_one "registry.sqlite-shm"
  archive_one "data/audit_chain.jsonl"
  archive_one "data/registry.sqlite"
  archive_one "data/soul_generated"
  mkdir -p data/soul_generated data/forge/skills/installed data/plugins
fi

# ---------- copy scenario into place -------------------------------------

say "Installing scenario contents into $TARGET target..."

# Map scenario files to target paths. For prod, top-level + data/ —
# matches the daemon's default config. For demo, everything goes under
# demo/ — matches start-demo.command's env-var overrides.
if [ "$TARGET" = "demo" ]; then
  CHAIN_DST="demo/audit_chain.jsonl"
  REG_DST="demo/registry.sqlite"
  REG_WAL_DST="demo/registry.sqlite-wal"
  REG_SHM_DST="demo/registry.sqlite-shm"
  SOUL_DST="demo/soul_generated"
else
  CHAIN_DST="audit_chain.jsonl"
  REG_DST="registry.sqlite"
  REG_WAL_DST="registry.sqlite-wal"
  REG_SHM_DST="registry.sqlite-shm"
  SOUL_DST="data/soul_generated"
fi

# Top-level chain + registry files from the scenario root.
[ -e "$SCENARIO_DIR/audit_chain.jsonl" ]      && cp "$SCENARIO_DIR/audit_chain.jsonl"      "$CHAIN_DST"   && ok "  copied → $CHAIN_DST"
[ -e "$SCENARIO_DIR/registry.sqlite" ]        && cp "$SCENARIO_DIR/registry.sqlite"        "$REG_DST"     && ok "  copied → $REG_DST"
[ -e "$SCENARIO_DIR/registry.sqlite-wal" ]    && cp "$SCENARIO_DIR/registry.sqlite-wal"    "$REG_WAL_DST" && ok "  copied → $REG_WAL_DST"
[ -e "$SCENARIO_DIR/registry.sqlite-shm" ]    && cp "$SCENARIO_DIR/registry.sqlite-shm"    "$REG_SHM_DST" && ok "  copied → $REG_SHM_DST"

# Soul artifacts — the scenario stores them at data/soul_generated/, we
# unpack them to the target soul dir.
if [ -d "$SCENARIO_DIR/data/soul_generated" ]; then
  mkdir -p "$SOUL_DST"
  cp -R "$SCENARIO_DIR/data/soul_generated/"* "$SOUL_DST/" 2>/dev/null || true
  ok "  copied → $SOUL_DST/"
fi

# ---------- presenter script pointer -------------------------------------

if [ -f "scenarios/scripts/$NAME.md" ]; then
  echo ""
  ok "Presenter script: scenarios/scripts/$NAME.md"
  echo "    open scenarios/scripts/$NAME.md   # or read it inline"
fi

echo ""
if [ "$TARGET" = "demo" ]; then
  ok "Scenario '$NAME' loaded into demo/. Double-click start-demo.command."
else
  ok "Scenario '$NAME' loaded. Double-click start.command to bring the stack up."
fi
press_to_close
