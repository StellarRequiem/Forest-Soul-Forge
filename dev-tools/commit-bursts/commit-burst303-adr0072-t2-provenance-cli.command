#!/bin/bash
# Burst 303 - ADR-0072 T2: fsf provenance CLI.
#
# Operator surface on the B290 behavior-provenance substrate.
# Read-only inspection - writes (preference edits, learned-rule
# pruning) land in T3-T5.
#
# What ships:
#
# 1. src/forest_soul_forge/cli/provenance_cmd.py:
#    Three subcommands:
#      - fsf provenance precedence
#          Print the four-layer precedence table (descending).
#          --json emits {layer: weight}.
#      - fsf provenance resolve <layer_a> <layer_b>
#          Print which of two layers wins under ADR-0072 D1.
#          Bad input -> rc=2 + stderr error.
#      - fsf provenance list
#          List loaded preferences + learned_rules. --json emits
#          structured form; default is formatted text with one
#          block per rule.
#          --preferences-path / --learned-rules-path override
#          paths for test fixtures.
#
# 2. src/forest_soul_forge/cli/main.py:
#    Register `fsf provenance ...` at top level.
#
# Tests (test_cli_provenance.py - 10 cases):
#   precedence:
#     - text output has all four layers in descending order
#     - --json output matches the canonical weights
#   resolve:
#     - preference beats learned
#     - constitutional beats preference
#     - hardcoded_handoff beats every other layer
#     - unknown layer name -> rc=2 + stderr message
#   list:
#     - empty files render (none) markers
#     - loaded preference renders id + statement
#     - --json output round-trips dataclass fields
#       (including the pending_activation / active bucket split
#        from B290's LearnedRulesConfig)
#
# Read-only across the board. No mutations to preferences.yaml
# or learned_rules.yaml.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/cli/provenance_cmd.py \
        src/forest_soul_forge/cli/main.py \
        tests/unit/test_cli_provenance.py \
        dev-tools/commit-bursts/commit-burst303-adr0072-t2-provenance-cli.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(provenance): ADR-0072 T2 - fsf provenance CLI (B303)

Burst 303. Operator surface on the B290 behavior-provenance
substrate. Three read-only subcommands: precedence (print the
four-layer ordering table), resolve (which of two layers wins
under ADR-0072 D1), list (loaded preferences + learned_rules).
Writes (preference edits, learned-rule pruning) land in T3-T5.

What ships:

  - cli/provenance_cmd.py: precedence prints the
    hardcoded_handoff/constitutional/preference/learned weights
    in descending order (--json emits the same shape as a JSON
    map). resolve consults resolve_precedence from B290 and
    prints winner + weights of both inputs; bad input returns
    rc=2 + stderr 'unknown layer X; valid: [...]'. list loads
    preferences.yaml and learned_rules.yaml via the B290
    load_*() helpers (so the same validation runs), prints
    preference + pending/active rule blocks. --json emits the
    structured form including the pending_activation / active
    bucket split from LearnedRulesConfig. --preferences-path
    and --learned-rules-path overrides support test fixtures.

  - cli/main.py: registers fsf provenance ... at top level.

Tests: test_cli_provenance.py - 10 cases covering both output
modes of precedence (descending text + canonical-weights JSON),
all three correct-winner cases of resolve plus the rc=2 bad-
input case, and list output across empty files / loaded
preferences / pending + active rules in JSON mode.

Sandbox-verified smoke: precedence text + JSON, resolve
preference<->learned, resolve bogus input rc=2 with stderr.

Queued T3-T5: Reality Anchor cron over pending learned rules
(verify-then-activate flow), orchestrator integration that
reads the resolved precedence at routing time, frontend
provenance pane."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 303 complete - ADR-0072 T2 provenance CLI shipped ==="
echo ""
echo "Press any key to close."
read -n 1
