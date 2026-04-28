#!/usr/bin/env bash
# Forest Soul Forge — reset to a clean data state.
#
# DESTRUCTIVE: archives every piece of generated state so the next launch
# behaves like a fresh checkout. Specifically:
#   - audit_chain.jsonl
#   - registry.sqlite (and any -wal / -shm sidecars)
#   - data/soul_generated/*  (every birthed agent's soul.md + constitution.yaml)
#   - data/forge/skills/installed/*  (runtime-installed skill manifests)
#   - data/plugins/*  (operator-installed .fsf packages)
#
# Nothing is hard-deleted. Each path is renamed with a .bak.<timestamp>
# suffix so you can recover by renaming back. The .bak files accumulate;
# clean them out manually when you're sure you don't need them.
#
# Use this when:
#   - rehearsing a demo and want to start clean each time
#   - the audit chain got into an inconsistent state during testing
#   - moving between branches that birthed agents with different schemas
#   - a fresh evaluator wants to forge their own first agent without the
#     existing agents cluttering the Agents tab
#
# This script does NOT touch:
#   - the .venv (use `rm -rf .venv` for that, then re-run start.command)
#   - source code or git state
#   - examples/  (those ship with the repo)
#   - .run/  (just transient logs — re-created on next start)
#
# After this, double-click start.command to bring the stack up clean.

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

BLUE="\033[1;34m"
GREEN="\033[1;32m"
YELLOW="\033[1;33m"
RED="\033[1;31m"
DIM="\033[2m"
RESET="\033[0m"

say()  { printf "${BLUE}[reset]${RESET} %s\n" "$*"; }
ok()   { printf "${GREEN}[reset]${RESET} %s\n" "$*"; }
warn() { printf "${YELLOW}[reset]${RESET} %s\n" "$*"; }
err()  { printf "${RED}[reset]${RESET} %s\n" "$*" 1>&2; }

press_to_close() {
  echo ""
  echo "Press return to close this window."
  read -r _
}

# ---------- confirm ------------------------------------------------------

cat <<EOF

${RED}Forest Soul Forge — RESET${RESET}

This will archive (NOT delete) every piece of generated state under
this checkout, so the next launch behaves like a clean install.

Files that will be archived (renamed with .bak.<timestamp>):
  - audit_chain.jsonl              (canonical event log)
  - registry.sqlite + sidecars     (derived index)
  - data/audit_chain.jsonl
  - data/registry.sqlite + sidecars
  - data/soul_generated/           (every birthed agent's artifacts)
  - data/forge/skills/installed/   (runtime-installed skills)
  - data/plugins/                  (operator-installed .fsf packages)

Untouched: source code, .venv, examples/, .run/ logs.

If a daemon is currently running, you should stop.command it first.

EOF

read -r -p "Type 'reset' to confirm, anything else to cancel: " confirm
if [ "$confirm" != "reset" ]; then
  warn "Cancelled. No files moved."
  press_to_close
  exit 0
fi

# ---------- archive ------------------------------------------------------

STAMP="$(date +%Y%m%d-%H%M%S)"
say "Archive timestamp: $STAMP"

archive_one() {
  local target="$1"
  if [ -e "$target" ]; then
    local backup="${target}.bak.${STAMP}"
    mv "$target" "$backup"
    ok "Archived ${target} → ${backup}"
  else
    ok "Skipped ${target} (doesn't exist)."
  fi
}

# Top-level (the daemon's default registry path lives here)
archive_one "audit_chain.jsonl"
archive_one "registry.sqlite"
archive_one "registry.sqlite-wal"
archive_one "registry.sqlite-shm"

# data/ subdirectory (canonical per ADR-0006 / STATE.md)
archive_one "data/audit_chain.jsonl"
archive_one "data/registry.sqlite"
archive_one "data/registry.sqlite-wal"
archive_one "data/registry.sqlite-shm"
archive_one "data/soul_generated"
archive_one "data/forge/skills/installed"
archive_one "data/plugins"

# Re-create the empty data/ scaffolding so the next run doesn't hit
# missing-directory errors.
mkdir -p data/soul_generated data/forge/skills/installed data/plugins
ok "Re-created empty data/ scaffolding."

echo ""
ok "Reset complete. Double-click start.command to bring the stack up clean."
press_to_close
