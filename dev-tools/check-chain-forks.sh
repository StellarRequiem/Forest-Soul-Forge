#!/usr/bin/env bash
# dev-tools/check-chain-forks.sh — exhaustive fork detector for the
# audit chain. Sister of the in-tree `audit_chain_verify.v1` builtin
# tool; the difference is that AuditChain.verify() short-circuits at
# the first structural break (correct for "is this chain still
# trustworthy") while scan_for_forks() walks the entire chain and
# reports every anomaly (correct for "where are all the breaches").
#
# Origin: B199 (2026-05-08). Surfaced 6 forks at chain seqs
# 3728/3735-3738/3740 in the live examples/audit_chain.jsonl —
# verify() reported only seq 3728 because it stopped at the first
# break, masking the other 5. Forensic record:
# docs/audits/2026-05-08-chain-fork-incident.md.
#
# Usage:
#   bash dev-tools/check-chain-forks.sh                   # default chain
#   bash dev-tools/check-chain-forks.sh path/to/other.jsonl
#
# Exits 0 if the chain is clean (ok=True), 1 if any duplicate seqs
# or hash mismatches are found. Pipe through ``jq`` for machine
# parseable output if you want to feed it into CI.
set -uo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

CHAIN_PATH="${1:-examples/audit_chain.jsonl}"

if [ ! -f "$CHAIN_PATH" ]; then
  echo "ERROR: chain file not found at $CHAIN_PATH" >&2
  exit 2
fi

PYTHONPATH=src python3 - "$CHAIN_PATH" <<'PYEOF'
import sys
from forest_soul_forge.core.audit_chain import AuditChain

chain_path = sys.argv[1]
chain = AuditChain(chain_path)
result = chain.scan_for_forks()

print(f"chain:            {chain_path}")
print(f"entries_scanned:  {result.entries_scanned}")
print(f"ok:               {result.ok}")
print(f"duplicate_seqs:   {list(result.duplicate_seqs)}")
print(f"hash_mismatches:  {list(result.hash_mismatches)}")
print(f"unknown_events:   {len(result.unknown_event_types)}")
if result.unknown_event_types:
    for et in result.unknown_event_types:
        print(f"  - {et}")

sys.exit(0 if result.ok else 1)
PYEOF
