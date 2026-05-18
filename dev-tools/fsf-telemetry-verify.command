#!/usr/bin/env bash
# ADR-0064 T3 (B377) — fsf telemetry verify <batch_id>.
#
# Operator-facing wrapper around
# forest_soul_forge.security.telemetry.verify.main().
#
# Usage:
#   dev-tools/fsf-telemetry-verify.command <batch_id>
#   dev-tools/fsf-telemetry-verify.command <batch_id> --json
#
# Exit codes:
#   0  OK                    — store + chain agree
#   1  MISMATCH              — integrity_root differs (corruption)
#   2  CHAIN_ENTRY_MISSING   — store has batch, chain lacks anchor
#   3  BATCH_EMPTY           — batch_id not in store
#   4  STORE_UNAVAILABLE     — couldn't open the telemetry store
#
# Defaults:
#   --telemetry-db  data/telemetry.sqlite
#   --chain-path    examples/audit_chain.jsonl

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"

if [ "$#" -lt 1 ]; then
  echo "usage: $0 <batch_id> [--telemetry-db PATH] [--chain-path PATH] [--json]"
  exit 64
fi

PY="$REPO_ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY=python3

cd "$REPO_ROOT"
PYTHONPATH="$REPO_ROOT/src" "$PY" -m forest_soul_forge.security.telemetry.verify "$@"
