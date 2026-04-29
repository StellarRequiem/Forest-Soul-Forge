#!/usr/bin/env bash
# Forest Soul Forge — bring the stack up against the isolated demo/ dir.
#
# Same as start.command, but points the daemon at demo/ instead of the
# top-level registry.sqlite + audit_chain.jsonl. Use this when you want
# to demo without touching your real state — every birth, archive,
# audit chain entry lands under demo/, leaving production data alone.
#
# Workflow:
#   ./scenarios/load-scenario.command synthetic-incident demo
#   ./start-demo.command
#   ...drive the demo, birth agents, run skills, etc...
#   Ctrl-C the start-demo terminal when done
#   ./start.command       # back to your real state, demo/ untouched
#
# To wipe demo/ between rehearsals: ./reset.command (archives everything,
# including demo/) — or just rm -rf demo/audit_chain.jsonl etc.

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

GREEN="\033[1;32m"
BLUE="\033[1;34m"
RESET="\033[0m"

ok() { printf "${GREEN}[start-demo]${RESET} %s\n" "$*"; }
say() { printf "${BLUE}[start-demo]${RESET} %s\n" "$*"; }

# Make sure the demo/ scaffolding exists so the daemon doesn't error out
# on missing parent dirs the first time it tries to write.
mkdir -p demo/soul_generated demo/forge/skills/installed demo/plugins

say "Pointing daemon at demo/ (env vars override defaults):"
say "  FSF_REGISTRY_DB_PATH=demo/registry.sqlite"
say "  FSF_AUDIT_CHAIN_PATH=demo/audit_chain.jsonl"
say "  FSF_SOUL_OUTPUT_DIR=demo/soul_generated"
say "  FSF_SKILL_INSTALL_DIR=demo/forge/skills/installed"
say "  FSF_PLUGINS_DIR=demo/plugins"
ok "Production state at top-level audit_chain.jsonl + registry.sqlite is untouched."
echo ""

# Export then exec into start.command so it inherits the environment +
# all of start.command's bootstrap logic (Python check, venv, pip install,
# port cleanup, log tail, Ctrl-C cleanup) is reused as-is.
export FSF_REGISTRY_DB_PATH="demo/registry.sqlite"
export FSF_AUDIT_CHAIN_PATH="demo/audit_chain.jsonl"
export FSF_SOUL_OUTPUT_DIR="demo/soul_generated"
export FSF_SKILL_INSTALL_DIR="demo/forge/skills/installed"
export FSF_PLUGINS_DIR="demo/plugins"

exec ./start.command
