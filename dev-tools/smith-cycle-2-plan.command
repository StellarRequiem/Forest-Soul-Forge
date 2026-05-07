#!/bin/bash
# Cycle 2 dispatch via the E7 cycle_dispatch.py helper.
#
# Demonstrates the new operator-side scaffolding from ADR-0056 E7
# (cycle_dispatch.py). Compare to dev-tools/smith-cycle-1-plan.command
# which built the JSON body manually with a Python heredoc — this
# version is a single CLI call.
#
# Cycle 2 target: unit tests for the cycle decision endpoint
# POST /agents/{instance_id}/cycles/{cycle_id}/decision
# (an undertested router endpoint analogous to cycle 1's target).
#
# v1 has no prior_cycle context (fresh start). If cycle 2 needs
# revision, v2 will pass --prior-response-from pointing at
# this file's output.

set -uo pipefail
cd "$(dirname "$0")/.."

INSTANCE_ID="experimenter_1de20e0840a2"

"$(pwd)/.venv/bin/python3" dev-tools/cycle_dispatch.py \
  --agent-id "$INSTANCE_ID" \
  --session-id "smith-cycle-2-plan-v1" \
  --mode work \
  --task-kind conversation \
  --max-tokens 4000 \
  --usage-cap-tokens 50000 \
  --prompt-from dev-tools/smith-cycle-2-prompt.md \
  --verbatim-from dev-tools/smith-cycle-2-verbatim.json \
  --save-response-to dev-tools/smith-cycle-2-plan-response-v1.json

echo
echo "Press any key to close."
read -n 1
